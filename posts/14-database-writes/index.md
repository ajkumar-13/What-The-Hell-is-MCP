# 14 - Project 1: Writes, transactions, and an audit trail

> **TL;DR.** A model-initiated database write is defensible when a human has read the exact statement that will run, when the change and the record of the change commit together, and when that record lands where the model cannot edit it. This post adds `INSERT`, `UPDATE`, and `DELETE` to the Model Context Protocol (MCP) server of [post 13](../13-database-analyst/index.md) with a deployment switch, a second PostgreSQL role, a dry run, an approval parameter the model cannot see, and an audit row written inside the mutation's own transaction. Every result, question, schema and call trace quoted below is printed by a script in `snippets/` against `mcp==2.3.0`; wherever a write pool is needed, including the transaction traces, a stub stands in for the database, and the one run that needs a real PostgreSQL is a job with its own setup.
>
> **Prerequisites:** [Post 13](../13-database-analyst/index.md), [Post 08](../08-elicitation-and-mrtr/index.md).
> **Safe to skip?** Skip it if the reader can already gate a database mutation behind an elicitation the model cannot forge, keep an audit row inside the mutation's transaction while recording failures on a separate write-pool connection, and say which annotation of a resolved parameter keeps decline and cancel apart.
>
> **After reading, you will be able to:**
>
> - Put a human confirmation on the critical path of a tool call, in a parameter the model cannot forge.
> - Keep an audit row atomic with the change it describes, and record the failed writes too.
> - Choose the annotation of a resolved approval parameter that keeps accept, decline, and cancel apart.
> - Recognize the pool and fallback mistakes that quietly turn a write boundary back into nothing.

![The write path drawn as a single column with no way around any stage. A tools/call for write_database passes the deployment switch, the statement validator, a dry-run check, a human reading the validated statement over a multi round-trip exchange, and one transaction holding the statement and its audit insert. Each stage has a dashed exit naming the status the tool returns: disabled, rejected, dry_run, declined, and failed.](diagrams/01-the-write-path.svg)

*The human gate is not beside the path. It is on it, and there is no argument the model can set to skip it.*

---

## 1. The question: what has to be true before a model-initiated write is defensible?

[Post 13](../13-database-analyst/index.md) argued that a read is safe when the database role, not the parser, decides what "read" means, whatever Structured Query Language (SQL) the model sends. A write has no equivalent. There is no grant that makes `UPDATE users SET active = false WHERE created_at < now()` safe, because that statement is exactly what a write role is for.

The difference is not that a write is riskier. It is that a write is irreversible by a system with no memory of the previous value. A wrong `SELECT` costs a wasted turn. A wrong `UPDATE` costs whatever was in the column.

So the write path adds four things the read path does not have, and each is a separate gate rather than a check inside a bigger function:

1. **A deployment switch.** `ENABLE_WRITES` is off by default. A server that was never meant to write cannot be talked into it.
2. **A separate role.** Writes go through `DATABASE_WRITE_URL`, a second login with its own grants, on its own pool. The read path cannot reach it.
3. **A human.** The exact statement is shown to a person, and it runs only if they say yes.
4. **One transaction, with the audit row inside it.** The change and the record of the change commit together or neither does.

The question for this post is the one in the heading: **what has to be true before a model-initiated database write is defensible?** The answer is those four gates, each in the right place, and most of the detail below is about the places an earlier version of this project put them wrong. The code is [code/13-postgres-analyst/](../../code/13-postgres-analyst/), the project post 13 started; this post adds `writes.py` and `audit.py` to it.

## 2. Three connection accessors, not one

The smallest change in the project has the largest consequence. [database.py](../../code/13-postgres-analyst/src/pg_analyst/database.py) exposes three ways to get a connection, and picking the wrong one is a bug that presents as something else entirely. `snippets/write_gates.py` prints the first line of each accessor's docstring:

```
read_connection    A connection from the read-only pool.
write_connection   A write-pool connection with **no** transaction wrapped around it.
write_transaction  A write-pool connection inside an explicit transaction.
```

Two callers need the middle one, and both are easy to get wrong.

**Creating the audit table.** `ensure_audit_table()` issues `CREATE TABLE IF NOT EXISTS` and two `CREATE INDEX IF NOT EXISTS` statements from the server's lifespan, at startup. An earlier version of the project, as its own docstrings record, ran them through the read pool. Against a development database with one superuser login that works. Against the read-only role, whose every transaction is read-only, PostgreSQL refuses with `cannot execute CREATE TABLE in a read-only transaction`, and because the call is inside the lifespan, the exception propagates and the server never starts. The report is "the server dies on launch", which points at everything except a `CREATE TABLE`. The startup trace that `snippets/transaction_trace.py` records (section 5 describes how) shows where the statements go now:

