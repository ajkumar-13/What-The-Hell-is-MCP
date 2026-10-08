"""Post 08, sections 8 and 10: the four answers a user can give, and how often the question is asked.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/four_outcomes.py

Imports the first-server project under code/05-first-server/ and calls
terminate_process through an in-memory client whose elicitation callback plays
the user: accept with confirm true, accept with confirm false, decline, and
cancel. The client drives the MRTR retry itself. The process id is 999999,
which does not exist, so the accepted path reaches the lookup and stops there.
Then it records every question the callback is shown for one call.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from system_info import mcp  # noqa: E402


def user(action: str, content: dict | None = None, seen: list | None = None):
    """An elicitation callback that always answers the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return callback


async def main() -> None:
    print("== terminate_process(pid=999999), one call per answer ==")
    for action, content in (
        ("accept", {"confirm": True}),
        ("accept", {"confirm": False}),
        ("decline", None),
        ("cancel", None),
    ):
        async with Client(mcp, elicitation_callback=user(action, content)) as client:
            result = await client.call_tool("terminate_process", {"pid": 999999})
        label = f"{action} {json.dumps(content)}" if content else action
        print(f"{label:<28} isError={json.dumps(result.is_error)}  {result.content[0].text}")

    print("\n== the questions shown for one call, terminate_process(pid=999999) ==")
    seen: list[str] = []
    async with Client(mcp, elicitation_callback=user("decline", seen=seen)) as client:
        await client.call_tool("terminate_process", {"pid": 999999})
    print(f"asked {len(seen)} time(s)")
    for text in seen:
        print(f"text: {text}")


asyncio.run(main())
