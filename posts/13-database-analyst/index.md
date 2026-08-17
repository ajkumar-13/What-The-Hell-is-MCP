# 13 · Project 1 · A secure database analyst

> **TL;DR.** A Model Context Protocol (MCP) server that lets a model query PostgreSQL is defensible only when the server, and not the model, decides what "read" means. This post builds that server: pooled connections, schema introspection as a resource, a Structured Query Language (SQL) validator built on a real parser, and structured results the model can act on. Then it makes the argument the whole project exists for, which is that the validator is a filter and the database role is the control. Every refusal message, published schema, and test count quoted below came from running [code/13-postgres-analyst/](../../code/13-postgres-analyst/).
>
> **After reading this you will be able to:**
> - Build a read-only database tool whose refusals happen before any connection is acquired.
> - Separate the layers that merely filter from the two that actually enforce, and say which of yours is which.
> - Refuse the syntax your SQL parser quietly gave up on, instead of trusting a check that never ran.
> - Test a security layer with no database at all, including the cases it deliberately allows.

![Five stacked layers between a model's SQL and the data. The top three sit inside the server and are drawn with dashed outlines and labeled as filters: an abstract syntax tree walk, a sixteen-name dangerous-function blocklist, and a LIMIT ceiling. The bottom two sit inside PostgreSQL, are drawn with solid heavy outlines and labeled as enforcement: a login role granted SELECT and nothing else with read-only transactions forced on, and a server-side statement timeout. Each layer names what it stops and where it stops being reliable.](diagrams/01-defense-in-depth.svg) *Five layers. Three of them are opinions about a string, and two of them are grants.*

---

## 1. The brief, and the thing that actually goes wrong

Someone on your team wants to ask questions of the company database in English. The data is in PostgreSQL, the questions change every day, and writing a tool per question does not scale past about four questions. So you expose one tool that takes SQL, and the model writes the SQL.

The fear everyone names first is `DROP TABLE`. It is the wrong fear. A model that has been told to answer questions does not usually reach for data definition language (DDL) on its own, and the moment it does, one string comparison catches it. The failures that actually happen are quieter.

A query that returns every row of a customers table, because nobody said not to, and now four thousand email addresses are in a conversation transcript. A statement that parses as a harmless `SELECT` and carries a `DELETE` inside a common table expression (CTE). A join with no useful index that pins a production replica for nine minutes. A tool argument interpolated straight into a statement, which is the oldest injection in the book wearing a new hat. And, the one this post spends the most time on, a check that appeared to run and did not.

The design rule that answers all of them is one sentence: **the server decides what "read" means, and the database enforces it.** Two clauses, and the second is doing more work than the first.

Two disclaimers before the code. The first is that this project is a teaching artifact pointed at a seeded database in a container, not something to attach to a production replica on a Friday. The second is that everything here is about *what the server allows*. It is not a defense against prompt injection reaching the model in the first place; [Post 19](../19-security/index.md) covers that separately, and the honest summary is that a tool which can read private data and can be steered by untrusted text is dangerous no matter how good its SQL validator is.

## 2. Architecture

Five modules and one server instance. The split is not tidiness; it is what lets the validator have no database dependency, which in turn is what lets it be tested exhaustively in milliseconds.

```text
code/13-postgres-analyst/
├── sql/
│   ├── 001-seed.sql            three tables with real foreign keys
│   └── 002-readonly-role.sql   the two login roles. The real control.
├── src/pg_analyst/
│   ├── app.py                  the MCPServer instance and the lifespan
│   ├── database.py             connection pools and how you acquire one
│   ├── security.py             SQL validation. No database import at all.
│   ├── schema.py               introspection, exposed as resources
│   ├── query.py                the read tools
│   ├── audit.py                post 14
│   └── writes.py               post 14
└── tests/
    ├── test_security.py        61 cases, no database, no event loop
    └── test_server.py          in-memory protocol tests
```

[app.py](../../code/13-postgres-analyst/src/pg_analyst/app.py) holds the instance so that every other module can hang a registration off it without a circular import:

```python
mcp = MCPServer("postgres-analyst", lifespan=lifespan)
```

Two lines in that file are load-bearing and neither is obvious.

```python
load_dotenv()

logging.basicConfig(stream=sys.stderr, level=logging.INFO, ...)
```

`load_dotenv()` runs at import time, before anything reads `os.environ`. An earlier draft of this project never called it, so `DATABASE_URL` was only ever picked up when the host happened to inject it into the process environment, and the server looked broken on a machine where the `.env` file was perfectly correct. The logging line sends every record to standard error, because under the stdio transport standard output *is* the protocol channel. [Post 04](../04-transports/index.md) has the full anatomy of that failure; the short version is that one `print()` in a tool body corrupts a JavaScript Object Notation Remote Procedure Call (JSON-RPC) frame and the error message names neither `print` nor your tool.

## 3. Pools, and reading configuration at call time

Opening a TCP connection and authenticating per tool call is slow, and doing it under a model that calls a tool six times in a turn is a good way to exhaust `max_connections`. A pool fixes both. [database.py](../../code/13-postgres-analyst/src/pg_analyst/database.py) opens it in the lifespan, which runs once per process:

```python
self._read_pool = await asyncpg.create_pool(
    url, min_size=1, max_size=10, command_timeout=READ_TIMEOUT_SECONDS,
)
```

`command_timeout` is a client-side ceiling. Keep it, but do not mistake it for the control: it makes the tool return a clear error instead of hanging the host's interface, and it does nothing at all if the client goes away. The timeout that matters is set on the role, in section 8, and enforced by the backend.

Two details in this file are worth copying.

**Nothing reads the environment at import time.** `read_url` is a property and `writes_enabled()` is a function call, so `load_dotenv()` is guaranteed to have run first no matter what order Python imports these modules in, and a test can flip a setting without reloading anything.

**A missing `DATABASE_URL` is a warning, not an exception.** The server starts, and every tool explains what is wrong when it is called. A URL that is present but wrong is a different matter: that exception propagates out of the lifespan and the server refuses to start, which is exactly what you want. The distinction shows up in the log on a machine with no configuration:

```
WARNING  postgres-analyst.database: DATABASE_URL is not set. The server is starting
         without a read pool and every database tool will refuse to run.
INFO     postgres-analyst: postgres-analyst ready (reads only)
```

and in the tool result, which carries the fix rather than a traceback:

```json
{
  "ok": false,
  "executed_sql": "SELECT email FROM users LIMIT 5",
  "row_count": 0,
  "columns": [],
  "rows": [],
  "notice": "",
  "error": "DATABASE_URL is not set, so this server has no read pool. Point it at a PostgreSQL role that holds SELECT and restart."
}
```

## 4. Schema introspection, as a resource

A model cannot write a correct query against a database it has never seen. Pasting the schema into a system prompt works until someone runs a migration, at which point it is a confident source of wrong column names.

Introspection at read time is the fix, and the primitive is a **resource**, not a tool. [Post 07](../07-resources-and-prompts/index.md) drew the line by who pulls the trigger: resources are application-driven, meaning the host or the user decides to attach them, while tools are model-controlled. A schema is context a person attaches to a conversation. It is not an action.

[schema.py](../../code/13-postgres-analyst/src/pg_analyst/schema.py) publishes three:

| Uniform Resource Identifier | What it returns |
|---|---|
| `postgres://schema` | every table and column the read-only role can see |
| `postgres://schema/{table}` | one table, for a database too large to dump whole |
| `postgres://relationships` | foreign keys, so the model joins instead of guessing |

Markdown, not JSON, because models read Markdown tables well and deeply nested JSON poorly.

**Two things in the introspection query are bugs waiting to happen, and the first edition of this post had both.** Constraint names in PostgreSQL are unique per schema, not per database. Join `information_schema.table_constraints` to `key_column_usage` on `constraint_name` alone and a `users_pkey` belonging to `staging.users` matches the `public.users` rows. The corrected join qualifies on both sides:

```sql
JOIN information_schema.key_column_usage ku
    ON tc.constraint_name = ku.constraint_name
   AND tc.constraint_schema = ku.constraint_schema
   AND tc.table_schema = ku.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema = $1::text
```

The consequence of getting it wrong is worse than a wrong row count. The output of this query is a document whose entire job is telling a model which column identifies a row, so a false primary key becomes a wrong `WHERE` clause in every subsequent query. The foreign-key query had the same class of defect and got the same fix.

The `::text` casts are not decoration either. `information_schema` columns are the `sql_identifier` domain, and comparing one to a bare placeholder leaves PostgreSQL inferring a domain type for the parameter. Casting the parameter side pins it.

One more habit, cheap and worth it. Every value that reaches a Markdown cell goes through:

```python
def _escape(text: object) -> str:
    return str(text).replace("|", "\\|")
```

Table and column names are data. Data from a database that an untrusted party can write to is untrusted input, and an unescaped pipe lets that party forge extra rows in a table the model is reading as ground truth.

## 5. The query tool, and the shape of a refusal

Wire format before the software development kit (SDK) call, as always. A `tools/call` for this server is an ordinary JSON-RPC request. There is no `initialize` and no session in revision 2026-07-28, so every request carries its own protocol version and its own declared capabilities in `_meta`:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "query_database",
    "arguments": { "sql": "SELECT email, country FROM users LIMIT 3" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

The tool that answers it is in [query.py](../../code/13-postgres-analyst/src/pg_analyst/query.py), and the whole architecture is visible in its first eight lines:

```python
@mcp.tool(title="Run a read-only SQL query", annotations=READ_ONLY)
async def query_database(sql: str) -> QueryResult:
    try:
        plan = validate_read_query(sql, max_rows=MAX_ROWS)
    except SecurityError as exc:
        log.info("query refused: %s", exc)
        return QueryResult(ok=False, executed_sql="", row_count=0,
                           error=f"Refused by the SQL validator: {exc}")
```

![A left-to-right pipeline for one read. The submitted SQL enters a chain of six checks drawn as small chips: parse one statement, root must be a query, no nested mutation, no SELECT INTO, no blocked function, and LIMIT required and clamped. Any chip failing drops to a refusal result that carries an empty executed_sql and never touches a pool. Surviving the chain, the statement is regenerated from the parse tree with comments dropped, then a connection is acquired from the read-only pool, the rows are fetched, coerced to JSON-safe values, and returned as a structured result. A second failure branch shows PostgreSQL itself refusing, which is the read-only role doing the job the validator only approximates.](diagrams/02-query-pipeline.svg) *Validation happens before a pool is touched, and what runs is regenerated from the parse tree rather than taken from the string that arrived.*

Three decisions in that pipeline are worth defending.

**Validation runs before anything is acquired.** A refused statement never gets near a connection. That is not only tidy; it is what makes most of the test suite able to run with no database at all.

**A refusal is a result, not a protocol error.** The specification's tools page splits protocol errors from tool execution errors precisely so a model can read a failure and correct itself. A structured `ok: false` with a sentence explaining the rule gets a better next turn than a JSON-RPC error object, which most models respond to with an apology. Here is a real refusal, captured in memory:

```
result_type: complete   is_error: False
```
```json
{
  "ok": false,
  "executed_sql": "",
  "row_count": 0,
  "columns": [],
  "rows": [],
  "notice": "",
  "error": "Refused by the SQL validator: A nested DELETE appears inside this statement. A CTE or subquery cannot carry a mutation past this layer."
}
```

**The caller is told what actually ran.** `executed_sql` is not the string that arrived; it is the statement regenerated from the parse tree. Section 6 explains why, and the field exists so the difference is never a surprise.

The second tool, `sample_table`, exists because "show me what is in this column" does not deserve a hand-written query. It is also where the injection lives, so it gets its own construction. PostgreSQL will not accept an identifier as a bind parameter, so a table name has to reach the statement text somehow. The safe move is to never let the caller's string get there:

```python
async def resolve_table_name(conn, table: str) -> str:
    row = await conn.fetchrow(_TABLE_EXISTS_SQL, INTROSPECTED_SCHEMA, table)
    if row is None:
        raise LookupError(f"There is no table named '{table}' in the {INTROSPECTED_SCHEMA} schema.")
    return row["table_name"]
```

The caller's string is looked up in `information_schema.tables` **as a bind parameter**, and the query is then built from the name the catalog handed back. The catalog is the allowlist, and it cannot contain a name that is not really a table. The suite verifies this against three arguments, including `users"; DROP TABLE users; --`, and all three come back as "no table named", with `executed_sql` empty. The identifier is then quoted by `sqlglot` rather than by an f-string, and the limit is coerced with `max(1, min(int(limit), 100))` so neither value can carry syntax.

## 6. The validating SQL layer, and its honest limits

[security.py](../../code/13-postgres-analyst/src/pg_analyst/security.py) has no database import. Every function in it is a pure function of a string, which is why section 10 can test it exhaustively without PostgreSQL anywhere.

It parses with `sqlglot` at `dialect="postgres"` and walks the resulting tree. The first edition of this post used `sqlparse` instead, and the difference is not a preference. `sqlparse` is a tokenizer and a formatter; it has no grammar. Two examples show what the grammar buys, and they fail in opposite directions.

**A substring search rejects a legitimate query.** The first edition's blocklist check was `if func in sql.lower()`. Under that rule this is refused:

```sql
SELECT * FROM users WHERE full_name = 'pg_sleep' LIMIT 1
```

Nothing is being called. `pg_sleep` there is a string literal, and a user whose surname collides with a Postgres function is not an attacker. The tree knows the difference, so the walk allows it, and the regenerated statement is byte-identical to the input.

**A leading-keyword check accepts a mutation.** PostgreSQL genuinely executes this, and it is an `exp.Select` at the root:

```sql
WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1
```

Walking the tree finds the nested node types `['CTE', 'Delete', 'From', 'Identifier', 'Limit', 'Literal', 'Returning', 'Select', 'Star', 'Table', 'TableAlias', 'With']`, and `Delete` in that list is a refusal.

The checks, in the order `validate_read_query` runs them:

| Check | What it refuses |
|---|---|
| `parse_single_statement` | more than one statement, unparseable text, an opaque node |
| root type | anything that is not `Select`, `Union`, `Intersect`, or `Except` |
| `reject_nested_mutations` | any of thirteen mutation node types below the root |
| `into` | `SELECT ... INTO`, which is a write wearing a `SELECT`'s clothes |
| `check_dangerous_functions` | 16 names, matched against called functions rather than text |
| `enforce_limit` | a missing `LIMIT`, a non-literal `LIMIT`, and anything over 1000 rows |

Some real refusals, produced by calling the validator directly:

```
'SELECT 1 LIMIT 1; DROP TABLE users;'
  -> Submit one statement at a time. This parsed as 2 statements, which is the
     shape of a chained-statement attack.

'SELECT * FROM users'
  -> Every query must carry a LIMIT of at most 1000 rows. Add 'LIMIT n' to the statement.

'SELECT pg_sleep(30) LIMIT 1'
  -> Function 'pg_sleep' is not allowed. It can stall a backend, reach the server's
     file system, or interfere with other sessions.

'SELECT * INTO evil FROM users LIMIT 1'
  -> SELECT ... INTO creates a new table. Refused on the read path.
```

Two rules in that table deserve a sentence each.

**A missing `LIMIT` is refused rather than silently added.** The model is perfectly capable of writing `LIMIT 50`, and a query that quietly returns a different result set than the one that was written is worse for everyone than an error naming the fix. An over-large `LIMIT` *is* clamped rather than refused, because the intent is unambiguous, and the caller is told:

```
in : 'SELECT * FROM users LIMIT 100000'
out: 'SELECT * FROM users LIMIT 1000'  limit=1000 clamped=True
```

An ungrouped aggregate is exempt, because `SELECT count(*) FROM users` returns one row by construction. A grouped aggregate returns one row per group and gets no exemption.

**The statement that runs is regenerated from the tree.**

```python
def render(statement: exp.Expression) -> str:
    return statement.sql(dialect="postgres", comments=False)
```

`comments=False` is the load-bearing argument, and it matters more in [Post 14](../14-database-writes/index.md) than here: a comment is a place to hide text from somebody skim-reading a confirmation prompt. Regeneration also means text the parser ignored cannot ride along:

```
in : 'SELECT   1 /* ; DROP TABLE users */ LIMIT 1  '
out: 'SELECT 1 LIMIT 1'
```

Be clear-eyed about what that trade buys. It removes one bug class, smuggling through parts of the string the checks did not look at, and it introduces another, a `sqlglot` generation bug that changes the meaning of a statement. The trade is worth making, and returning `executed_sql` to the caller is the price of admission.

## 7. When the parser gives up and does not tell you

This is the finding that changed how the whole project is built, and it is the strongest argument in this series against treating a parser as a security boundary.

`sqlglot` does not raise on syntax it cannot model. It wraps the raw text in an opaque `exp.Command` node, emits a logger warning, and carries on. Here is the warning, on stderr, where nothing in your tool body will ever see it:

```
'VACUUM FULL users' contains unsupported syntax. Falling back to parsing as a 'Command'.
```

And here is what your checks are then walking:

```
'VACUUM FULL users'
    type(tree) = Command
    walk types = ['Command', 'Literal']

'DO $$ BEGIN PERFORM pg_sleep(30); END $$'
    type(tree) = Command
    walk types = ['Command', 'Literal']

'CALL do_it()'
    type(tree) = Command
    walk types = ['Command', 'Literal']
```

Read the middle one again. The statement contains `pg_sleep`, which is the first name on the blocklist. The blocklist check walks the tree collecting `exp.Func` nodes. There are none, because the entire body of the `DO` block is one string literal hanging off an opaque node. The check ran, found nothing, and returned cleanly.

**That is the shape of the problem.** Every check in the module walks the parse tree, so a node the parser could not model is a node none of the checks could look inside. Nothing raised, nothing logged in your process, and a green result from every check that works by reading the tree. What refuses this one is its shape, not its contents.

The fix is one `isinstance`, placed where the tree is first obtained:

```python
if isinstance(statement, exp.Command):
    raise SecurityError(
        "The parser could not model this statement and fell back to an "
        "opaque command node, so none of the safety checks could look "
        "inside it. Refused."
    )
```

`exp.Command` is also in the `_MUTATIONS` tuple, so an opaque node nested inside an otherwise valid statement is refused too.

The cost is real and you should accept it deliberately: **valid PostgreSQL gets rejected.** `VACUUM`, `CALL`, and a `DO` block are all legal, and this server refuses all three. That is the correct trade for a tool a model drives. The rule generalizes past `sqlglot`: when a parser degrades instead of failing, every check downstream of it degrades silently with it. Find out what your parser does with input it does not understand, and if the answer is "returns something opaque", refuse the opaque thing.

![Two panels. The left panel shows a statement selecting every email address and full name from the users table with a limit of one thousand, running the full gauntlet of six checks, each of which passes, and arriving at a thousand rows of personal data with the caption that nothing here is a bug. The right panel shows an anonymous DO block calling pg_sleep being parsed into an opaque command node, with the three tree-reading checks drawn faded and marked as unable to look inside because there is nothing in the tree to inspect, and two possible endings: the blocklist holds pg_sleep and saw nothing, while this server refuses on sight.](diagrams/03-what-the-validator-cannot-see.svg) *Left, every check passes and the query is a data breach. Right, every check that reads the tree passes, because not one of them could see inside.*

## 8. The database role is the real control

Everything above is a filter. A filter is a thing that can be wrong, and each of these can be wrong in a specific way:

- The blocklist names the sixteen functions that were dangerous when it was written. PostgreSQL ships new ones and extensions ship more, and the list does not update itself.
- Names lie. A table can be a view, and a view can sit on a `SECURITY DEFINER` function that does whatever it likes with the privileges of its owner. The parser sees an identifier.
- Cost is invisible. A three-way self join with no useful index is a denial of service and looks exactly like a cheap query in the tree.
- The validator has no concept of "sensitive". Column and row policy is a database concern.

What holds when all of that is wrong is [sql/002-readonly-role.sql](../../code/13-postgres-analyst/sql/002-readonly-role.sql). This file, not `security.py`, is the security control, and the difference is that a grant is not a heuristic:

```sql
GRANT CONNECT ON DATABASE analytics TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_readonly;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM mcp_readonly;

ALTER ROLE mcp_readonly SET default_transaction_read_only = ON;
ALTER ROLE mcp_readonly SET statement_timeout = '30s';
ALTER ROLE mcp_readonly SET idle_in_transaction_session_timeout = '60s';
```

Six of those lines are ordinary. Three are the post.

**`default_transaction_read_only = ON`** turns an `INSERT` that slipped past the parser into `ERROR: cannot execute INSERT in a read-only transaction`. It applies to every transaction this role opens, whatever the session thinks it is doing.

**`statement_timeout = '30s'`** is the answer to `pg_sleep` reached through a wrapper the blocklist has never heard of, to the cartesian join, and to every other query whose cost the parser cannot see. Note that it is enforced by the backend, so it survives a client that has stopped listening. The `command_timeout` on the pool does not.

**`REVOKE CREATE ON SCHEMA public FROM PUBLIC`** is the one people skip. On PostgreSQL 14 and earlier, `PUBLIC` holds `CREATE` on the `public` schema by default, which means "read-only" quietly includes "can make new tables".

The suite has one test whose only job is to prove this layer exists independently of the Python. It reaches past the validator and uses the pool directly, the way a bug in the query path would:

```python
async def test_the_read_role_refuses_a_write_that_somehow_got_through():
    async with Client(mcp):
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            async with db.read_connection() as conn:
                await conn.execute("DELETE FROM orders WHERE id = 1")

    message = str(excinfo.value).lower()
    assert "read-only" in message or "permission denied" in message
```

That test needs a live PostgreSQL, so it is one of the twelve that skip when `DATABASE_URL` is unset. Start the container in [docker-compose.yml](../../code/13-postgres-analyst/docker-compose.yml) and it runs.

## 9. Structured results

A tool that returns a formatted string is a tool the model has to re-parse. Every tool here returns a dataclass, and the SDK turns the return annotation into an `outputSchema` and the returned object into `structuredContent`. On the wire the field is `structuredContent`; the Python SDK exposes it as `structured_content`.

```python
@dataclass
class QueryResult:
    ok: bool
    executed_sql: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    notice: str = ""
    error: str = ""
```

**The class-body annotations are load-bearing.** A class that only assigns attributes inside `__init__` has no type hints for the SDK to read, so the tool ships with `outputSchema: null` and no warning at all. [Post 06](../06-tools-in-depth/index.md) has the full failure and [Post 12](../12-testing-and-debugging/index.md) has the test that catches it, which is why every tool in this project is asserted to publish one.

The other half of structured output is coercion. PostgreSQL hands back types JSON has no opinion about, and `jsonable()` in `query.py` maps each one. The interesting line is the first:

```python
if isinstance(value, decimal.Decimal):
    return str(value)
```

A `NUMERIC` column is exact and a float is not. Returning `str` keeps a money column readable and correct; returning `float` loses precision quietly, which is a bad way to find out about a rounding bug. Dates and times become ISO 8601 strings, UUIDs and IP addresses become strings, and `bytes` becomes `<n bytes>` rather than a wall of escaped characters.

Here is the published surface, read back through an in-memory client:

```
tool query_database
  input properties : ['sql']
  output schema    : present
  annotations      : readOnly=True destructive=False idempotent=True
tool sample_table
  input properties : ['table', 'limit']
  output schema    : present
  annotations      : readOnly=True destructive=False idempotent=True
resources: ['postgres://audit', 'postgres://relationships', 'postgres://schema']
templates: ['postgres://schema/{table}']
```

The annotations are honest and they are also not enforcement. `readOnlyHint` is a hint to the host, and a host is free to ignore it. What makes these two tools read-only is that they acquire from a pool whose role holds `SELECT` and nothing else.

## 10. Testing the security layer, including what it allows

Run the suite:

```bash
cd code/13-postgres-analyst && PYTHONPATH=src pytest tests -q
```
```
........................................................................ [ 74%]
.............ssssssssssss                                                [100%]
85 passed, 12 skipped in 1.71s
```

Split by file, because the split is the design:

```
$ pytest tests/test_security.py -q
61 passed in 1.41s

$ pytest tests/test_server.py -q
24 passed, 12 skipped in 1.55s
```

The sixty-one need no database, no event loop, and no server. The validator is a pure function of a string, so there is no excuse for not testing every rule in it exhaustively and in under two seconds. The twelve skips are the tests that need a live PostgreSQL; they run when the container in `docker-compose.yml` is up.

The part worth copying is the last section of [test_security.py](../../code/13-postgres-analyst/tests/test_security.py), which is four tests asserting things the validator **allows**:

```python
def test_a_full_table_read_of_sensitive_data_is_perfectly_valid_sql():
    """The validator has no concept of "sensitive". Column-level policy is a
    database concern, not a parser concern."""
    plan = validate_read_query("SELECT email, full_name FROM users LIMIT 1000")
    assert plan.limit == 1000


def test_a_where_clause_that_matches_everything_satisfies_the_where_rule():
    plan = validate_write_statement("UPDATE users SET active = false WHERE true")
    assert plan.operation == "UPDATE"


def test_an_expensive_query_looks_exactly_like_a_cheap_one():
    validate_read_query(
        "SELECT * FROM orders a, orders b, orders c WHERE a.id <> b.id LIMIT 1000"
    )


def test_the_validator_cannot_tell_a_table_from_a_view():
    validate_read_query("SELECT * FROM users LIMIT 1")
```

None of those are bugs. They are the boundary of what a parser can know, written down where the next person to read this module will see them. A security layer whose limits are not recorded anywhere is a security layer people over-trust, and the four names above are the cheapest documentation in the project: they cannot go stale, because they run.

The first one is the important one. `SELECT email, full_name FROM users LIMIT 1000` passes every check, returns a thousand rows of personally identifiable information, and is indistinguishable from legitimate analytics. If that matters for your data, the answer is a restricted view, PostgreSQL row-level security, or a role that cannot see the column. It is not another regular expression.

---

## Common pitfalls

- **Treating the SQL validator as the security boundary.** It is a filter that reduces how often the database has to say no. The role is the boundary. If you have to choose which to build first, build `002-readonly-role.sql`.
- **Trusting a parser that degrades instead of raising.** `sqlglot` answers syntax it cannot model with an opaque `exp.Command` node and a stderr warning, so every tree-walking check runs, finds nothing, and passes. Refuse opaque nodes explicitly, and go find out what your own parser does with input it does not understand.
- **Blocking on substrings instead of on the parse tree.** `if "pg_sleep" in sql.lower()` refuses a customer whose name is `pg_sleep` and misses the same function called through a wrapper. It gets both directions wrong at once.
- **Only checking the first keyword.** A data-modifying CTE is an `exp.Select` at the root and PostgreSQL really does execute it. Walk the whole tree and refuse mutation nodes anywhere below it.
- **Joining `information_schema` on `constraint_name` alone.** Constraint names are unique per schema, not per database, so a same-named constraint elsewhere marks the wrong column as a primary key in the very document that tells the model which column identifies a row.
- **Interpolating a table name from a tool argument.** PostgreSQL will not bind an identifier, so look the caller's string up in `information_schema.tables` as a parameter and build the query from the catalog's spelling, never from the caller's.
- **Returning a formatted string instead of a dataclass.** No `outputSchema`, no `structuredContent`, and a model that has to re-parse your prose. Watch for the silent `outputSchema: null` from a class with no class-body annotations.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The protocol-error against execution-error split that makes a refusal a result rather than an error. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Resources"*, revision 2026-07-28. Why the schema is application-driven context and not a tool. <https://modelcontextprotocol.io/specification/draft/server/resources>
- PostgreSQL, *"Database roles and privileges"*. `default_transaction_read_only`, `statement_timeout`, and default privileges. <https://www.postgresql.org/docs/current/user-manag.html>
- `sqlglot`. <https://github.com/tobymao/sqlglot>. Version 30.13.0 produced every parse quoted here, including the `Command` fallback.
- `asyncpg`. <https://magicstack.github.io/asyncpg/current/>
- Willison, S. *"The lethal trifecta"* (2025). Private data, untrusted content, and exfiltration in one agent, which is the risk this server's read tool sits inside. <https://simonwillison.net/2025/Jul/6/supabase-mcp-lethal-trifecta/>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 14 — Project 1 · Writes, transactions, and an audit trail](../14-database-writes/index.md)**: the same server given `INSERT`, `UPDATE`, and `DELETE`, with a human on the critical path and an audit row that commits inside the same transaction as the change.
- **[Post 12 — Testing and debugging MCP](../12-testing-and-debugging/index.md)**: the in-memory pattern that makes the eighty-five tests above cost less than two seconds.
- **[Post 19 — Security: the attacks the protocol does not stop](../19-security/index.md)**: the attack classes a good SQL validator does nothing about, starting with the prompt injection that chose the query in the first place.