```
1. acquire: write pool
2. execute(CREATE TABLE IF NOT EXISTS mcp_audit_log ( id BI...)
3. execute(CREATE INDEX IF NOT EXISTS mcp_audit_log_occurre...)
4. execute(CREATE INDEX IF NOT EXISTS mcp_audit_log_operati...)
```

**Recording a failed write.** The audit insert for a *successful* write goes on the caller's transaction. The insert for a *failed* one cannot: by the time the server knows the write failed, the transaction has rolled back and taken any audit row written inside it along. So the failure path takes a fresh connection, and it must come from the write pool. Section 5 shows the trace, and section 9 shows what happens when that connection comes from the read pool instead.

Both mistakes have the same shape. A function that reaches for "a connection" without saying which pool works on a machine where both roles are the same login, and breaks only on the machine that took the security advice.

**And there is no fallback.** The earlier version had this line, which `snippets/write_gates.py` evaluates with only `DATABASE_URL`, the read role's Uniform Resource Locator (URL), set:

```python
    write_url = os.getenv("DATABASE_WRITE_URL", os.getenv("DATABASE_URL"))
```

```
the first edition's write URL: postgresql://mcp_readonly:readonly_password@localhost:5432/analytics
Database().write_url today:    None
```

On any machine where only `DATABASE_URL` was set, that line made the "write pool" the read-only role, and the first statement that pool ran failed at the database with a read-only or permissions error, far from the line that caused it, instead of a clear message that no write URL was configured. Against today's lifespan that first statement is the audit table's `CREATE TABLE`, so the server would not even start. Worse, it made the two-role design a suggestion: nothing in the running system said which of the two configurations was in force. **A fallback that quietly degrades a security boundary is worse than a hard failure**, because a hard failure is a bug report and a quiet degradation is a false sense of a control. Now `write_url` is `None`, no write pool opens, and the tool says so.

The roles themselves are in [sql/002-readonly-role.sql](../../code/13-postgres-analyst/sql/002-readonly-role.sql). `mcp_writer` holds `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on the seeded tables, plus `CREATE` on the schema so that it can build the audit table at startup. It does not own the seeded tables, so it cannot `DROP` or `ALTER` them; it does not hold `TRUNCATE`; and it is not a superuser, so the blocklisted file-system functions fail regardless of the blocklist.

The file closes with what a trail that a compromised write path can add to but not edit would take. Revoking `UPDATE` and `DELETE` on `mcp_audit_log` from `mcp_writer` does not get there. Because `mcp_writer` creates that table, it owns it, and PostgreSQL treats an owner as holding every grant option on its own table and the right to drop it: an owner can re-grant itself whatever was revoked. A trail that is append-only for the write role needs a table created by a different role, which grants `mcp_writer` only what an `INSERT ... RETURNING id` needs; the project's own startup creation of the table would then have to be skipped, since `CREATE INDEX IF NOT EXISTS` on a table another role owns fails before the existence check.

## 3. Validate first, then dry run, then ask

The write validator is the read validator's sibling in [security.py](../../code/13-postgres-analyst/src/pg_analyst/security.py), and it is deliberately narrower: `INSERT`, `UPDATE`, `DELETE`, and nothing else. `snippets/write_gates.py` turns `ENABLE_WRITES` on, opens a stand-in write pool so that a question could be asked, and sends statements the validator refuses; each call also counts the questions its user was shown:

```
--- {"sql": "TRUNCATE TABLE users"}
{
  "status": "rejected",
  "operation": "",
  "table": "",
  "executed_sql": "",
  "affected_rows": 0,
  "audit_id": 0,
  "detail": "Refused by the SQL validator: Only INSERT, UPDATE, and DELETE are allowed here, and this is TRUNCATE. Schema changes are not this server's job."
}
isError=true  questions asked: 0

