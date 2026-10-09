# 13 - Project 1: A secure database analyst

> **TL;DR.** A Model Context Protocol (MCP) server that lets a model query PostgreSQL is defensible only when the server, and not the model, decides what "read" means, and the database enforces it. This post builds that server: pooled connections, schema introspection as a resource, a Structured Query Language (SQL) validator built on a real parser, and structured results the model can act on. Then it makes the argument the whole project exists for, which is that the validator is a filter and the database role is the control. Every refusal, published schema, and test count quoted below is printed by a script in `snippets/` or by the project's own test run.
>
> **Prerequisites:** [Post 12](../12-testing-and-debugging/index.md).
> **Safe to skip?** Skip it if the reader can already say which layers of a database tool only filter and which enforce, knows what their SQL parser does with syntax it cannot model, and tests a validator, including what it allows, with no database running.
>
> **After reading, you will be able to:**
>
> - Build a read-only database tool whose refusals happen before any connection is acquired.
> - Separate the layers that only filter a statement from the two that enforce, and say which of a server's layers is which.
> - Refuse the syntax a SQL parser quietly gave up on, instead of trusting a check that never ran.
> - Test a security layer with no database at all, including the cases it deliberately allows.

![Five stacked layers between a model's SQL and the data. The top three sit in the server, drawn with dashed outlines and labeled Filter: a parse and tree walk with sqlglot, a sixteen-name function blocklist matched on the tree, and a required LIMIT capped at one thousand rows. The bottom two sit in PostgreSQL, drawn with heavy solid outlines and labeled Enforcement: the login role mcp_readonly granted SELECT and nothing else, and a thirty-second statement timeout set on the role. Beside every layer is the limit of what it can stop.](diagrams/01-defense-in-depth.svg)

*Five layers. Three of them are opinions about a string, and two of them live in the database.*

---

## 1. The question: what makes read access to a database defensible?

A team wants to ask questions of the company database in plain English. The data is in PostgreSQL, the questions change every day, and writing a tool per question stops scaling after about four questions. So the server exposes one tool that takes SQL, and the model writes the SQL.

The fear everyone names first is `DROP TABLE`. It is the wrong fear. A model that has been told to answer questions does not usually reach for data definition language (DDL) on its own, and when it does, the validator's root-type check in section 6 catches it. The failures that actually happen are quieter.

A query that returns every row of a customers table, because nobody said not to, and now four thousand email addresses are in a conversation transcript. A statement that parses as a harmless `SELECT` and carries a `DELETE` inside a common table expression (CTE). A join with no useful index that pins a production replica for nine minutes. A tool argument interpolated straight into a statement, which is the oldest injection in the book wearing a new hat. And, the one this post spends the most time on, a check that appeared to run and did not.

So the question this post answers is: **what makes giving a model read access to a PostgreSQL database defensible, and which layer is the control rather than the filter?** The design rule that answers it is one sentence: **the server decides what "read" means, and the database enforces it.** Two clauses, and the second is doing more work than the first.

Two disclaimers before the code. The first is that this project is a teaching artifact pointed at a seeded database in a container, not something to attach to a production replica on a Friday. The second is that everything here is about *what the server allows*. It is not a defense against prompt injection reaching the model in the first place; [post 19](../19-security/index.md) covers that separately. Simon Willison's "lethal trifecta" names the combination this read tool sits inside: private data, exposure to untrusted content, and a way to send data out. A tool that can read private data and can be steered by untrusted text is dangerous no matter how good its SQL validator is.

## 2. Architecture

Five modules and one server instance for the read path. The split is not tidiness; it is what lets the validator have no database dependency, which in turn is what lets it be tested exhaustively in milliseconds.

```text
code/13-postgres-analyst/
├── docker-compose.yml          a throwaway PostgreSQL 17, loopback only
├── .env.example                the two URLs and the write switch
├── sql/
│   ├── 001-seed.sql            three tables with real foreign keys
│   └── 002-readonly-role.sql   the two login roles. The real control.
├── src/pg_analyst/
│   ├── app.py                  the MCPServer instance and the lifespan
│   ├── database.py             connection pools and how one is acquired
│   ├── security.py             SQL validation. No database import at all.
│   ├── schema.py               introspection, exposed as resources
│   ├── query.py                the read tools
│   ├── audit.py                post 14
│   └── writes.py               post 14
└── tests/
    ├── test_security.py        64 tests, no database, no event loop
    └── test_server.py          in-memory protocol tests
```

[app.py](../../code/13-postgres-analyst/src/pg_analyst/app.py) holds the instance so that every other module can hang a registration off it without a circular import. Its last line, in `src/pg_analyst/app.py`:

```python
mcp = MCPServer("postgres-analyst", lifespan=lifespan)
```

Two calls earlier in that file are load-bearing and neither is obvious. The first is `load_dotenv()`, which runs at import time, before anything reads `os.environ`. An earlier version of the project never called it, so `DATABASE_URL` was only ever picked up when the host happened to inject it into the process environment, and the server looked broken on a machine where the `.env` file was perfectly correct. The second, also in `src/pg_analyst/app.py`, sends every log record to standard error:

```python
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
```

Under the stdio (standard input and output) transport, standard output *is* the protocol channel. [Post 04](../04-transports/index.md) has the full anatomy: under `mcp==2.3.0` the server points file descriptor 1 at standard error while it serves, so a flushed `print()` in a tool body lands on standard error, but text written before the transport starts, or held in Python's buffer until exit, still reaches the pipe and corrupts the JavaScript Object Notation Remote Procedure Call (JSON-RPC) stream. Logging to standard error is the habit that does not depend on any of that.

## 3. Pools, and reading configuration at call time

Opening a Transmission Control Protocol (TCP) connection and authenticating on every tool call is slow, and a model that calls a tool six times in a turn makes it slow six times. A pool fixes that by keeping a few authenticated connections open and lending them out. [database.py](../../code/13-postgres-analyst/src/pg_analyst/database.py) opens it in the lifespan, which runs once per process. The read pool, from `src/pg_analyst/database.py`:

```python
                self._read_pool = await asyncpg.create_pool(
                    url,
                    min_size=1,
                    max_size=10,
                    command_timeout=READ_TIMEOUT_SECONDS,
                )
```

`command_timeout` is a client-side ceiling of 30 seconds. Keep it, but do not mistake it for the control: it makes the tool return a clear error instead of hanging the host's interface, and it does nothing at all if the client goes away. The timeout that matters is set on the role, in section 8, and enforced by the database server.

Two details in this file are worth copying.

**Nothing reads the environment at import time.** `read_url` is a property and `writes_enabled()` is a function call, so `load_dotenv()` is guaranteed to have run first no matter what order Python imports these modules in, and a test can flip a setting without reloading anything.

**A missing `DATABASE_URL` is a warning, not an exception.** The server starts, and every tool explains what is wrong when it is called. A Uniform Resource Locator (URL) that is present but wrong is a different matter: that exception propagates out of the lifespan and the server refuses to start, which is the right behavior. `snippets/startup_modes.py` shows both. With `DATABASE_URL` empty, it prints the project's startup log (the timestamps dropped) and the result of one call:

```
startup log:
  WARNING  postgres-analyst.database: DATABASE_URL is not set. The server is starting without a read pool and every database tool will refuse to run.
  INFO     postgres-analyst: postgres-analyst ready (reads only)

query_database: resultType 'complete', isError true
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

The result is flagged as an error and carries the fix rather than a traceback, and the schema resource of section 4 reads `Schema unavailable:` followed by the same sentence. Then the script sets `DATABASE_URL`, after the import, to a port on 127.0.0.1 that nothing listens on, and opens a second client:

```
opening the client raised ConnectionRefusedError, raised through app.py:lifespan -> database.py:connect
```

The second client saw the new value without a reload, which is the first detail at work, and the server never came up, which is the second.

## 4. Schema introspection, as a resource

A model cannot write a correct query against a database it has never seen. Pasting the schema into a system prompt works until someone runs a migration, at which point it is a confident source of wrong column names.

Introspection at read time is the fix, and the primitive is a **resource**, not a tool. [Post 07](../07-resources-and-prompts/index.md) drew the line by who pulls the trigger: resources are application-driven, meaning the host or the user decides to attach them, while tools are model-controlled. A schema is context a person attaches to a conversation. It is not an action.

[schema.py](../../code/13-postgres-analyst/src/pg_analyst/schema.py) publishes three:

| Uniform Resource Identifier (URI) | What it returns |
|---|---|
| `postgres://schema` | every table and column the read-only role can see |
| `postgres://schema/{table}` | one table, for a database too large to dump whole |
| `postgres://relationships` | foreign keys, so the model joins instead of guessing |

All three return Markdown, on the project's view that a model reads a Markdown table more reliably than deeply nested JSON. A template variable is untrusted input, and `{table}` reaches the query as a bind parameter and nothing else, so the worst a hostile value can do is match no rows.

**Two things in the introspection queries are bugs waiting to happen, and an earlier version of the project had both.** The first is privilege. `information_schema.table_constraints` lists only the constraints on tables the current user owns or holds a privilege other than `SELECT` on, and `constraint_column_usage` lists only columns of tables a current role owns. The read role holds `SELECT` and nothing else, so through those views it sees no primary key and no foreign key at all. The second is naming. The views identify a constraint by schema and name, but PostgreSQL makes a foreign key's name unique only within its table, so two tables in one schema may each carry a foreign key of the same name, and a join on the name pairs each one's columns with the other's. The corrected queries read `pg_catalog.pg_constraint`, which every role can read and which names a constraint's table by its object identifier, `conrelid`. The primary-key test, in the `_COLUMNS_SQL` string of `src/pg_analyst/schema.py`:

```sql
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class cls ON cls.oid = con.conrelid
        JOIN pg_catalog.pg_namespace ns ON ns.oid = cls.relnamespace
        JOIN pg_catalog.pg_attribute att
            ON att.attrelid = con.conrelid
           AND att.attnum = ANY (con.conkey)
        WHERE con.contype = 'p'
          AND ns.nspname::text = c.table_schema::text
          AND cls.relname::text = c.table_name::text
          AND att.attname::text = c.column_name::text
```

The consequence of getting it wrong is worse than a wrong row count. The output of this query is a document whose entire job is telling a model which column identifies a row, so a missing or false primary key becomes a wrong `WHERE` clause in every query that follows. The foreign-key query joins `pg_constraint` to the table a key belongs to through `conrelid` and to the table it points at through `confrelid`, so a shared name cannot pair the wrong columns. Both resources need a database, so their tests are among the twelve that skip without one, and the job in section 11 prints both.

The `::text` casts are not decoration either. `information_schema` columns are the `sql_identifier` domain, and comparing one to a bare placeholder leaves PostgreSQL inferring a domain type for the parameter. Casting the parameter side pins it.

One more habit, cheap and worth it. Every value that reaches a Markdown cell goes through `_escape()` in `src/pg_analyst/schema.py`, which ends:

```python
    return str(text).replace("|", "\\|")
```

Table and column names are data, and data from a database an untrusted party can write to is untrusted input. An unescaped pipe lets that party forge extra rows in a table the model is reading as ground truth.

## 5. The query tool, and the shape of a refusal

Wire format before the software development kit (SDK) call, as always. A `tools/call` for this server is an ordinary JSON-RPC request. There is no `initialize` and no session in revision 2026-07-28, so every request carries its own protocol revision and its own declared capabilities in `_meta`. `snippets/refusal_on_the_wire.py` records what the SDK's client hands its transport for one call, which adds `jsonrpc` and `id` around it:

```json
{
  "method": "tools/call",
  "params": {
    "name": "query_database",
    "arguments": {
      "sql": "WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1"
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mcp",
        "version": "0.1.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

`clientInfo` is the SDK's default, and the specification makes it a **SHOULD**, not a **MUST**. The tool that answers is in [query.py](../../code/13-postgres-analyst/src/pg_analyst/query.py). Its registration, in `src/pg_analyst/query.py`:

```python
@mcp.tool(
    title="Run a read-only SQL query",
    annotations=READ_ONLY,
)
async def query_database(sql: str) -> Annotated[CallToolResult, QueryResult]:
```

The return annotation is the SDK's spelling for a tool that sometimes builds its own result: `QueryResult` still supplies the output schema, a successful read returns a plain `QueryResult`, and a refusal returns a `CallToolResult`. After the docstring, the first thing the body does:

```python
    try:
        plan = validate_read_query(sql, max_rows=MAX_ROWS)
    except SecurityError as exc:
        # Refused. No pool was touched, no connection was acquired.
        log.info("query refused: %s", exc)
        return tool_error(
            QueryResult(
                ok=False,
                executed_sql="",
                row_count=0,
                error=f"Refused by the SQL validator: {exc}",
            )
        )
```

`tool_error()`, in `src/pg_analyst/app.py`, is the one place every refusal in the project goes through. It keeps the dataclass as the structured body and sets the flag:

```python
    body = asdict(result)
    return CallToolResult(
        is_error=True,
        structured_content=body,
        content=[TextContent(type="text", text=json.dumps(body, indent=2))],
    )
```

![A left-to-right pipeline for one query_database call. A strip of six chips names the checks in order: one statement, root is a query, no nested mutation, no SELECT INTO, no blocked function, and LIMIT then clamp. Failing any of them drops to a refusal carrying ok false and an empty executed_sql that never touches a pool. Past the checks, the statement is regenerated from the parse tree, a connection is acquired from the read-only pool, the rows are fetched, values are coerced so NUMERIC becomes a string, and a structured result is returned. A second failure branch shows PostgreSQL itself refusing with permission denied or a statement timeout.](diagrams/02-query-pipeline.svg)

*Validation happens before a pool is touched, and what runs is regenerated from the parse tree rather than taken from the string that arrived.*

Three decisions in that pipeline are worth defending.

**Validation runs before anything is acquired.** A refused statement never gets near a connection. That is not only tidy; it is what makes most of the test suite able to run with no database at all.

**A refusal is a result, not a protocol error.** The specification's Tools page splits protocol errors from tool execution errors so that a model can read a failure and correct itself: clients **SHOULD** pass tool execution errors to the model, while protocol errors are less likely to lead to a successful recovery. Here is the refusal the script printed, as it came back:

```
resultType: "complete"
isError: true
_meta: {"io.modelcontextprotocol/serverInfo": {"name": "postgres-analyst", "version": ""}}
structuredContent:
{
  "ok": false,
  "executed_sql": "",
  "row_count": 0,
  "columns": [],
  "rows": [],
  "notice": "",
  "error": "Refused by the SQL validator: A nested DELETE appears inside this statement. A CTE or subquery cannot carry a mutation past this layer."
}
content[0] is one text block carrying the same object: True
```

The `_meta` line is where every result carries `serverInfo`; its `version` is empty because the project never passes one to `MCPServer`. The flag above it follows the specification's schema comment on `isError`: an error that originates from the tool **SHOULD** be reported inside the result with `isError` set to true. A host that uses `isError` as its failure signal sees a failure, and the model still reads the `error` sentence, because `structuredContent` has the same shape as a page of rows with `ok: false` in it.

**The caller is told what actually ran.** `executed_sql` is not the string that arrived; it is the statement regenerated from the parse tree. Section 6 explains why, and the field exists so the difference is never a surprise.

The second tool, `sample_table`, exists because "show me what is in this column" does not deserve a hand-written query. It is also where the injection lives, so it gets its own construction. PostgreSQL will not accept an identifier as a bind parameter, so a table name has to reach the statement text somehow. The safe move is to never let the caller's string get there. The body of `resolve_table_name` in `src/pg_analyst/schema.py`:

```python
    row = await conn.fetchrow(_TABLE_EXISTS_SQL, INTROSPECTED_SCHEMA, table)
    if row is None:
        raise LookupError(
            f"There is no table named '{table}' in the {INTROSPECTED_SCHEMA} schema."
        )
    return row["table_name"]
```

The caller's string is looked up in `information_schema.tables` **as a bind parameter**, and the query is then built from the name the catalog handed back. The catalog is the allowlist, and it cannot contain a name that is not really a table. The project's injection test sends three names, including `users"; DROP TABLE users; --`, and asserts that each comes back with `ok: false`, "no table named" in the error, and an empty `executed_sql`; it needs a database, so it is among the tests that skip without one. The identifier is then quoted by `sqlglot` rather than by an f-string, and the limit is coerced with `max(1, min(int(limit), MAX_SAMPLE_ROWS))`, where `MAX_SAMPLE_ROWS` is 100, so neither value can carry syntax.

## 6. The validating SQL layer, and its honest limits

[security.py](../../code/13-postgres-analyst/src/pg_analyst/security.py) has no database import. Every function in it is a pure function of a string, which is why section 10 can test it exhaustively without PostgreSQL anywhere.

It parses with `sqlglot` at `dialect="postgres"` and walks the resulting abstract syntax tree (AST). An earlier version checked the text instead, and the difference is not a preference. Two examples show what a grammar buys, and they fail in opposite directions. `snippets/validator_cases.py` runs both, along with every other statement in this section, against `sqlglot` 30.13.0.

**A substring search rejects a legitimate query.** The earlier blocklist check was `if func in sql.lower()`. Under that rule this is refused:

```sql
SELECT * FROM users WHERE full_name = 'pg_sleep' LIMIT 1
```

Nothing is being called. `pg_sleep` there is a string literal, and a user whose surname collides with a PostgreSQL function is not an attacker. The tree knows the difference, so the walk allows it, and the script confirms that the regenerated statement is identical to the input.

**A leading-keyword check accepts a mutation.** PostgreSQL genuinely executes this, and it is a `Select` at the root:

```sql
WITH doomed AS (DELETE FROM users RETURNING *) SELECT * FROM doomed LIMIT 1
```

Walking the tree finds the node types the script prints, and `Delete` in that list is a refusal:

```
root type  = Select
walk types = ['CTE', 'Delete', 'From', 'Identifier', 'Limit', 'Literal', 'Returning', 'Select', 'Star', 'Table', 'TableAlias', 'With']
```

The checks, in the order `validate_read_query` runs them:

| Check | What it refuses |
|---|---|
| `parse_single_statement` | more than one statement, unparseable text, an opaque node |
| root type | anything that is not `Select`, `Union`, `Intersect`, or `Except` |
| `reject_nested_mutations` | any of thirteen mutation node types below the root |
| `into` | `SELECT ... INTO`, which is a write wearing a `SELECT`'s clothes |
| locking clause | `FOR UPDATE`, `FOR SHARE`, and the other row locks, anywhere in the tree |
| `check_dangerous_functions` | 16 names, matched against called functions rather than text |
| `enforce_limit` | a missing `LIMIT`, a non-literal `LIMIT`, and anything over 1,000 rows |

Five refusals, as the script prints them:

```
'SELECT 1 LIMIT 1; DROP TABLE users;'
  -> Submit one statement at a time. This parsed as 2 statements, which is the shape of
     a chained-statement attack.
'SELECT * FROM users'
  -> Every query must carry a LIMIT of at most 1000 rows. Add 'LIMIT n' to the
     statement.
'SELECT pg_sleep(30) LIMIT 1'
  -> Function 'pg_sleep' is not allowed. It can stall a backend, reach the server's file
     system, or interfere with other sessions.
'SELECT * INTO evil FROM users LIMIT 1'
  -> SELECT ... INTO creates a new table. Refused on the read path.
'SELECT * FROM users LIMIT 1 FOR UPDATE'
  -> A locking clause (FOR UPDATE, FOR SHARE) takes row locks, which is not a read.
     Refused on the read path.
```

A row lock blocks writers for as long as the transaction stays open. PostgreSQL refuses one under the read role anyway, since every locking clause needs `UPDATE` privilege on the table and the role holds none; the validator's refusal gives the model a sentence instead of a database error. Two rules in that table deserve a sentence each.

**A missing `LIMIT` is refused rather than silently added.** The model is perfectly capable of writing `LIMIT 50`, and a query that quietly returns a different result set than the one that was written is worse for everyone than an error naming the fix. An over-large `LIMIT` *is* clamped rather than refused, because the intent is unambiguous, and the caller is told (`query_database` adds a `notice` saying the limit was reduced). An ungrouped aggregate is exempt, because `SELECT count(*) FROM users` returns one row by construction; a grouped aggregate returns one row per group and gets no exemption:

```
'SELECT * FROM users LIMIT 100000'
  -> allowed, runs as 'SELECT * FROM users LIMIT 1000'  limit=1000 clamped=True
'SELECT count(*) FROM users'
  -> allowed, runs as 'SELECT COUNT(*) FROM users'  limit=None clamped=False
'SELECT country, count(*) FROM users GROUP BY country'
  -> Every query must carry a LIMIT of at most 1000 rows. Add 'LIMIT n' to the
     statement.
```

**The statement that runs is regenerated from the tree.** The last line of `render()` in `src/pg_analyst/security.py`:

```python
    return statement.sql(dialect="postgres", comments=False)
```

`comments=False` is the load-bearing argument: without it `sqlglot` copies comments through, and a comment is a place to hide text from somebody skim-reading a confirmation prompt, which matters more in [post 14](../14-database-writes/index.md), where a human reads the statement before it runs. Regeneration also means text the parser ignored cannot ride along:

```
'SELECT   1 /* ; DROP TABLE users */ LIMIT 1  '
  -> allowed, runs as 'SELECT 1 LIMIT 1'  limit=1 clamped=False
```

Be clear-eyed about what that trade buys. It removes one bug class, smuggling through parts of the string the checks did not look at, and it introduces another, a `sqlglot` generation bug that changes the meaning of a statement. The trade is worth making, and returning `executed_sql` to the caller is the price of admission.

## 7. When the parser gives up without saying so

This is the finding that changed how the whole project is built, and it is the strongest argument in this series against treating a parser as a security boundary.

`sqlglot` does not always raise on syntax it cannot model. Text it rejects outright raises a `ParseError`, but for many statements it does not model, the four below among them, it wraps the raw text in an opaque `exp.Command` node, logs a warning, and carries on. The warning goes to the logging system, which in this server means standard error, where nothing in a tool body ever sees it. `snippets/validator_cases.py` parses four such statements and prints, for each, the warning, the tree, and what the tree-reading checks make of it. Two of the four:

```
'DO $$ BEGIN PERFORM pg_sleep(30); END $$'
    sqlglot logged    : 'DO $$ BEGIN PERFORM pg_sleep(30); END $$' contains unsupported syntax. Falling back to parsing as a 'Command'.
    type(tree)        = Command
    walk types        = ['Command', 'Literal']
    function_names()  = []
    the blocklist and nested-mutation checks raise nothing
    a read root?      = False
    enforce_limit()   : refuses, Every query must carry a LIMIT of at most 1000 rows. Add 'LIMIT n' to the statement.
'EXPLAIN ANALYZE DELETE FROM users'
    sqlglot logged    : 'EXPLAIN ANALYZE DELETE FROM users' contains unsupported syntax. Falling back to parsing as a 'Command'.
    type(tree)        = Command
    walk types        = ['Command', 'Literal']
    function_names()  = []
    the blocklist and nested-mutation checks raise nothing
    a read root?      = False
    enforce_limit()   : refuses, Every query must carry a LIMIT of at most 1000 rows. Add 'LIMIT n' to the statement.
```

`VACUUM FULL users` and `CALL do_it()` print the same tree. Read the first one again. The statement contains `pg_sleep`, which is the first name on the blocklist. The blocklist check walks the tree collecting function nodes. There are none, because the entire body of the `DO` block is one string literal hanging off an opaque node. The check ran, found nothing, and returned cleanly. The second is worse: `EXPLAIN ANALYZE` executes the statement it measures, so that `DELETE` would really run, and the nested-mutation check cannot see it either.

**That is the shape of the problem.** Every check in the module walks the parse tree, so a node the parser could not model is a node none of the checks could look inside. Nothing raised, nothing was logged where the tool could see it, and every check that works by reading the tree passed. What refuses these statements has to be their shape, not their contents.

The last two lines of each entry show what else would have stopped them on this server's read path: the root-type check, because a `Command` is none of the four read roots, and the `LIMIT` rule, because an opaque node has no `LIMIT` to find. Neither is about the node being opaque. A root check written the other way round, listing the root types to refuse, would let a `Command` through, and the `LIMIT` rule would be the last thing standing, refusing for a reason that has nothing to do with safety; every check that reads the tree for its contents has already passed it. So the project refuses the opaque node explicitly, in `parse_single_statement` in `src/pg_analyst/security.py`, where the tree is first obtained and before either the read path or the write path looks at it:

```python
    if isinstance(statement, exp.Command):
        raise SecurityError(
            "The parser could not model this statement and fell back to an "
            "opaque command node, so none of the safety checks could look "
            "inside it. Refused."
        )
```

`exp.Command` is also in the `_MUTATIONS` tuple, so an opaque node nested inside an otherwise valid statement is refused too. The same script ends by passing all four statements to the validator, and `snippets/refusal_on_the_wire.py` sends them to `query_database` over the in-memory client; every one comes back `ok: false` with an empty `executed_sql` and one error:

```
error: Refused by the SQL validator: The parser could not model this statement and fell back to an opaque command node, so none of the safety checks could look inside it. Refused.
```

The cost is real and should be accepted deliberately: **valid PostgreSQL gets rejected.** `VACUUM`, `CALL`, `EXPLAIN`, and a `DO` block are all legal, and this server refuses all four, which is the correct trade for a tool a model drives. The rule generalizes past `sqlglot`: when a parser degrades instead of failing, every check downstream of it degrades silently with it. Find out what the parser does with input it does not understand, and if the answer is "returns something opaque", refuse the opaque thing.

![Two panels. The left panel shows a statement selecting every email address and full name from the users table with a limit of one thousand passing all six checks and being allowed, labeled a thousand rows of personally identifiable information, with the caption that nothing here is a bug. The right panel shows an anonymous DO block calling pg_sleep parsed into an opaque Command node with only a string literal below it, the three tree-reading checks marked with flat dashes because there is nothing to inspect, and two endings side by side: the blocklist holds pg_sleep and saw nothing, while this server refuses the opaque node on sight.](diagrams/03-what-the-validator-cannot-see.svg)

*Left, every check passes and the query is a data breach. Right, every check that reads the tree passes, because not one of them could see inside.*

## 8. The database role is the real control

Everything above is a filter. A filter is a thing that can be wrong, and each of these can be wrong in a specific way:

- The blocklist names the sixteen functions that were dangerous when it was written. PostgreSQL ships new ones and extensions ship more, and the list does not update itself.
- Names lie. A table can be a view, and a view can sit on a `SECURITY DEFINER` function that does whatever it likes with the privileges of its owner. The parser sees an identifier.
- Cost is invisible. A three-way self join with no useful index is a denial of service and looks exactly like a cheap query in the tree.
- The validator has no concept of "sensitive". Column and row policy is a database concern.

What holds when all of that is wrong is [sql/002-readonly-role.sql](../../code/13-postgres-analyst/sql/002-readonly-role.sql). This file, not `security.py`, is the security control, and the difference is that a grant is not a heuristic. Its statements for the read role, with the comments and the role's creation omitted:

```sql
GRANT CONNECT ON DATABASE analytics TO mcp_readonly;
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO mcp_readonly;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM mcp_readonly;

REVOKE TEMPORARY ON DATABASE analytics FROM PUBLIC;

ALTER ROLE mcp_readonly SET default_transaction_read_only = ON;
ALTER ROLE mcp_readonly SET statement_timeout = '30s';
ALTER ROLE mcp_readonly SET idle_in_transaction_session_timeout = '60s';
```

Five of those ten statements are ordinary. Five are the post.

**`GRANT SELECT` and nothing else** is the one that cannot be argued with. The role holds no `INSERT`, `UPDATE`, or `DELETE` privilege, so a write that slipped past every filter fails with a permission error, and no statement the session sends can grant the privilege back.

**`default_transaction_read_only = ON`** makes every transaction the role opens read-only by default, so a stray `INSERT` fails with `cannot execute INSERT in a read-only transaction`, as the comment in the role file puts it. **`statement_timeout = '30s'`** is the answer to `pg_sleep` reached through a wrapper the blocklist has never heard of, to the Cartesian join, and to every other query whose cost the parser cannot see. It is enforced by the database server, so it survives a client that has stopped listening; the `command_timeout` on the pool does not. Both are role defaults, though, not grants: a session may change either with `SET` or `set_config()`. On this server it cannot, because the validator refuses `SET` as a non-`SELECT` root and `set_config` is on the blocklist. Here a filter guards an enforcement layer, which is why the grant, not the settings, is the line that holds when everything else is wrong.

**`REVOKE CREATE ON SCHEMA public FROM PUBLIC`** is the one people skip. On PostgreSQL 14 and earlier, `PUBLIC` holds `CREATE` on the `public` schema by default, which means "read-only" quietly includes "can make new tables". The compose file runs PostgreSQL 17, where that default is already gone, which is exactly why the line is easy to leave out of a script that later meets an older server. **`REVOKE TEMPORARY ON DATABASE analytics FROM PUBLIC`** closes the same gap for temporary tables: `PUBLIC` holds `TEMPORARY` on every new database, so a session that had switched `default_transaction_read_only` off could otherwise create them.

The suite has one test whose only job is to prove this layer exists independently of the Python. It reaches past the validator and uses the pool directly, the way a bug in the query path would. From `tests/test_server.py`:

```python
@needs_database
async def test_the_read_role_refuses_a_write_that_somehow_got_through():
    """Reach past the validator and call the pool directly, the way a bug in
    the query path would. This is the layer that has to hold when the parser
    does not, and it is the whole argument of post 13."""
    import asyncpg

    from pg_analyst.database import db

    async with Client(mcp):
        with pytest.raises(asyncpg.PostgresError) as excinfo:
            async with db.read_connection() as conn:
                await conn.execute("DELETE FROM orders WHERE id = 1")

    message = str(excinfo.value).lower()
    assert "read-only" in message or "permission denied" in message
```

That test needs a live PostgreSQL, so it is one of the twelve that skip when `DATABASE_URL` is unset. Section 11 shows how to start the container in [docker-compose.yml](../../code/13-postgres-analyst/docker-compose.yml) and run it.

## 9. Structured results

A tool that returns a formatted string is a tool the model has to re-parse. Every tool here returns a dataclass, and the SDK turns the return annotation into an `outputSchema` and the returned object into `structuredContent`, with one text block carrying the same object as JSON for a client that reads only `content`. On the wire the field is `structuredContent`; the Python SDK exposes it as `structured_content`. The read tool's result, from `src/pg_analyst/query.py`:

```python
@dataclass
class QueryResult:
    """The result of a read.

    The class-body annotations are load-bearing. A class that only assigns
    attributes in `__init__` has no type hints for the SDK to read, and the
    tool ships with `outputSchema: null` and no warning at all.
    """

    ok: bool
    executed_sql: str
    row_count: int
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    notice: str = ""
    error: str = ""
```

**The class-body annotations are load-bearing.** [Post 06](../06-tools-in-depth/index.md) has the full failure and [post 12](../12-testing-and-debugging/index.md) has the test that catches it, which is why every tool in this project is asserted to publish an output schema. Returning a formatted string instead gives up both the schema and the structured object.

The other half of structured output is coercion. PostgreSQL hands back types JSON has no opinion about, and `jsonable()` in `src/pg_analyst/query.py` maps each one. The interesting branch is the first after the primitives:

```python
    if isinstance(value, decimal.Decimal):
        # str, not float. A NUMERIC column is exact and float is not, and
        # silently losing precision on a money column is a bad way to find out.
        return str(value)
```

Returning `str` keeps a money column exact; returning `float` loses precision quietly. `snippets/published_surface.py` passes one value of each awkward type through `jsonable()`:

```
  Decimal      -> '639.98'
  datetime     -> '2026-07-24T09:30:00+00:00'
  date         -> '2026-07-24'
  timedelta    -> '2 days, 3:00:00'
  UUID         -> '12345678-1234-5678-1234-567812345678'
  IPv4Address  -> '192.0.2.10'
  bytes        -> '<16 bytes>'
```

Dates and times become International Organization for Standardization (ISO) 8601 strings, an interval becomes Python's own rendering of it, universally unique identifiers (UUIDs) and Internet Protocol (IP) addresses become strings, and `bytes` becomes a byte count rather than a wall of escaped characters.

The same script reads the published surface back through an in-memory client:

```
tool query_database
  input properties : ['sql']
  output schema    : QueryResult, required ['ok', 'executed_sql', 'row_count', 'columns', 'rows']
  annotations      : readOnlyHint=True destructiveHint=False idempotentHint=True openWorldHint=False
tool sample_table
  input properties : ['table', 'limit']
  output schema    : TableSample, required ['ok', 'table', 'executed_sql', 'row_count', 'columns', 'rows']
  annotations      : readOnlyHint=True destructiveHint=False idempotentHint=True openWorldHint=False
tool write_database
  input properties : ['sql', 'dry_run']
  output schema    : WriteResult, required ['status', 'operation', 'table', 'executed_sql', 'affected_rows', 'audit_id', 'detail']
  annotations      : readOnlyHint=False destructiveHint=True idempotentHint=False openWorldHint=False
resources: ['postgres://audit', 'postgres://relationships', 'postgres://schema']
templates: ['postgres://schema/{table}']
```

`write_database` and `postgres://audit` belong to post 14; with `ENABLE_WRITES` off, the write tool reports itself disabled and asks nobody anything. The annotations are honest and they are also not enforcement. The Tools page says clients **MUST** treat annotations as untrusted unless they come from a trusted server. What makes the two read tools read-only is that they acquire from a pool whose role holds `SELECT` and nothing else.

## 10. Testing the security layer, including what it allows

The suite runs from inside `code/13-postgres-analyst/`:

```bash
uv run --extra dev pytest -q
```

With no database configured it ends:

```
........................................................................ [ 71%]
.................ssssssssssss                                            [100%]
89 passed, 12 skipped in 2.42s
```

Split by file, because the split is the design (the wall-clock times vary by machine):

```
$ uv run --extra dev pytest tests/test_security.py -q
64 passed in 1.88s

$ uv run --extra dev pytest tests/test_server.py -q
25 passed, 12 skipped in 2.23s
```

The sixty-four in `test_security.py` need no database, no event loop, and no server, because the validator is a pure function of a string. The twelve skips are the tests in `test_server.py` that need a live PostgreSQL; the other twenty-five drive the server in memory, refusals included, because a refusal never reaches a pool.

The part worth copying is the last section of [test_security.py](../../code/13-postgres-analyst/tests/test_security.py), which is four tests asserting things the validator **allows**. The first, from `tests/test_security.py`:

```python
def test_a_full_table_read_of_sensitive_data_is_perfectly_valid_sql():
    """The validator has no concept of "sensitive". Column-level policy is a
    database concern, not a parser concern."""
    plan = validate_read_query("SELECT email, full_name FROM users LIMIT 1000")
    assert plan.limit == 1000
```

The other three are `test_a_where_clause_that_matches_everything_satisfies_the_where_rule` (`WHERE true` satisfies the write path's rule), `test_an_expensive_query_looks_exactly_like_a_cheap_one` (a three-way self join with `LIMIT 1000` passes), and `test_the_validator_cannot_tell_a_table_from_a_view`. None of those are bugs. They are the boundary of what a parser can know, written down where the next person to read this module will see them. A security layer whose limits are not recorded anywhere is a security layer people over-trust, and these four tests are the cheapest documentation in the project: they cannot go stale, because they run.

The first one is the important one. `SELECT email, full_name FROM users LIMIT 1000` passes every check, returns up to a thousand rows of personally identifiable information (the seeded table holds eight), and is indistinguishable from legitimate analytics. Where that matters, the answer is a restricted view, PostgreSQL row-level security, or a role that cannot see the column. It is not another regular expression.

## 11. Make it run: the project, the scripts, and the database

The project is the post's code. From inside `code/13-postgres-analyst/`:

```bash
uv sync --extra dev                  # once: the environment, with the test tools
uv run --extra dev pytest            # 89 passed, 12 skipped without a database
uv run python -m pg_analyst          # stdio, what a desktop host spawns; Ctrl+C stops it
```

To give it a database, and to run the twelve skipped tests:

```bash
docker compose up -d                 # PostgreSQL 17 on 127.0.0.1:5432; seeds the tables, then creates both roles
cp .env.example .env                 # DATABASE_URL names mcp_readonly; writes stay off
uv run --extra dev pytest            # the needs_database tests now run instead of skipping
```

Four scripts under `snippets/` print every transcript quoted above. Each runs from the series root with no arguments in a few seconds, imports the project, and needs no database: the three that drive the server connect an in-memory client to it, as the project's tests do, with `DATABASE_URL` forced empty so that a `.env` on the machine changes nothing.

```bash
uv run python posts/13-database-analyst/snippets/startup_modes.py        # section 3: a missing URL, then a wrong one
uv run python posts/13-database-analyst/snippets/refusal_on_the_wire.py  # sections 5 and 7: the request, the refusal, the four opaque statements
uv run python posts/13-database-analyst/snippets/validator_cases.py      # sections 6 and 7: the validator on every statement quoted
uv run python posts/13-database-analyst/snippets/published_surface.py    # section 9: tools, schemas, annotations, resources, jsonable()
```

The series root's environment pins the parser and the database driver beside the SDK, `sqlglot` at 30.13.0 as the project's lock does, so the scripts need nothing from the project's own environment. `refusal_on_the_wire.py` reads the outgoing request by wrapping a private method of the SDK's in-memory dispatcher, a seam of `mcp==2.3.0` that may move in a later release; it only observes.

One more script needs the container. `snippets/jobs/against_postgres.py` reads the `users` schema resource and the relationships resource, sends a valid and a clamped query, runs `sample_table` on a real table and on the three hostile names, and sends a `DELETE` straight to the read pool twice, the second time after switching `default_transaction_read_only` off in the session, so that the grant alone has to refuse it. With the container up and the `.env` in place, from the series root (a few seconds; it changes nothing):

```bash
uv run python posts/13-database-analyst/snippets/jobs/against_postgres.py
```

No PostgreSQL was available when this page was checked, so that script's output is not quoted; what the page says about PostgreSQL's own refusals is what the project's database tests assert.

## 12. What can go wrong?

**Every tool answers that `DATABASE_URL` is not set.** The server started without a read pool: the `.env` was never copied from `.env.example`, or the host's configuration block launched the server without an `env` entry for it. The startup log's `WARNING` line says the same thing, on standard error, where the host keeps its logs.

**The server dies on launch.** `DATABASE_URL` is set and wrong, or the container is not up: the lifespan raises (`ConnectionRefusedError` for a port with nothing listening) and the host reports a server that exited. `docker compose ps` should show the container healthy before the server starts.

**The test run fails instead of skipping.** With a `.env` in place the twelve database tests no longer skip, so a stopped container turns them into failures from the same connection error. Either start the container or move the `.env` aside.

**An edit to `sql/` has no effect.** The image runs the files in `sql/` only when its data volume is first created, so after changing a grant the volume has to go: `docker compose down -v && docker compose up -d`.

**The read-role test fails with `DID NOT RAISE`.** `DATABASE_URL` names a role that is allowed to write, typically the `postgres` superuser from the compose file, so the `DELETE` succeeded and one seeded order is gone. Every filter still works and the control is not there; point `DATABASE_URL` at `mcp_readonly`.

**A model's query keeps coming back refused.** That is the design working: a missing `LIMIT`, a `VACUUM`, or an `EXPLAIN` is refused with a sentence naming the rule for the model to act on. A refusal that recurs for a legitimate need calls for a dedicated tool, not a looser validator.

## Common pitfalls

1. **Treating the SQL validator as the security boundary.** It is a filter that reduces how often the database has to say no; the role is the boundary, so build `002-readonly-role.sql` first.
2. **Trusting a parser that degrades instead of raising.** `sqlglot` answers much of the syntax it cannot model with an opaque `exp.Command` node and a logged warning, so every tree-walking check runs, finds nothing, and passes; refuse opaque nodes explicitly.
3. **Blocking on substrings instead of on the parse tree.** `if "pg_sleep" in sql.lower()` refuses a customer whose name is `pg_sleep` and misses the same function called through a wrapper, getting both directions wrong at once.
4. **Only checking the first keyword.** A data-modifying CTE is a `Select` at the root and PostgreSQL really does execute it; walk the whole tree and refuse mutation nodes anywhere below the root.
5. **Reading constraints through `information_schema` as the read role.** Its constraint views hide every table the role holds only `SELECT` on, and they join on a name PostgreSQL keeps unique only per table for a foreign key; read `pg_constraint`, which every role can see and which names the table itself.
6. **Interpolating a table name from a tool argument.** PostgreSQL will not bind an identifier, so look the caller's string up in `information_schema.tables` as a parameter and build the query from the catalog's spelling, never from the caller's.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The split between protocol errors and tool execution errors, the `isError` rule quoted in section 5, and the rule that annotations are untrusted. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Resources"*, revision 2026-07-28. Why the schema is application-driven context and not a tool. <https://modelcontextprotocol.io/specification/draft/server/resources>
- PostgreSQL roles and privileges. Login roles, `GRANT`, and what a role may and may not do. <https://www.postgresql.org/docs/current/user-manag.html>
- PostgreSQL documentation, *Client Connection Defaults*. `statement_timeout` and `default_transaction_read_only` as role settings a session can override. <https://www.postgresql.org/docs/current/runtime-config-client.html>
- `sqlglot`. Release 30.13.0 produced every parse quoted here, including the `Command` fallback. <https://github.com/tobymao/sqlglot>
- `asyncpg`. The pool and its `command_timeout`. <https://magicstack.github.io/asyncpg/current/>
- Willison, S. *"The lethal trifecta for AI agents"* (2025-06-16). The post that named the trifecta. <https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/>
- Willison, S. *"Supabase MCP can leak your entire SQL database"* (2025-07-06). The lethal trifecta of private data, untrusted content, and a way out, applied to a database MCP server, which is the risk this server's read tool sits inside. <https://simonwillison.net/2025/Jul/6/supabase-mcp-lethal-trifecta/>
- MCP Python SDK, release 2.3.0. `MCPServer`, `Client`, and the output schema built from a return annotation. <https://github.com/modelcontextprotocol/python-sdk>
- [code/13-postgres-analyst/](../../code/13-postgres-analyst/). The complete project, with the tests quoted in sections 8 and 10.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 14 - Project 1: Writes, transactions, and an audit trail](../14-database-writes/index.md):** the same server given `INSERT`, `UPDATE`, and `DELETE`, with a human on the critical path and an audit row that commits inside the same transaction as the change.
- **[Post 19 - Security: the attacks the protocol does not stop](../19-security/index.md):** the attack classes a good SQL validator does nothing about, starting with the prompt injection that chose the query in the first place.
