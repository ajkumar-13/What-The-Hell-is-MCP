"""Post 14, section 8: the write path against a real PostgreSQL. A job: the validator does not run it.

Setup, once, from inside code/13-postgres-analyst/:
    docker compose up -d          # PostgreSQL 17 on 127.0.0.1:5432, seeded, both roles created
    cp .env.example .env          # then set ENABLE_WRITES=true in .env

Run from the series root:
    uv run python posts/14-database-writes/snippets/jobs/live_lab.py

It connects an in-memory client to the project's server, which opens real
pools against the URLs in code/13-postgres-analyst/.env (app.py calls
load_dotenv()), and sends one UPDATE through a dry run and then through
accept, decline, cancel, and an accept with confirm false. It then sends a
DELETE that the database refuses (users 1 has orders, so the foreign key
stops it) and reads the audit resource back, so the reader can check which of
the attempts left a row. The UPDATE sets a value user 3 already holds in the
seed, so the data ends as it started. A few seconds against a local container.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[4]
PROJECT = ROOT / "code" / "13-postgres-analyst"

try:
    import asyncpg  # noqa: F401  the project's own dependencies
    import dotenv  # noqa: F401
    import sqlglot  # noqa: F401
except ModuleNotFoundError:
    if os.environ.get("POST14_SNIPPET_CHILD"):
        raise
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env.update(POST14_SNIPPET_CHILD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    uv = os.environ.get("UV", "uv")
    sys.exit(subprocess.call(
        [uv, "run", "--frozen", "--quiet", "--project", str(PROJECT), "python", str(Path(__file__).resolve())],
        env=env,
    ))

import asyncio  # noqa: E402
import json  # noqa: E402

sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from pg_analyst import mcp  # noqa: E402  (load_dotenv() runs here)
from pg_analyst.database import writes_enabled  # noqa: E402

UPDATE = "UPDATE users SET active = false WHERE id = 3"
REFUSED = "DELETE FROM users WHERE id = 1"


def user(action: str, content: dict | None = None):
    async def callback(context, params):
        return ElicitResult(action=action, content=content)

    return callback


async def call(sql: str, action: str, content: dict | None = None, dry_run: bool = False) -> None:
    arguments: dict = {"sql": sql}
    if dry_run:
        arguments["dry_run"] = True
    async with Client(mcp, elicitation_callback=user(action, content)) as client:
        result = await client.call_tool("write_database", arguments)
    data = result.structured_content
    label = "dry run" if dry_run else (f"{action} {json.dumps(content)}" if content else action)
    print(f"{label:<26} status={data['status']!r} rows={data['affected_rows']} audit_id={data['audit_id']}")
    print(f"{'':<26} {data['detail']}")


async def main() -> None:
    if not (os.getenv("DATABASE_URL") and os.getenv("DATABASE_WRITE_URL") and writes_enabled()):
        sys.exit("Set DATABASE_URL, DATABASE_WRITE_URL and ENABLE_WRITES=true (code/13-postgres-analyst/.env) first.")

    print(f"== {UPDATE} ==")
    await call(UPDATE, "accept", {"confirm": True}, dry_run=True)
    await call(UPDATE, "accept", {"confirm": True, "reason": "post 14 lab"})
    await call(UPDATE, "decline")
    await call(UPDATE, "cancel")
    await call(UPDATE, "accept", {"confirm": False})

    print(f"\n== {REFUSED} ==")
    await call(REFUSED, "accept", {"confirm": True})

    print("\n== postgres://audit ==")
    async with Client(mcp) as client:
        print((await client.read_resource("postgres://audit")).contents[0].text)


asyncio.run(main())
