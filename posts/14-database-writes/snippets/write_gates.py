"""Post 14, sections 2 and 3: the pool accessors, the fallback that was removed, and the gates before any question.

Run from the series root:
    uv run python posts/14-database-writes/snippets/write_gates.py

Imports the project under code/13-postgres-analyst/ with both database URLs
empty and connects an in-memory client to it, as the project's
tests/test_server.py does. Prints the first docstring line of each connection
accessor, what the removed fallback line resolves to when only DATABASE_URL is
set, and then what write_database answers before anybody is asked anything:
with writes switched off, for statements the validator refuses, and for dry
runs. Every call counts the questions its user was shown.

The validator and the driver live in the project's environment rather than the
root one, so when the root environment lacks the project's dependencies the
script runs itself again under `uv run --project code/13-postgres-analyst`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
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
import logging  # noqa: E402

# Empty values, not missing ones: load_dotenv() never overrides a variable that
# is already set, so a .env file in the project cannot reach a real database.
os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="false")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from pg_analyst import mcp  # noqa: E402
from pg_analyst.database import Database  # noqa: E402

logging.getLogger().handlers[0].setLevel(logging.CRITICAL)  # the server's startup lines are not the point here


def accessors() -> None:
    print("== the three ways to get a connection, database.py ==")
    for name in ("read_connection", "write_connection", "write_transaction"):
        first_line = getattr(Database, name).__doc__.strip().splitlines()[0]
        print(f"{name:<18} {first_line}")


def fallback() -> None:
    print("\n== only DATABASE_URL set ==")
    saved = dict(os.environ)
    os.environ["DATABASE_URL"] = "postgresql://mcp_readonly:readonly_password@localhost:5432/analytics"
    os.environ.pop("DATABASE_WRITE_URL", None)
    write_url = os.getenv("DATABASE_WRITE_URL", os.getenv("DATABASE_URL"))
    print(f"the first edition's write URL: {write_url}")
    print(f"Database().write_url today:    {Database().write_url}")
    os.environ.clear()
    os.environ.update(saved)


async def call(sql: str, dry_run: bool | None = None) -> None:
    seen: list[str] = []

    async def user(context, params):
        seen.append(params.message)
        return ElicitResult(action="accept", content={"confirm": True})

    arguments: dict = {"sql": sql}
    if dry_run is not None:
        arguments["dry_run"] = dry_run
    async with Client(mcp, elicitation_callback=user) as client:
        result = await client.call_tool("write_database", arguments)
    print(f"\n--- {json.dumps(arguments)}")
    print(json.dumps(result.structured_content, indent=2))
    print(f"isError={json.dumps(result.is_error)}  questions asked: {len(seen)}")


async def main() -> None:
    accessors()
    fallback()

    print("\n== ENABLE_WRITES=false ==")
    await call("UPDATE users SET active = false WHERE id = 1")

    print("\n== ENABLE_WRITES=true ==")
    os.environ["ENABLE_WRITES"] = "true"
    await call("TRUNCATE TABLE users")
    await call("DELETE FROM users")
    await call("update users set active=false where id = 3", dry_run=True)
    await call("update  users   set active = false where id = 3 /* and everything else */", dry_run=True)
    await call("UPDATE users SET active = false WHERE true", dry_run=True)


asyncio.run(main())
