# postgres-analyst

The server built across posts 13 and 14. It gives a model read access to a
PostgreSQL database, and then, carefully, write access.

The argument the code is here to make: **the server, not the model, decides
what "read" means**, and the layer that actually enforces that decision is the
database role, not the Python.

| File | What it is for | Post |
|---|---|---|
| `src/pg_analyst/app.py` | the server instance, `load_dotenv()`, stderr logging, the lifespan | [13](../../posts/13-database-analyst/index.md) |
| `src/pg_analyst/database.py` | two connection pools, two roles | [13](../../posts/13-database-analyst/index.md), [14](../../posts/14-database-writes/index.md) |
| `src/pg_analyst/security.py` | the `sqlglot` validator, and what it cannot see | [13](../../posts/13-database-analyst/index.md) |
| `src/pg_analyst/schema.py` | introspection, exposed as resources | [13](../../posts/13-database-analyst/index.md) |
| `src/pg_analyst/query.py` | `query_database`, `sample_table` | [13](../../posts/13-database-analyst/index.md) |
| `src/pg_analyst/writes.py` | `write_database`, gated by a human | [14](../../posts/14-database-writes/index.md) |
| `src/pg_analyst/audit.py` | the audit trail, and the `postgres://audit` resource | [14](../../posts/14-database-writes/index.md) |
| `sql/002-readonly-role.sql` | **the actual security control** | [13](../../posts/13-database-analyst/index.md) |

## Requirements

Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), and a PostgreSQL to
point at. `docker-compose.yml` provides one.

## Install and run

```bash
uv sync --extra dev
docker compose up -d          # seeds the tables, then creates both roles
cp .env.example .env          # edit if you changed the passwords
uv run python -m pg_analyst   # stdio, what a desktop host spawns
```

`uv run python -m pg_analyst --http` serves Streamable HTTP on 127.0.0.1:8000
instead. Loopback by default, deliberately: this process holds live database
credentials.

Under stdio, **stdout is the protocol channel**. A stray `print()` anywhere in
this package would be parsed as a JSON-RPC frame and break the connection. All
logging goes to stderr, which is set up in `app.py`.

## Defense in depth, in the order the layers run

```
    model writes SQL
        |
    [1] ENABLE_WRITES ..... a write path that was never switched on
        |
    [2] sqlglot AST ....... single statement, no nested mutation, no
        |                   blocked function, mandatory LIMIT, WHERE on
        |                   UPDATE and DELETE
        |
    [3] a human ........... reads the exact statement and says yes or no
        |                   (write path only)
        |
    [4] the role .......... SELECT and nothing else, in a read-only
        |                   transaction, with a statement_timeout
        v
    PostgreSQL
```

Layers 1 to 3 are convenience and user experience. **Layer 4 is the security
boundary.** A parser can be surprised; a `GRANT` that was never issued cannot.
The module docstring in `security.py` lists, in detail, six things the
validator cannot catch, and every one of them lands on layer 4.

## Two roles, two URLs

`DATABASE_URL` names `mcp_readonly`. `DATABASE_WRITE_URL` names `mcp_writer`.
There is **no fallback** between them. If you only set `DATABASE_URL`, the
server can only read, and it says so at startup rather than failing later with
a confusing permissions error.

`ENABLE_WRITES` defaults to false. With it off, the write pool is never opened
and the audit table is never created.

## The write path

`write_database(sql, dry_run=False)` publishes exactly two arguments. A third
parameter, `approval`, is resolved by elicitation and is **stripped from the
published input schema**, so the model cannot supply its own approval. Post 08
covers the mechanism; `tests/test_server.py` asserts the parameter's absence,
which is the single most important test in the file.

The confirmation question is built only from the tool's arguments. That is not
a style preference. The SDK matches a recorded answer against a SHA-256 digest
of the exact rendered question, so a question that embedded a timestamp or a
live row count would re-render differently on the retry, never match its own
answer, and loop until `input_required_max_rounds` gave up.

Nobody is asked to confirm a dry run, a statement the validator is going to
refuse, or anything at all when `ENABLE_WRITES` is off. Prompting a human for
approval of something that was never going to run is how you train them to
click through prompts without reading them.

The audit row for a successful write is inserted **inside the write's own
transaction**, so the log cannot claim something happened that rolled back. The
row for a failed write goes on a fresh connection from the write pool, because
by then the original transaction is gone and everything written inside it went
with it.

## Test

```bash
uv run pytest
```

Two files, two levels, from post 12:

- `tests/test_security.py` is unit tests of a pure function. **No database, no
  server, no event loop.** Its last section deliberately tests things the
  validator *allows*, because a security layer whose limits are not written
  down is a layer people over-trust.
- `tests/test_server.py` connects a client straight to the server object with
  no subprocess and no socket. Most of it needs no database either, because
  validation happens before a pool is ever acquired. The rest is marked
  `needs_database` and skips when `DATABASE_URL` is unset.

Each test opens its client with `async with` in the test body rather than
taking it from a yield fixture. The client owns an anyio task group, and a task
group must be exited by the task that entered it; a yield fixture tears down
elsewhere and every test fails with a cancel-scope error.

## Connecting it to a host

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/13-postgres-analyst", "run", "python", "-m", "pg_analyst"],
  "env": {
    "DATABASE_URL": "postgresql://mcp_readonly:readonly_password@localhost:5432/analytics"
  }
}
```

Where that JSON goes differs per host, and the key it sits under differs too.
Post 23 has the matrix.

Note what is not in that `env` block. Leaving `DATABASE_WRITE_URL` and
`ENABLE_WRITES` out is how you deploy the post 13 server: read-only, by
construction, with nothing to switch off later.
