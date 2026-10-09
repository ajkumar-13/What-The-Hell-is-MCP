"""Post 13, sections 4, 5 and 8: the parts of the read path that need a real PostgreSQL.

Not run by the validator: it needs the database that
code/13-postgres-analyst/docker-compose.yml starts. Setup, from inside
code/13-postgres-analyst/:

    docker compose up -d          # seeds three tables, then creates both roles
    cp .env.example .env          # DATABASE_URL names the mcp_readonly role

Then, from the series root, in the project's environment:

    uv run --project code/13-postgres-analyst python posts/13-database-analyst/snippets/jobs/against_postgres.py

It takes a few seconds once the container reports healthy. It imports the
project, which reads DATABASE_URL from that .env, connects an in-memory client,
and prints:

1. the postgres://schema/users resource, with its primary key marked;
2. one valid query_database call, and one whose LIMIT is clamped;
3. sample_table on a real table, then on the three hostile names of the
   project's injection test;
4. the role's settings as the session sees them (SHOW), then a DELETE sent
   straight to the read pool, past the validator, and the error PostgreSQL
   returns;
5. the same DELETE after the session switches default_transaction_read_only
   off, which a session is allowed to do: the grant still refuses it.

Expected wall-clock: under ten seconds. Nothing is changed in the database;
every write attempt is refused by PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "code" / "13-postgres-analyst" / "src"))

import asyncpg  # noqa: E402
from mcp import Client  # noqa: E402

from pg_analyst import mcp  # noqa: E402  (load_dotenv() runs here)
from pg_analyst.database import db  # noqa: E402


async def main() -> None:
    if not db.read_url:
        sys.exit("DATABASE_URL is not set; see the setup in this file's docstring")

    async with Client(mcp) as client:
        print("== 1. postgres://schema/users ==")
        print((await client.read_resource("postgres://schema/users")).contents[0].text)

        print("\n== 2. query_database ==")
        for sql in ("SELECT email, country FROM users LIMIT 3", "SELECT id FROM users LIMIT 999999"):
            data = (await client.call_tool("query_database", {"sql": sql})).structured_content
            print(json.dumps(data, indent=2))

        print("\n== 3. sample_table ==")
        for table in ("products", "no_such_table", 'users"; DROP TABLE users; --', "users WHERE 1=1"):
            data = (await client.call_tool("sample_table", {"table": table, "limit": 1})).structured_content
            print(f"{table!r}: ok={data['ok']} executed_sql={data['executed_sql']!r} error={data['error']!r}")

        print("\n== 4. the role, past the validator ==")
        async with db.read_connection() as conn:
            for setting in ("default_transaction_read_only", "statement_timeout",
                            "idle_in_transaction_session_timeout"):
                print(f"SHOW {setting}: {await conn.fetchval(f'SHOW {setting}')}")
            try:
                await conn.execute("DELETE FROM orders WHERE id = 1")
            except asyncpg.PostgresError as exc:
                print(f"DELETE: {type(exc).__name__} (SQLSTATE {exc.sqlstate}): {exc}")

        print("\n== 5. the same DELETE with the read-only default switched off ==")
        async with db.read_connection() as conn:
            await conn.execute("SET default_transaction_read_only = off")
            try:
                await conn.execute("DELETE FROM orders WHERE id = 1")
            except asyncpg.PostgresError as exc:
                print(f"DELETE: {type(exc).__name__} (SQLSTATE {exc.sqlstate}): {exc}")
            await conn.execute("RESET default_transaction_read_only")


asyncio.run(main())
