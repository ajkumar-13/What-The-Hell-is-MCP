# 14 · Project 1 · Writes, transactions, and an audit trail

> **TL;DR.** A write is defensible when a human has read the exact statement that will run,
> when the change and the record of the change commit together, and when the record lands
> somewhere the model cannot reach. This post adds `INSERT`, `UPDATE`, and `DELETE` to the
> Model Context Protocol (MCP) server from [Post 13](../13-database-analyst/index.md), using
> a second PostgreSQL role, a dry run, an approval parameter the model cannot see, and an
> audit row written inside the mutation's own transaction. Every result shape, question
> text, and call trace below was produced by running
> [code/13-postgres-analyst/](../../code/13-postgres-analyst/).
>
> **After reading this you will be able to:**
> - Put a human confirmation on the critical path of a tool call, in a parameter the model cannot forge.
> - Keep an audit row atomic with the change it describes, and record the failures too.
> - Choose the resolver return type that preserves all three elicitation outcomes instead of collapsing them.
> - Recognize the pool and fallback mistakes that quietly turn a write boundary back into nothing.

![The write path drawn as a single line with no way around it. A tool call arrives and passes four gates in order: a deployment switch, the SQL validator, a human confirmation rendered from the validated statement, and finally one transaction containing both the mutation and its audit row. A dry run branches off before the human is asked. Declining, dismissing, or answering no all leave by the same exit, and a failed statement rolls back and then writes a second audit row on a fresh connection.](diagrams/01-the-write-path.svg)
*The human gate is not beside the path. It is on it, and there is no argument the model can set to skip it.*

---

## 1. Why a write is categorically different

[Post 13](../13-database-analyst/index.md) argued that a read is safe when the database role,
not the parser, decides what "read" means. A write does not have an equivalent. There is no
grant that makes `UPDATE users SET tier = 'free' WHERE created_at < now()` safe, because
that statement is exactly what the write role is for.

The difference is not that a write is riskier. It is that it is irreversible by a system with
no memory of the previous value. A wrong `SELECT` costs a wasted turn. A wrong `UPDATE`
costs whatever was in the column.

So the write path adds four things the read path does not have, and each one is a separate
gate rather than a check inside a bigger function:

1. **A deployment switch.** `ENABLE_WRITES` is off by default. A server that was never meant
   to write cannot be talked into it.
2. **A separate role.** Writes go through `DATABASE_WRITE_URL`, a second login with its own
   grants, on its own pool. The read path physically cannot reach it.
3. **A human.** The exact statement is shown to a person, and it runs only if they say yes.
4. **One transaction, with the audit row inside it.** The change and the record of the change
   commit together or neither does.

Everything below is the detail of getting those four right, and most of it is detail that
the first edition of this project got wrong.

## 2. Three connection accessors, not one

Here is the smallest change in the project with the largest consequence.
[database.py](../../code/13-postgres-analyst/src/pg_analyst/database.py) exposes three ways
to get a connection, and picking the wrong one is a bug that presents as something else
entirely:

```python
@asynccontextmanager
async def read_connection(self):    ...   # read pool, no transaction
@asynccontextmanager
async def write_connection(self):   ...   # write pool, no transaction
@asynccontextmanager
async def write_transaction(self):  ...   # write pool, inside BEGIN / COMMIT
```

Two callers need the middle one, and both of them are easy to get wrong.

**Creating the audit table.** `ensure_audit_table()` issues `CREATE TABLE IF NOT EXISTS`. The
first edition ran it through the read pool, because the read pool was the only pool the
project had when the function was written and the call site never got revisited. Against a
casual development database with one superuser login, that works. Against a correctly
hardened read-only role it fails with `cannot execute CREATE TABLE in a read-only
transaction`, and because the call is inside the lifespan, the exception propagates and the
server never starts. The report you get is "your server dies on launch", which points at
everything except a `CREATE TABLE`.

**Recording a failed write.** The audit insert for a *successful* write goes on the caller's
transaction. The insert for a *failed* one cannot: by the time you know the write failed, the
transaction has rolled back and taken any audit row written inside it with it. So the failure
path takes a fresh connection, and it must come from the write pool. The first edition took
it from the read pool, so the failure insert failed too, silently, and the entries you most
want in an audit log were the only ones never written.