--- {"sql": "DELETE FROM users"}
{
  "status": "rejected",
  "operation": "",
  "table": "",
  "executed_sql": "",
  "affected_rows": 0,
  "audit_id": 0,
  "detail": "Refused by the SQL validator: DELETE without a WHERE clause would touch every row in the table. Add a WHERE clause naming the rows you mean."
}
isError=true  questions asked: 0
```

No data definition language (DDL), no `TRUNCATE`, no `COPY`, no `GRANT`, no `MERGE`. A model with a shell into the schema is a different product from a model that can correct a row, and this server is the second one. A refusal is a result, not a protocol error, for the reason post 13 gives where it discusses the shape of a refusal: a model that reads a structured refusal can correct itself on the next turn. It carries `isError: true`, as the schema comment on `isError` asks of an error that originates in the tool, through the same `tool_error()` helper the read path uses; a disabled server and a missing write pool answer the same way. A dry run, a commit, and a human saying no are not failures of the tool and come back with `isError` false.

The `WHERE` rule is a floor, not a guarantee, and the validator's own comment says so. The same script sends `UPDATE users SET active = false WHERE true` as a dry run and gets `"status": "dry_run"` with `"executed_sql": "UPDATE users SET active = FALSE WHERE TRUE"`. The rule is aimed at the statement with no predicate at all. What bounds the rest is the human in section 4.

Before anyone is asked anything there is a dry run, the cheapest checkpoint in the design. Called with `{"sql": "update users set active=false where id = 3", "dry_run": true}`, the tool answers:

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

`executed_sql` is already normalized, uppercase keywords and `FALSE` rather than `false`, because it is regenerated from the parse tree. It is the same string that will appear in the confirmation prompt and in the audit row. The script's count for that call is `questions asked: 0`, and it is `0` for the switched-off server too, whose answer is `"status": "disabled"`.

That the dry run asks nobody, and that an invalid statement is refused without asking, are design decisions. **Asking a human to approve something the server has already decided to reject teaches the human to click through prompts without reading them.** Consent is a limited resource; spend it only where the answer changes what happens.

## 4. Confirming over a multi round-trip request, and the resolver signature

Revision 2026-07-28 removed the server-to-client request channel. A server cannot call the client and wait. It answers the request with an interim result that asks for input, the client collects the answer, and the client calls the same tool again. That pattern is Multi Round-Trip Requests (MRTR), and [post 08](../08-elicitation-and-mrtr/index.md) walks it message by message.

Here is what this server puts on the wire for the first leg of a call. `snippets/confirm_wire.py` calls the tool through the client's session with `allow_input_required=True`, so the interim result comes back as it is instead of being answered, and shortens `requestState` for printing (the real value is a sealed token of about 400 characters):

```json
{
  "_meta": {
    "io.modelcontextprotocol/serverInfo": {
      "name": "postgres-analyst",
      "version": ""
    }
  },
  "resultType": "input_required",
  "inputRequests": {
    "pg_analyst.writes:_confirm_write": {
      "method": "elicitation/create",
      "params": {
        "mode": "form",
        "message": "Commit this DELETE against orders?\n\nDELETE FROM orders WHERE id = 4242\n\nIt runs inside a single transaction and is recorded in the audit log.",
        "requestedSchema": {
          "properties": {
            "confirm": {
              "description": "Confirm that this statement should be committed.",
              "title": "Confirm",
              "type": "boolean"
            },
            "reason": {
              "default": "",
              "description": "Optional note recorded alongside the change in the audit log.",
              "title": "Reason",
              "type": "string"
            }
          },
          "required": [
            "confirm"
          ],
          "type": "object"
        }
      }
    }
  },
  "requestState": "v1.<sealed, shortened here>"
}
```

The original request is now finished. The key `pg_analyst.writes:_confirm_write` is derived from the resolver's `module:qualname`, which is stable across workers and is one of the two things this needs behind a load balancer. The other is the key: the software development kit (SDK) seals `requestState` itself, by default under a process-local ephemeral key, which is correct for stdio (standard input and output) and wrong for more than one worker; post 08 shows the shared-key policy that fixes it.

**The parameter the model cannot see.** In the source this is a resolver plus one annotated parameter, in [writes.py](../../code/13-postgres-analyst/src/pg_analyst/writes.py):

```python
@mcp.tool(
    title="Write to the database",
    annotations=ToolAnnotations(
        title="Write to the database",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
async def write_database(
    sql: str,
    approval: Annotated[ElicitationResult[WriteApproval], Resolve(_confirm_write)],
    dry_run: bool = False,
) -> Annotated[CallToolResult, WriteResult]:
```

The SDK strips every `Resolve`-backed parameter from the published input schema. Read back through an in-memory client, `snippets/confirm_wire.py` prints:

```
tool write_database
  input properties : ['sql', 'dry_run']
  required         : ['sql']
  annotations      : readOnly=False destructive=True idempotent=False
```

`approval` is absent, so there is no field in which a model can supply its own approval. The project's suite asserts the whole set, `set(props) == {"sql", "dry_run"}`, rather than the absence of one name, because a check for `approval` alone would keep passing while the next resolved parameter leaked. The `destructive` annotation tells a host to treat the tool with care, but it is a hint: the Tools page has clients treat annotations as untrusted unless they come from a trusted server, so the gate has to be in the server.

**Now the part that took the longest to get right.** The resolver's return type is a union, so that it can either ask or decline to ask:

```python
def _confirm_write(
    sql: str, dry_run: bool
) -> Elicit[WriteApproval] | ElicitationResult[WriteApproval]:
    """Decide what, if anything, to ask the human.

    A resolver may take any of the tool's arguments by name, and the SDK fills
    in defaults the caller omitted, so `dry_run` is always a real bool here.

    Four conditions send back a stand-in `DeclinedElicitation` instead of an
    `Elicit`, and no question is put to the user at all:

    - writes are switched off, so there is nothing to approve;
    - this is a dry run, so nothing will be executed;
    - there is no write pool, so an approved write could not run;
    - the statement is going to be refused anyway, and asking a human to
      approve something the server has already decided to reject teaches them
      to click through prompts without reading them.

    The tool body handles each of those cases before it ever looks at the
    approval, so the stand-in is never mistaken for a real accept.
    """
    if not writes_enabled() or dry_run or not db.writes_configured:
        return DeclinedElicitation()

    try:
        plan = validate_write_statement(sql)
    except SecurityError:
        return DeclinedElicitation()

    return Elicit(_confirmation_question(plan), WriteApproval)
```

The resolver takes `sql` and `dry_run` by name, matched against the tool's own arguments. In the four no-ask cases it returns a stand-in and nothing goes on the wire, which is why section 3's counts were all zero.

The parameter's annotation is the load-bearing choice, and three plausible spellings behave differently. `snippets/resolver_spellings.py` registers one small tool per spelling on a throwaway server and has a user accept, decline, and cancel each:

| Annotation | Registration | On accept | On decline or cancel |
|---|---|---|---|
| `Annotated[ElicitationResult[T], Resolve(f)]` | fine | `AcceptedElicitation` | `DeclinedElicitation`, `CancelledElicitation` |
| `Annotated[ElicitationResult[T] \| None, Resolve(f)]` | fine | unwrapped to bare `T` | `isError: true`, `Error executing tool optional_inside: Resolver for parameter 'ap' could not resolve: elicitation was decline` |
| `Annotated[ElicitationResult[T], Resolve(f)] \| None` | `InvalidSignature` | | |

Only the first row preserves the three-way branch. The second registers without complaint and quietly converts a user saying no into a tool error, which is the wrong shape: a person declining is not a failure of the tool. The third fails at registration with a message that names the fix:

```
  at registration: InvalidSignature: Parameter 'ap' of 'optional_outside' wraps `Resolve(...)` in a union; annotate the parameter directly as `Annotated[T, Resolve(...)]`
```

**One measured caveat on the stand-in.** When a resolver returns `DeclinedElicitation()` rather than an `Elicit`, the SDK treats it as an already-resolved value and hands the tool body an `AcceptedElicitation` whose `.data` is that `DeclinedElicitation`. The same script prints:

```
  asked=0 body got AcceptedElicitation[Any], data=DeclinedElicitation(action='decline'), isinstance(ap, DeclinedElicitation)=False
```

So a tool that relied on `isinstance(approval, DeclinedElicitation)` for the stand-in would read a synthetic skip as an accept. `write_database` never gets there: the deployment switch, the validator, the dry-run return, and the write-pool check handle all four no-ask conditions before the body inspects `approval`. That ordering is part of the pattern, not an accident of it.

The real outcomes all land as a plain `"complete"` result with a distinct `detail`, and none of them is an error. `snippets/confirm_wire.py` answers the same call four ways. A stand-in write pool answers every `DELETE` with `DELETE 0`, the tag PostgreSQL returns for a `DELETE` that matches no row, so the approved write commits and changes nothing:

```
decline                    asked=1 isError=false status='declined' detail='The user declined to confirm this write.'
cancel                     asked=1 isError=false status='declined' detail='The confirmation prompt was dismissed.'
accept {"confirm": false}  asked=1 isError=false status='declined' detail='The user answered no to the confirmation.'
accept {"confirm": true}   asked=1 isError=false status='committed' detail='Committed. 0 row(s) changed, recorded in the audit log as entry 1.'
```

The third row is the one worth pausing on, and it is the trap post 08 names. `action: "accept"` means the form came back, not that the user said yes. A confirmation checkbox left unchecked arrives as an accept carrying `confirm: false`.

The specification is quiet on one point here. The Elicitation page says what a server should *do* with an accept, a decline, and a cancel, and that it must handle the decline and the cancel; it does not say what the server must *return*. A `"complete"` result with `isError: true`, a `"complete"` result describing the abandonment without `isError`, and another `input_required` result re-asking are all permitted by the text. This server picks the middle one, on the grounds that a user declining is not a tool failure. That is a design choice the revision leaves open, not a rule.

**The question is a pure function of the SQL.** This is the difference between a call that converges and one that does not. The SDK matches a recorded answer against a SHA-256 digest of the exact rendered question, its message and its schema, where SHA-256 is the 256-bit Secure Hash Algorithm. Put a timestamp or a live row count in the message and the retry renders a different string, the recorded answer never matches, and the call goes round until the client's `input_required_max_rounds` gives up; post 08 prints that failure. From the outside it looks like the user's answer being ignored.

```python
def _confirmation_question(plan: WritePlan) -> str:
    """Render the confirmation prompt. Pure function of the plan."""
    return (
        f"Commit this {plan.operation} against {plan.table}?\n\n"
        f"{plan.sql}\n\n"
        "It runs inside a single transaction and is recorded in the audit log."
    )
```

Every character comes from the validated plan, which comes from `sql`. Two tests in the project pin the property: one asserts exactly one question in a single call, the other calls the tool three times with the same argument and asserts one distinct question text. `snippets/confirm_wire.py` repeats the second:

```
three calls, distinct question texts: 1 of 3
```

**Comments are stripped on the way in.** `plan.sql` comes from `statement.sql(dialect="postgres", comments=False)`, so a statement submitted as

```sql
update  users   set active = false where id = 3 /* and everything else */
```

reaches the prompt as `UPDATE users SET active = FALSE WHERE id = 3`; `snippets/write_gates.py` sends it as a dry run and prints that `executed_sql`. A comment is inert to PostgreSQL and is therefore a perfect place to hide text from somebody skim-reading a confirmation dialog. Dropping it costs nothing and closes the gap between what a human read and what runs. It also means the prompt, the audit row, and the statement PostgreSQL executes are one string, because all three come from `plan.sql`.

## 5. One transaction, with the audit row inside it

Gate 4 is twelve lines, and the shape is the whole argument. From `write_database` in [writes.py](../../code/13-postgres-analyst/src/pg_analyst/writes.py):

```python
        async with db.write_transaction() as conn:
            command_tag = await conn.execute(plan.sql)
            touched = affected_rows(command_tag)
            audit_id = await record_operation(
                operation=plan.operation,
                target_table=plan.table,
                sql_text=plan.sql,
                affected_rows=touched,
                success=True,
                note=note,
                conn=conn,
            )
```

`conn=conn` is the entire point. `record_operation` inserts on the caller's connection, so the audit row is inside the mutation's transaction. Had the audit insert raised, the write above it would have rolled back with it. There is no state in which the change happened and the log does not say so.

`affected_rows` comes from PostgreSQL's own command tag, not from a count the server estimated. The tags have the forms `INSERT 0 3`, `UPDATE 5`, and `DELETE 2`, and the count is always the last field.

![Two call traces side by side. The success trace has five steps: acquire a write-pool connection, BEGIN, execute the statement, insert the audit row on the same connection, COMMIT. The failure trace has six: acquire, BEGIN, execute, ROLLBACK, then a second acquire from the write pool and a second audit insert, because the first transaction is gone and anything written inside it went with it.](diagrams/02-transaction-and-audit.svg)

*Success is one transaction. Failure is a transaction that no longer exists plus a second, separate write.*

The traces below are recorded, not drawn from imagination, and no PostgreSQL produced them. In `snippets/transaction_trace.py` a recording stub stands in for the two `asyncpg` pools: it writes down every acquire, `BEGIN`, `execute`, `fetchval`, `COMMIT`, and `ROLLBACK` in order, answers each statement with the command tag a freshly seeded database returns for it (one order in `sql/001-seed.sql` is cancelled, hence `DELETE 1`), raises the errors a scenario asks for, and drops the rows a rolled-back transaction inserted, which is what PostgreSQL guarantees. The project's code is imported unmodified. Success:

```
1. acquire: write pool
2. BEGIN
3. execute("DELETE FROM orders WHERE status = 'cancelled'") returns 'DELETE 1'
4. fetchval(audit insert, success=True) returns id 1
5. COMMIT
{"status": "committed", "operation": "DELETE", "table": "orders", "executed_sql": "DELETE FROM orders WHERE status = 'cancelled'", "affected_rows": 1, "audit_id": 1, "detail": "Committed. 1 row(s) changed, recorded in the audit log as entry 1."}
```

Failure, with the stub raising `InsufficientPrivilegeError` for the statement:

```
1. acquire: write pool
2. BEGIN
3. execute("DELETE FROM orders WHERE status = 'cancelled'") raises InsufficientPrivilegeError
4. ROLLBACK
5. acquire: write pool
6. fetchval(audit insert, success=False) returns id 1
{"status": "failed", "operation": "DELETE", "table": "orders", "executed_sql": "DELETE FROM orders WHERE status = 'cancelled'", "affected_rows": 0, "audit_id": 1, "detail": "Rolled back. PostgreSQL said: permission denied for table orders"}
```

Steps 5 and 6 are the ones to internalize. **The failure audit has to be a second, separate write**, on a connection acquired after the rollback, because the transaction that would have carried it no longer exists. That is why `write_connection()` exists as its own accessor.

The same script makes the audit insert itself fail, which is the claim two paragraphs up put to the test:

```
1. acquire: write pool
2. BEGIN
3. execute("UPDATE users SET active = FALSE WHERE id = 3") returns 'UPDATE 1'
4. fetchval(audit insert, success=True) raises InsufficientPrivilegeError
5. ROLLBACK
6. acquire: write pool
7. fetchval(audit insert, success=False) raises InsufficientPrivilegeError
```

The `UPDATE` executed, its audit row could not be written, and the transaction rolled back with both inside it, so nothing changed unlogged. The failure row could not be written either, and the tool still answered with `"status": "failed"` and the database's message, because `_record_failure` swallows its own exceptions on purpose:

```python
    except Exception as exc:  # noqa: BLE001 - deliberate, see docstring
        log.error("could not record the failed write in the audit log: %s", exc)
        return 0
```

Losing an audit row is bad. Losing the caller's original error message behind a second exception raised while trying to record the first is worse, because then nobody can debug either failure. The error goes to the server's log on stderr instead, where the script picks up `could not record the failed write in the audit log: permission denied for table mcp_audit_log`.

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

![One audit row exploded into eleven fields, each labeled with where its value comes from. Four come from the database: the serial identifier, the timestamp from now, the affected row count from the command tag, and the error message. Three come from the parse tree: the operation, the target table, and the regenerated statement with comments stripped. One comes from the approving human, the optional note. Two come from the operator's environment, and one, the success flag, from the server's own control flow.](diagrams/03-the-audit-record.svg)

*Eleven fields. Exactly one of them carries content the model chose, and a person read it before it ran.*

That provenance is the property worth designing for. Walking the fields:

| Field | Where the value comes from |
|---|---|
| `id`, `occurred_at` | PostgreSQL: `BIGSERIAL` and `now()` |
| `operation`, `target_table` | the parse tree, via `WritePlan`, not any string the model chose |
| `sql_text` | `plan.sql`, regenerated with `comments=False`, the exact string the human read |
| `affected_rows` | PostgreSQL's command tag |
| `success` | the server's own control flow |
| `error_message` | the error's message, on the failure path |
| `note` | typed by the human in the confirmation form |
| `session_id`, `actor` | the operator's environment |

The model contributes the semantics of one field and the literal text of none of them, since even `sql_text` is regenerated from the tree. That is the reason to derive `operation` and `target_table` from the tree rather than from a keyword match on the submitted string.

Two smaller decisions in [audit.py](../../code/13-postgres-analyst/src/pg_analyst/audit.py) are worth borrowing.

**The identity variables are named for what they configure.** They are `AUDIT_SESSION_ID` and `AUDIT_ACTOR`, and both default to `unknown`; the stub's committed row in `snippets/transaction_trace.py` carries `session_id='unknown' actor='unknown'`. They avoid an `MCP_` prefix because a variable's name should say which component reads it, not to dodge the SDK, which under `mcp==2.3.0` ignores even a malformed `MCP_DEBUG`: `snippets/transaction_trace.py` builds an `MCPServer` with a malformed `MCP_DEBUG` set and prints `MCPServer('probe') with MCP_DEBUG=not-a-bool: constructed, settings.debug=False`.

**Neither is an authenticated identity, and the code says so.** Under stdio there is nobody to authenticate: the host launched this process. Recording `unknown` honestly is better than recording a value that looks like an identity and is not. [Post 20](../20-authorization/index.md) covers what to record when there is a real one.

## 7. The trail as a resource, not a tool

The audit log is published at `postgres://audit`, as Markdown, through the **read** pool; `mcp_readonly` can read the table because `sql/002-readonly-role.sql` gives it `SELECT` on whatever `mcp_writer` creates, by default privileges.

It is a resource rather than a tool, for the same reason the schema is one in post 13. Resources are application-driven: a human pins this into a conversation to check the assistant's work. It is not an action the model takes to decide what to do next, and giving the model a tool that reads its own audit trail is a small but real step toward a system that reasons about its own oversight.

Content that reaches a Markdown cell is escaped, and here that matters more than it did for table names:

```python
def _cell(value: object) -> str:
    """Make a value safe to drop into a Markdown table cell.

    Recorded SQL is attacker-influenced text: whoever can talk to the model can
    get a statement recorded here, and an unescaped pipe would let them forge
    extra rows in this table.
    """
    return str(value).replace("|", "\\|").replace("\n", " ")
```

Whoever can talk to the model can get a statement recorded in this table. `snippets/transaction_trace.py` renders a recorded `UPDATE` whose string literal carries three pipes and a newline, enough to end the row early and start a forged one:

```
| 2026-10-09 12:00:00 | ok | UPDATE | `products` | 1 | `UPDATE products SET name = 'a \| ok \| \| forged' WHERE id = 8` |
```

Every pipe is escaped and the newline is flattened, so the line stays one row of six cells in the very document a human reads to audit the assistant.

The resource also handles the case where the table does not exist yet, which is every read-only deployment, since the table is created only when writes are enabled:

```python
    except asyncpg.UndefinedTableError:
        return (
            "Audit trail unavailable: the audit table does not exist yet. It "
            "is created at startup when ENABLE_WRITES is true."
        )
```

One check closes the loop on the whole write path: one `UPDATE` through accept, one through decline, one through cancel, and the trail read back to see which of them left a row. The same script runs it against the stub, adding the accept with `confirm` false:

```
accept {"confirm": true}   status='committed' audit_id=1
decline                    status='declined' audit_id=0
cancel                     status='declined' audit_id=0
accept {"confirm": false}  status='declined' audit_id=0
# Recent write operations

| Time | Result | Operation | Table | Rows | Statement |
| --- | --- | --- | --- | --- | --- |
| 2026-10-09 12:00:01 | ok | UPDATE | `users` | 1 | `UPDATE users SET active = FALSE WHERE id = 3` |
```

Only the approved write left a row. A refusal leaves none, because nothing was attempted; the timestamp is the stub's clock. Section 8 gives the job that runs the same check against a real PostgreSQL.

## 8. Make it run: the suite, the scripts, and a live database

The project is the post's code. From inside `code/13-postgres-analyst/`:

```bash
uv sync --extra dev               # once: the environment, with the test tools
uv run --extra dev pytest         # 89 passed, 12 skipped without a database
```

With `-q` added for the short form, the run on the machine that wrote this post (the time varies):

```
........................................................................ [ 71%]
.................ssssssssssss                                            [100%]
89 passed, 12 skipped in 2.42s
```

Every write-path test runs with no database, because every gate before the transaction is pure: writes disabled, nobody asked when they are, an invalid statement rejected without asking, the dry run, decline, cancel, accept with `confirm` false, the approval hidden from the schema, one question per call, identical questions across three calls, an approved write with no write pool, and nobody asked when there is none. The tests that need a question asked open a stand-in write pool that accepts only the startup `CREATE` statements. The twelve skips are the tests marked `needs_database`, and all twelve are on the read path. No test in the suite commits a write to PostgreSQL; the committed and failed paths of section 5 are exercised by the stub script and by the live job below.

Four scripts under `snippets/` print everything quoted above. Each runs from the series root with no arguments in a few seconds and needs no database, host, or network. `resolver_spellings.py` needs only the SDK; the other three import the project in memory, and the root environment pins the project's `asyncpg`, `sqlglot` and `python-dotenv` beside the SDK, so nothing from the project's own environment is needed.

```bash
uv run python posts/14-database-writes/snippets/write_gates.py          # sections 2 and 3: accessors, the fallback, gates before any question
uv run python posts/14-database-writes/snippets/confirm_wire.py         # sections 4 and 9: the schema, the first leg, the answers, one question
uv run python posts/14-database-writes/snippets/resolver_spellings.py   # section 4: three annotations and the stand-in
uv run python posts/14-database-writes/snippets/transaction_trace.py    # sections 2, 5, 6, 7 and 9: recorded transactions and the trail
```

The live run needs the container `docker-compose.yml` defines, which seeds the tables and creates both roles. From inside `code/13-postgres-analyst/`:

```bash
docker compose up -d              # PostgreSQL 17 on 127.0.0.1:5432
cp .env.example .env              # then set ENABLE_WRITES=true in .env
uv run python -m pg_analyst       # stdio, what a desktop host spawns
```

Then, from the series root, `snippets/jobs/live_lab.py` is the check from section 7 against the real database: a dry run, then one `UPDATE` through accept, decline, cancel, and accept with `confirm` false, then a `DELETE` the foreign key refuses, then `postgres://audit` read back. It takes a few seconds against a local container, the validator does not run it, and its output is not quoted in this post because no PostgreSQL was available where the post was checked. The reader's run should show exactly one `ok` row for the approved `UPDATE` and one `failed` row for the refused `DELETE`, with nothing for the three refusals.

```bash
uv run python posts/14-database-writes/snippets/jobs/live_lab.py
```

## 9. What can go wrong?

Four mistakes print a symptom, and two more are about people rather than code.

**The failure path is given the read pool.** `snippets/transaction_trace.py` reproduces the earlier version's mistake on one instance, pointing `write_connection` at the read pool, and lets the statement fail:

```
5. acquire: read pool
6. fetchval(audit insert, success=False) raises ReadOnlySQLTransactionError
{"status": "failed", "operation": "DELETE", "table": "orders", "executed_sql": "DELETE FROM orders WHERE status = 'cancelled'", "affected_rows": 0, "audit_id": 0, "detail": "Rolled back. PostgreSQL said: permission denied for table orders"}
audit rows kept: []
log: ERROR postgres-analyst.writes: could not record the failed write in the audit log: cannot execute INSERT in a read-only transaction
```

The tool's answer looks almost normal; only `"audit_id": 0` and one log line give it away. The stub raises the error with the wording PostgreSQL uses for a read-only transaction. A server wired this way records every success and no failures at all, and the failures are the entries an audit log exists for.

**`ENABLE_WRITES` is on and `DATABASE_WRITE_URL` is missing.** The server starts, logs a warning, and refuses every valid write before anyone is asked. `snippets/confirm_wire.py` sends section 4's approved `DELETE` once more with no write pool:

```
accept {"confirm": true}   asked=0 isError=true status='failed' detail='DATABASE_WRITE_URL is not set, so this server has no write pool. Nothing was executed and nobody was asked to confirm.'
```

The user would have approved, and was never shown the question: the resolver checks the pool as well as the switch and the statement, so consent is never spent on a write that cannot run.

**A test client with no elicitation callback.** `Client(mcp)` without `elicitation_callback` declares no elicitation capability, so the resolver may not ask, and the call fails before any question is sent. `snippets/confirm_wire.py` prints:

```
MCPError -32021: Client did not declare the form elicitation capability required by resolver 'pg_analyst.writes:_confirm_write'
data: {"requiredCapabilities": {"elicitation": {"form": {}}}}
```

`-32021` is `MISSING_REQUIRED_CLIENT_CAPABILITY`, and the resolver's `module:qualname` in the message is the fastest way to find the one that asked.

**A resolver that reads the clock.** A question that is not a pure function of the arguments never converges, as section 4 explains; the symptom is a user who keeps being asked and a call that ends in `InputRequiredRoundsExceededError`.

**Routing a steady stream of writes through the dialog.** A person asked to confirm one write a week reads it. A person asked to confirm forty in an afternoon clicks yes. Elicitation is a consent mechanism only when the question is rare, specific, and expected, and none of the code above can make that true. A workflow that generates a steady stream of writes needs a narrower tool with no free-text SQL in it, not a better dialog.

**Treating an approved statement as an understood one.** `UPDATE users SET active = false WHERE created_at < now() - INTERVAL '1 year'` is easy to read and hard to evaluate, because the text does not say how many rows that is. Showing a count would help and would break question determinism, since the count changes between rounds, so it would have to be gathered in an earlier round and carried in `requestState`. That is a real design, and this project does not implement it. The parser bounds the shape of a statement, never its blast radius.

## Common pitfalls

1. **Falling back from the write URL to the read URL.** One `os.getenv(a, os.getenv(b))` turns the write pool into the read-only role on every machine that configured one URL, and the two-role design silently becomes a comment; let the pool be absent and say so.
2. **Writing the failure audit on the rolled-back connection, or on the read pool.** On the first it disappears with the transaction, on the second it fails as read-only; the failure path acquires again, from the write pool, after the rollback.
3. **Annotating the approval as `ElicitationResult[T] | None` inside `Annotated`.** It registers without complaint, unwraps to the bare model on accept, and turns decline and cancel into `isError: true`; use `Annotated[ElicitationResult[T], Resolve(f)]` and branch on all three.
4. **Treating `action: "accept"` as approval.** Accept means the form came back; the checkbox inside it can still be false, and a tool that skips that branch commits a write its owner explicitly declined.
5. **Letting the confirmation question vary between rounds.** A timestamp or a live row count changes the digest, the recorded answer never matches, and the call goes round to the client's limit; derive every character from the tool's arguments.
6. **Asking for approval of a statement that is already doomed.** A prompt the user cannot meaningfully refuse is training for the prompt that matters; refuse first, and ask only when the answer decides what runs.

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The four steps, and why the server returns its question instead of sending one. <https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr>
- Specification, *"Elicitation"*, revision 2026-07-28. Form mode, the three actions, and the requirement that a server handle a decline and a cancel. <https://modelcontextprotocol.io/specification/draft/client/elicitation>
- PostgreSQL roles and privileges. The grants behind `mcp_writer` and `mcp_readonly`, and default privileges for tables created later. <https://www.postgresql.org/docs/current/user-manag.html>
- PostgreSQL documentation, *Privileges* (ddl-priv). The ownership rule behind section 2: the right to drop or alter a table is inherent in owning it. <https://www.postgresql.org/docs/current/ddl-priv.html>
- `asyncpg`. Connection pools and the `transaction()` context manager that `write_transaction()` wraps. <https://magicstack.github.io/asyncpg/current/>
- `sqlglot`. The parser behind the validator and the regenerated, comment-free `plan.sql`. <https://github.com/tobymao/sqlglot>
- MCP Python SDK, release 2.3.0. `Resolve`, `Elicit`, `ElicitationResult`, and `Client` with an `elicitation_callback`; every result, error and schema quoted here came from this release driving the project. <https://github.com/modelcontextprotocol/python-sdk>
- [code/13-postgres-analyst/](../../code/13-postgres-analyst/). The complete project, with `writes.py`, `audit.py`, and the role definitions.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 15 - Project 2: A DevOps first responder](../15-devops-responder/index.md):** the read-only-by-construction argument applied to a Kubernetes cluster, where the read side is harder and the objects are larger.
- **[Post 19 - Security: the attacks the protocol does not stop](../19-security/index.md):** the threat model this project's controls sit inside, including which of them stop an attack and which merely narrow it.
