"""Post 14, section 4: what write_database publishes and what it puts on the wire to ask a human.

Run from the series root:
    uv run python posts/14-database-writes/snippets/confirm_wire.py

Imports the project under code/13-postgres-analyst/ with both database URLs
empty and ENABLE_WRITES=true, and connects in-memory clients to it. Prints the
input properties and annotations tools/list publishes for write_database; the
input_required result of the first leg of a call, taken with
allow_input_required=True so the client does not answer it; the four answers a
user can give, with the questions each call showed; the question texts of three
identical calls; and what a client with no elicitation callback gets.

No PostgreSQL is involved. The resolver asks only when there is a write pool,
so a stand-in pool is opened in place of asyncpg's before each client starts:
it accepts the lifespan's CREATE statements, answers a DELETE with
`DELETE 0` (the tag PostgreSQL returns for a DELETE that matches no row; order
4242 is not in sql/001-seed.sql), and hands back 1 as the audit row's id. Last,
the approved write once more with no write pool at all.

The series root's environment pins the project's asyncpg, sqlglot and
python-dotenv beside the SDK, so this runs there as it is.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "13-postgres-analyst"

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from importlib.metadata import version  # noqa: E402

os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="true")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from pg_analyst import mcp  # noqa: E402
from pg_analyst.database import db  # noqa: E402

logging.getLogger().handlers[0].setLevel(logging.CRITICAL)  # the server's startup lines are not the point here

SQL = "DELETE FROM orders WHERE id = 4242"


class StandInConnection:
    async def execute(self, sql: str, *args) -> str:
        return "CREATE" if sql.lstrip().startswith("CREATE") else "DELETE 0"

    async def fetchval(self, sql: str, *args) -> int:
        return 1  # the audit row's id

    @asynccontextmanager
    async def transaction(self):
        yield


class StandInPool:
    @asynccontextmanager
    async def acquire(self):
        yield StandInConnection()

    async def close(self) -> None:
        pass


def connect(callback=None, write_pool: bool = True) -> Client:
    """A client on the project's server, with the stand-in write pool opened.

    The lifespan closes the pools when a client disconnects, so each client
    gets the stand-in installed again.
    """
    db._write_pool = StandInPool() if write_pool else None
    return Client(mcp, elicitation_callback=callback) if callback else Client(mcp)


def user(action: str, content: dict | None = None, seen: list | None = None):
    """An elicitation callback that always answers the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return callback


async def published() -> None:
    async with Client(mcp) as client:
        tool = {t.name: t for t in (await client.list_tools()).tools}["write_database"]
    a = tool.annotations
    print("tool write_database")
    print(f"  input properties : {list(tool.input_schema['properties'])}")
    print(f"  required         : {tool.input_schema['required']}")
    print(f"  annotations      : readOnly={a.read_only_hint} destructive={a.destructive_hint} idempotent={a.idempotent_hint}")


async def first_leg() -> None:
    print(f"\n== first leg of write_database({json.dumps({'sql': SQL})}) ==")
    async with connect(user("decline")) as client:
        result = await client.session.call_tool("write_database", {"sql": SQL}, allow_input_required=True)
    wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    state = wire["requestState"]
    wire["requestState"] = state[:3] + "<sealed, shortened here>"  # different on every run
    print(json.dumps(wire, indent=2))
    print(f"requestState: {len(state)} characters, shortened above")


async def four_answers() -> None:
    print(f"\n== four answers to write_database({json.dumps({'sql': SQL})}) ==")
    for action, content in (
        ("decline", None),
        ("cancel", None),
        ("accept", {"confirm": False}),
        ("accept", {"confirm": True}),
    ):
        await answer(action, content)


async def answer(action: str, content: dict | None, write_pool: bool = True, label: str = "") -> None:
    seen: list[str] = []
    async with connect(user(action, content, seen), write_pool=write_pool) as client:
        result = await client.call_tool("write_database", {"sql": SQL})
    data = result.structured_content
    label = label or (f"{action} {json.dumps(content)}" if content else action)
    print(f"{label:<26} asked={len(seen)} isError={json.dumps(result.is_error)} "
          f"status={data['status']!r} detail={data['detail']!r}")


async def no_write_pool() -> None:
    print("\n== the approved write, with no write pool ==")
    await answer("accept", {"confirm": True}, write_pool=False)


async def same_question() -> None:
    seen: list[str] = []
    for _ in range(3):
        async with connect(user("decline", seen=seen)) as client:
            await client.call_tool("write_database", {"sql": "DELETE FROM orders WHERE id = 7"})
    print(f"\nthree calls, distinct question texts: {len(set(seen))} of {len(seen)}")


async def no_callback() -> None:
    print("\n== a client with no elicitation_callback ==")
    async with connect() as client:
        try:
            await client.call_tool("write_database", {"sql": SQL})
        except MCPError as exc:
            print(f"MCPError {exc.code}: {exc}")
            print(f"data: {json.dumps(exc.error.data)}")


async def main() -> None:
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")
    await published()
    await first_leg()
    await four_answers()
    await no_write_pool()
    await same_question()
    await no_callback()


asyncio.run(main())