Both mistakes have the same shape. A function that reaches for "a connection" without saying
which pool works fine on a machine where both roles are the same login, and only breaks on
the machine that took the security advice.

**And there is no fallback.** The first edition had this line:

```python
write_url = os.getenv("DATABASE_WRITE_URL", os.getenv("DATABASE_URL"))
```

It is gone, deliberately. On any machine where only `DATABASE_URL` was set, that line made
the "write pool" the read-only role, and the first write then failed at the database with a
confusing permissions error instead of at startup with a clear one. Worse, it made the
two-role design a suggestion: nothing in the running system tells you which of the two
configurations you actually have. **A fallback that quietly degrades a security boundary is
worse than a hard failure**, because a hard failure is a bug report and a quiet degradation
is a false sense of a control. Now `write_url` returns `None`, no write pool opens, and the
tool says so.

The roles themselves are in
[sql/002-readonly-role.sql](../../code/13-postgres-analyst/sql/002-readonly-role.sql).
`mcp_writer` holds `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on the seeded tables, plus
`CREATE` on the schema so it can build the audit table at startup. It does not own the tables,
so it cannot `DROP` or `ALTER` them; it does not hold `TRUNCATE`; and it is not a superuser,
so the blocklisted file-system functions fail regardless of the blocklist. The file names one
more grant worth adding on a real system, and it is worth repeating here: after
`mcp_audit_log` exists, revoke `UPDATE` and `DELETE` on it from `mcp_writer` specifically. A
compromised write path should be able to add to the record and not edit it.

## 3. Validate first, then dry run, then ask

The write validator is the read validator's sibling in
[security.py](../../code/13-postgres-analyst/src/pg_analyst/security.py), and it is
deliberately narrower. `INSERT`, `UPDATE`, `DELETE`, and nothing else:

```
'TRUNCATE TABLE users'
  -> Only INSERT, UPDATE, and DELETE are allowed here, and this is TRUNCATE.
     Schema changes are not this server's job.

'DELETE FROM users'
  -> DELETE without a WHERE clause would touch every row in the table.
     Add a WHERE clause naming the rows you mean.
