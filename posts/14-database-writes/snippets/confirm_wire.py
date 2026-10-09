"""Post 14, section 4: what write_database publishes and what it puts on the wire to ask a human.

Run from the series root:
    uv run python posts/14-database-writes/snippets/confirm_wire.py

Imports the project under code/13-postgres-analyst/ with both database URLs
empty and ENABLE_WRITES=true, and connects in-memory clients to it. Prints the
input properties and annotations tools/list publishes for write_database; the
input_required result of the first leg of a call, taken with
allow_input_required=True so the client does not answer it; the four answers a
user can give, with the questions each call showed; the question texts of three
identical calls; and what a client with no elicitation callback gets. There is
no write pool, so an approved write stops at the pool check and nothing runs.

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
from importlib.metadata import version  # noqa: E402

os.environ.update(DATABASE_URL="", DATABASE_WRITE_URL="", ENABLE_WRITES="true")
sys.path.insert(0, str(PROJECT / "src"))

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from pg_analyst import mcp  # noqa: E402

logging.getLogger().handlers[0].setLevel(logging.CRITICAL)  # the server's startup lines are not the point here

SQL = "DELETE FROM orders WHERE id = 4242"


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
    async with Client(mcp, elicitation_callback=user("decline")) as client:
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
        seen: list[str] = []
        async with Client(mcp, elicitation_callback=user(action, content, seen)) as client:
            result = await client.call_tool("write_database", {"sql": SQL})
        data = result.structured_content
        label = f"{action} {json.dumps(content)}" if content else action
        print(f"{label:<26} asked={len(seen)} isError={json.dumps(result.is_error)} "
              f"status={data['status']!r} detail={data['detail']!r}")


async def same_question() -> None:
    seen: list[str] = []
    for _ in range(3):
        async with Client(mcp, elicitation_callback=user("decline", seen=seen)) as client:
            await client.call_tool("write_database", {"sql": "DELETE FROM orders WHERE id = 7"})
    print(f"\nthree calls, distinct question texts: {len(set(seen))} of {len(seen)}")


async def no_callback() -> None:
    print("\n== a client with no elicitation_callback ==")
    async with Client(mcp) as client:
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
    await same_question()
    await no_callback()


asyncio.run(main())