```

No data definition language, no `TRUNCATE`, no `COPY`, no `GRANT`, no `MERGE`. A model with a
shell into your schema is a different product from a model that can correct a row, and this
is the second one.

The `WHERE` rule is a floor and not a guarantee, and the test suite says so out loud:
`UPDATE users SET active = false WHERE true` satisfies it. That is fine, because the rule is
aimed at the fat-fingered statement with no predicate at all. What bounds the rest is the
human in section 4.

Before anyone is asked anything there is a dry run, which is the cheapest checkpoint in the
whole design:

```json
{
  "status": "dry_run",
  "operation": "UPDATE",
  "table": "users",
  "executed_sql": "UPDATE users SET active = FALSE WHERE id = 3",
  "affected_rows": 0,
  "audit_id": 0,
  "detail": "Valid. Nothing was executed and nobody was asked to confirm. Call again with dry_run=False to run it for real."
}
```

That is a real result, from calling the tool with
`{"sql": "update users set active=false where id = 3", "dry_run": true}`. Note that
`executed_sql` is already normalized: uppercase keywords, `FALSE` rather than `false`. It is
the regenerated statement from the parse tree, which is the same string that will appear in
the confirmation prompt and in the audit row.

The dry run asks nobody anything, and that is a design decision rather than an oversight. So
is refusing an invalid statement without asking. From a real run, with `ENABLE_WRITES=true`:

```
invalid          -> {"status": "rejected", ...,
                     "detail": "Refused by the SQL validator: DELETE without a WHERE
                                clause would touch every row in the table. ..."}
questions asked  -> 0

dry run          -> {"status": "dry_run", ...}
questions asked  -> 0
```

**Asking a human to approve something the server has already decided to reject teaches them
to click through prompts without reading them.** Consent is a limited resource. Spend it only
where the answer changes what happens.

## 4. Confirming over MRTR, and the resolver signature

Revision 2026-07-28 removed the server-to-client request channel. A server cannot call the
client and wait. It answers the request with a partial result, the client collects the answer,
and the client calls the same tool again. That pattern is Multi Round-Trip Requests (MRTR),
and [Post 08](../08-elicitation-and-mrtr/index.md) walks it message by message.

Here is what this server actually puts on the wire, captured from the first leg of a real
call:

```json
{
  "resultType": "input_required",
  "inputRequests": {
    "pg_analyst.writes:_confirm_write": {
      "method": "elicitation/create",
      "params": {
        "mode": "form",
        "message": "Commit this DELETE against orders?\n\nDELETE FROM orders WHERE id = 4242\n\nIt runs inside a single transaction and is recorded in the audit log.",
        "requestedSchema": {
          "type": "object",
          "properties": {
            "confirm": { "type": "boolean", "title": "Confirm",
                         "description": "Confirm that this statement should be committed." },
            "reason":  { "type": "string", "title": "Reason", "default": "",
                         "description": "Optional note recorded alongside the change in the audit log." }
          },
          "required": ["confirm"]
        }
      }
    }
  },
  "requestState": "v1.7S5r-RbTPT4RH1Gon3-IF6F_RAmdA0LMDFod04p8Lfu3ykWs4p8fIRCgZ5QA..."
}
```

The original request is now finished. The key `pg_analyst.writes:_confirm_write` is derived
from the resolver's `module:qualname`, which is stable across workers and is what makes this
work behind a load balancer. The `requestState` is sealed by the software development kit
(SDK); by default under a process-local ephemeral key, which is correct for stdio and wrong
for more than one worker.

**The parameter the model cannot see.** In the source this is a resolver plus one annotated
parameter, in [writes.py](../../code/13-postgres-analyst/src/pg_analyst/writes.py):

```python
@mcp.tool(title="Write to the database", annotations=ToolAnnotations(destructive_hint=True, ...))
async def write_database(
    sql: str,
    approval: Annotated[ElicitationResult[WriteApproval], Resolve(_confirm_write)],
    dry_run: bool = False,
) -> WriteResult:
```

The SDK strips every `Resolve`-backed parameter from the published input schema. Read back
through an in-memory client, the tool's input properties are:

```
tool write_database
  input properties : ['sql', 'dry_run']
  required         : ['sql']
  annotations      : readOnly=False destructive=True idempotent=False
```

`approval` is absent, so there is no field in which a model can supply its own approval. The
suite asserts the whole set rather than the absence of one name, because
`assert "approval" not in props` would pass forever while the next resolved parameter leaked.

**Now the part that took the longest to get right.** The resolver's return type is a union:

```python
def _confirm_write(
    sql: str, dry_run: bool
) -> Elicit[WriteApproval] | ElicitationResult[WriteApproval]:
    if not writes_enabled() or dry_run:
        return DeclinedElicitation()
    try:
        plan = validate_write_statement(sql)
    except SecurityError:
        return DeclinedElicitation()
    return Elicit(_confirmation_question(plan), WriteApproval)
```

The resolver takes `sql` and `dry_run` by name, matched against the tool's own arguments, and
the SDK fills in defaults the caller omitted, so `dry_run` is always a real boolean. Three
conditions return a stand-in instead of a question, and nothing goes on the wire: writes are
off, this is a dry run, or the statement is going to be refused anyway.

The parameter annotation is the load-bearing choice, and the three plausible spellings behave
completely differently. All three were measured against `mcp==2.0.0b2`:

| Annotation | Registration | On accept | On decline or cancel |
|---|---|---|---|
| `Annotated[ElicitationResult[T], Resolve(f)]` | fine | `AcceptedElicitation` | `DeclinedElicitation`, `CancelledElicitation` |
| `Annotated[ElicitationResult[T] \| None, Resolve(f)]` | fine | unwrapped to bare `T` | `isError: true`, `Resolver for parameter 'ap' could not resolve: elicitation was decline` |
| `Annotated[ElicitationResult[T], Resolve(f)] \| None` | `InvalidSignature` | | |

Only the first row preserves the three-way branch. The second looks harmless, registers
without complaint, and quietly converts a user saying no into a tool error, which is the
wrong shape: a person declining is not a failure of the tool. The third fails loudly at
registration with a message that tells you the fix:

```
InvalidSignature: Parameter 'ap' of 'probe' wraps `Resolve(...)` in a union;
annotate the parameter directly as `Annotated[T, Resolve(...)]`
```

**One measured caveat on the stand-in.** When a resolver returns a `DeclinedElicitation()`
rather than an `Elicit`, this SDK build treats it as an already-resolved value and hands the
tool body an `AcceptedElicitation` whose `.data` is that `DeclinedElicitation`. So
`isinstance(approval, DeclinedElicitation)` is `False` for the stand-in, and a tool that
relied on it would misread a synthetic skip as a real refusal. `write_database` never gets
there: all three no-ask conditions are handled by gates 1 and 2 and by the dry-run return,
before the body ever inspects `approval`. Worth knowing if you copy this pattern, and worth
re-checking when stable 2.0 ships.

The three real outcomes all land as a plain `"complete"` result with a distinct `detail`, and
none of them is an error:

```
decline   -> {"status": "declined", ..., "detail": "The user declined to confirm this write."}
cancel    -> {"status": "declined", ..., "detail": "The confirmation prompt was dismissed."}
accept, confirm=false
          -> {"status": "declined", ..., "detail": "The user answered no to the confirmation."}
```

The third row is the one worth pausing on, and it is the same trap
[Post 08](../08-elicitation-and-mrtr/index.md) names. `action: "accept"` means the form came
back, not that the user said yes. A confirmation checkbox left unchecked arrives as an accept
carrying `confirm: false`.

**The question is a pure function of the SQL.** This is not style; it is the difference
between a call that converges and one that does not. The SDK matches a recorded answer
against a SHA-256 digest of the exact rendered question, where SHA-256 is the 256-bit Secure
Hash Algorithm. Put a timestamp or a live row count in the message and the retry renders a
different string, the recorded answer never matches, and the tool loops until
`input_required_max_rounds` gives up. From the outside that looks like the user's answer being
ignored.

```python
def _confirmation_question(plan: WritePlan) -> str:
    return (
        f"Commit this {plan.operation} against {plan.table}?\n\n"
        f"{plan.sql}\n\n"
        "It runs inside a single transaction and is recorded in the audit log."
    )
```

Every character comes from the validated plan, which comes from `sql`. Two tests pin the
property. One counts the questions in a single call and asserts exactly one. The other calls
the tool three times with the same argument and asserts one distinct question text:

```
three calls, distinct question texts: 1 of 3
```

**Comments are stripped on the way in.** `plan.sql` comes from
`statement.sql(dialect="postgres", comments=False)`, so a statement submitted as

```sql
update  users   set active = false where id = 3 /* and everything else */
```

reaches the prompt as `UPDATE users SET active = FALSE WHERE id = 3`. A comment is inert to
PostgreSQL and is therefore a perfect place to hide text from somebody skim-reading a
confirmation dialog. Dropping it costs nothing and closes the gap between what a human read
and what runs. It also means the prompt string, the audit row, and the statement PostgreSQL
executes are one string, because all three come from `plan.sql`.

One last thing, from the SDK research and confirmed here: a client with no
`elicitation_callback` declares no elicitation capability, so the call fails before the
question is ever composed:

```
MCPError: Client did not declare the form elicitation capability required by
resolver 'pg_analyst.writes:_confirm_write'
```

with code `-32021` (`MissingRequiredClientCapability`).

## 5. One transaction, with the audit row inside it

Gate 4 is eight lines and the shape is the whole argument:

```python
async with db.write_transaction() as conn:
    command_tag = await conn.execute(plan.sql)
    touched = affected_rows(command_tag)
    audit_id = await record_operation(
        operation=plan.operation, target_table=plan.table, sql_text=plan.sql,
        affected_rows=touched, success=True, note=note, conn=conn,
    )
```

`conn=conn` is the entire point. `record_operation` inserts on the caller's connection, so
the audit row is inside the mutation's transaction. Had the audit insert raised, the write
above it would have rolled back with it. There is no state in which the change happened and
the log does not say so.

`affected_rows` comes from PostgreSQL's own command tag, not from a count the server
estimated. The tags are `INSERT 0 3`, `UPDATE 5`, and `DELETE 2`, and the count is always the
last field.

![Two call traces side by side, both recorded from the write tool. The success trace has five steps: acquire a write-pool connection, BEGIN, execute the statement, insert the audit row on the same connection, COMMIT. The failure trace has six: acquire, BEGIN, execute, ROLLBACK, then a second acquire from the write pool and a second audit insert, because the first transaction is gone and anything written inside it went with it. A note marks that the second acquire must come from the write pool and that using the read pool here is why the first edition never recorded a failure.](diagrams/02-transaction-and-audit.svg)
*Success is one transaction. Failure is a transaction that no longer exists plus a second, separate write.*

Those two traces are recorded, not drawn from imagination. Docker was not running on the
machine that produced them, so a recording stub stood in for the `asyncpg` pool and wrote down
every acquire, `BEGIN`, `execute`, `fetchval`, `COMMIT`, and `ROLLBACK` in order. The tool
code is unmodified. Success:

```
1. acquire: write pool
2. BEGIN
3. execute("DELETE FROM orders WHERE status = 'cancelled'")
4. fetchval(audit insert, success=True)
5. COMMIT
```
```json
{"status": "committed", "operation": "DELETE", "table": "orders",
 "executed_sql": "DELETE FROM orders WHERE status = 'cancelled'",
 "affected_rows": 2, "audit_id": 17,
 "detail": "Committed. 2 row(s) changed, recorded in the audit log as entry 17."}
```

Failure, with the statement raising a `PostgresError`:

```
1. acquire: write pool
2. BEGIN
3. execute("DELETE FROM orders WHERE status = 'cancelled'")
4. ROLLBACK
5. acquire: write pool
6. fetchval(audit insert, success=False)
```
```json
{"status": "failed", "operation": "DELETE", "table": "orders",
 "executed_sql": "DELETE FROM orders WHERE status = 'cancelled'",
 "affected_rows": 0, "audit_id": 17,
 "detail": "Rolled back. PostgreSQL said: permission denied for table orders"}
```

Steps 5 and 6 are the ones to internalize. **The failure audit has to be a second, separate
write**, on a connection acquired after the rollback, because the transaction that would have
carried it no longer exists. That is why `write_connection()` exists as its own accessor, and
it is why pointing it at the read pool produced a system that recorded every success and no
failures at all.

`_record_failure` also swallows its own exceptions on purpose:

```python
except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
    log.error("could not record the failed write in the audit log: %s", exc)
    return 0
```

Losing an audit row is bad. Losing the caller's original error message behind a second
exception raised while trying to record the first is worse, because then nobody can debug
either failure.

## 6. The audit record, field by field

```sql
CREATE TABLE IF NOT EXISTS mcp_audit_log (
    id            BIGSERIAL PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    operation     TEXT        NOT NULL,
    target_table  TEXT,
    sql_text      TEXT        NOT NULL,
    affected_rows INTEGER,
    success       BOOLEAN     NOT NULL,
    error_message TEXT,
    note          TEXT,
    session_id    TEXT        NOT NULL DEFAULT 'unknown',
    actor         TEXT        NOT NULL DEFAULT 'unknown'
)
```

![One audit row exploded into eleven fields, each labeled with where its value comes from. Four come from the database itself: the serial identifier, the timestamp from now, the affected row count taken from PostgreSQL's own command tag, and the error message. Three come from the parse tree: the operation name, the target table, and the regenerated statement text with comments stripped. One comes from the human who approved the write, namely the optional note they typed. Two come from the operator's environment. One comes from the server's own control flow, the success flag. A summary marks that the only field carrying text the model authored is the statement, and that it is the exact string a human read before approving it.](diagrams/03-the-audit-record.svg)
*Eleven fields. Exactly one of them carries text the model wrote, and a person read that one before it ran.*

That provenance is the property worth designing for. Walking the fields:

| Field | Where the value comes from |
|---|---|
| `id`, `occurred_at` | PostgreSQL: `BIGSERIAL` and `now()` |
| `operation`, `target_table` | the parse tree, via `WritePlan`, not from any string the model chose |
| `sql_text` | `plan.sql`, regenerated with `comments=False`, the exact string the human read |
| `affected_rows` | PostgreSQL's command tag |
| `success` | the server's own control flow |
| `error_message` | PostgreSQL's message, on the failure path |
| `note` | typed by the human in the confirmation form |
| `session_id`, `actor` | the operator's environment |

The model contributes the semantics of one field and the literal text of none of them. That
is not an accident of implementation; it is the reason to derive `operation` and
`target_table` from the tree rather than from a keyword match on the submitted string.

Two smaller decisions in [audit.py](../../code/13-postgres-analyst/src/pg_analyst/audit.py)
are worth borrowing.

**The identity variables are not named `MCP_*`.** The SDK's `Settings` object reads the whole
`MCP_` prefix out of the environment, and unrelated keys under that prefix invite a collision
that presents as a validation error at server construction. They are `AUDIT_SESSION_ID` and
`AUDIT_ACTOR`.

**Neither is an authenticated identity, and the code says so.** Under stdio there is nobody to
authenticate: the host launched this process. Recording `unknown` honestly is better than
recording a value that looks like an identity and is not. [Post 20](../20-authorization/index.md)
covers what to record when there is a real one.

## 7. The trail as a resource, not a tool

The audit log is published at `postgres://audit`, as Markdown, through the **read** pool.

A resource rather than a tool, for the same reason the schema was one in
[Post 13](../13-database-analyst/index.md). Resources are application-driven: a human pins
this into a conversation to check the assistant's work. It is not an action the model takes to
decide what to do next, and giving the model a tool that reads its own audit trail is a small
but real step toward a system that reasons about its own oversight.

Content that reaches a Markdown cell is escaped, and here that matters more than it did for
table names:

```python
def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
```

Recorded SQL is attacker-influenced text. Whoever can talk to the model can get a statement
recorded in this table, and an unescaped pipe would let them forge extra rows in the very
document a human is reading to audit them. Newlines are flattened for the same reason.

The resource also handles the case where the table does not exist yet, which happens on every
read-only deployment:

```python
except asyncpg.UndefinedTableError:
    return ("Audit trail unavailable: the audit table does not exist yet. It "
            "is created at startup when ENABLE_WRITES is true.")
```

## 8. Running it, and what still goes wrong

```bash
cd code/13-postgres-analyst && PYTHONPATH=src pytest tests -q
```
```
........................................................................ [ 74%]
.............ssssssssssss                                                [100%]
85 passed, 12 skipped in 1.71s
```

Almost the entire write path is covered with no database at all, because every gate before
the transaction is pure. Writes disabled, nobody asked, invalid statement rejected without
asking, dry run, decline, cancel, accept-but-no, approval hidden from the schema, one question
per call, and identical questions across three calls: none of those need PostgreSQL. The
twelve skips are the live-database tests, which run once the container in
`docker-compose.yml` is up.

One of the tests deserves calling out because it covers a state that only happens on a
misconfigured machine, which is exactly the state that gets shipped:

```python
async def test_an_approved_write_with_no_write_pool_fails_cleanly(monkeypatch):
    """ENABLE_WRITES on, DATABASE_WRITE_URL missing. The user said yes and
    there is nowhere to send it, which must be a clear result rather than a
    traceback."""
```

It produces:

```json
{"status": "failed", "operation": "DELETE", "table": "orders",
 "executed_sql": "DELETE FROM orders WHERE id = 1", "affected_rows": 0, "audit_id": 0,
 "detail": "DATABASE_WRITE_URL is not set, so this server has no write pool. Nothing was executed."}
```

**Now the honest section.** Four things this design does not solve.

**Approval fatigue is the real limit.** A person asked to confirm one write a week reads it.
A person asked to confirm forty in an afternoon clicks yes. Elicitation is a consent mechanism
only when the question is rare, specific, and expected, and none of the code above can make
that true. If your workflow generates a steady stream of writes, the answer is a narrower tool
with no free-text SQL in it, not a better dialog.

**A human approving a statement is not a human understanding it.** `UPDATE users SET tier =
'free' WHERE created_at < now() - INTERVAL '1 year'` is easy to read and hard to evaluate.
Nobody knows from the text how many rows that is. Showing a count would help and would break
question determinism, since the count changes between rounds, so it would have to be gathered
in an earlier round and carried in `requestState`. That is a real design, and this project
does not implement it.

**`WHERE true` satisfies the `WHERE` rule.** Said in section 3 and repeated here because it is
the gap most likely to bite: the parser bounds the shape of a statement, never its blast
radius.

**And the specification is genuinely quiet on one point.** It says what a server should *do*
with an accept, a decline, and a cancel. It does not say what the server must *return*. A
`"complete"` result with `isError: true`, a `"complete"` result describing the abandonment
without `isError`, and another `InputRequiredResult` re-asking are all permitted by the text.
This server picks the middle one, on the grounds that a user declining is not a tool failure.
Pick one and be consistent; that is a design choice the revision leaves open rather than a
rule.

---

## Common pitfalls

- **Falling back from the write URL to the read URL.** One `os.getenv(a, os.getenv(b))` turns
  the write pool into the read-only role on every machine that only configured one URL, and
  the two-role design silently becomes a comment. Let the pool be absent and say so.
- **Reaching for "a connection" instead of naming the pool.** Creating the audit table on the
  read pool means a correctly hardened server cannot start, and writing the failure audit on
  the read pool means failures are the only entries you never record. Both work fine on a
  laptop where the two roles are the same login.
- **Writing the failure audit on the rolled-back connection.** It disappears with the
  transaction. The failure path has to acquire again, from the write pool, after the rollback.
- **Annotating the approval as `ElicitationResult[T] | None`.** It registers without
  complaint, unwraps to the bare model on accept, and turns decline and cancel into
  `isError: true`. Use `Annotated[ElicitationResult[T], Resolve(f)]` and branch on all three.
- **Treating `action: "accept"` as approval.** Accept means the form came back. The checkbox
  inside it can still be false, and a tool that skips that branch commits a write its owner
  explicitly declined.
- **Letting the confirmation question vary between rounds.** A timestamp or a live row count
  changes the digest, the recorded answer never matches, and the call loops to the round limit
  while looking like an ignored answer. Derive every character from the tool's arguments.
- **Leaving comments in the statement you show the human.** Regenerate with `comments=False`
  so the prompt, the audit row, and the executed statement are one string with nowhere to hide
  text.
- **Asking for approval on something already doomed.** A prompt the user cannot meaningfully
  refuse is training for the prompt that matters.

---

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The four steps, and why
  the server returns its question instead of sending one.
  <https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr>
- Specification, *"Elicitation"*, revision 2026-07-28. Form mode, the three actions, and the
  rule that a decline is not an error channel.
  <https://modelcontextprotocol.io/specification/draft/client/elicitation>
- PostgreSQL, *"Database roles and privileges"*. The grants behind `mcp_writer`, and default
  privileges for tables created later.
  <https://www.postgresql.org/docs/current/user-manag.html>
- `asyncpg`, transactions and connection pools.
  <https://magicstack.github.io/asyncpg/current/>
- MCP Python SDK, `mcp==2.0.0b2`. Every result shape, resolver behavior, and error message
  quoted here came from this version driving
  [code/13-postgres-analyst/](../../code/13-postgres-analyst/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 15 — Project 2 · A DevOps first responder](../15-devops-responder/index.md)**: the
  same read-only-by-construction argument applied to a Kubernetes cluster, where the read side
  is harder and the objects are larger.
- **[Post 13 — Project 1 · A secure database analyst](../13-database-analyst/index.md)**: the
  read path this post extends, and the case for the database role over the parser.
- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**:
  the loop underneath section 4, message by message.
