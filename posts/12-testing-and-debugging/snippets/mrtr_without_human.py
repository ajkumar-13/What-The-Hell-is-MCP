"""Post 12, section 6: the elicitation round trip, driven with no human in the loop.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/mrtr_without_human.py

Imports the first-server project under code/05-first-server/ and calls
terminate_process through in-memory clients whose elicitation callback plays
the user. The process id is 999999, which does not exist, so the accepted path
reaches the lookup and stops there. Then it calls the same tool from a client
with no callback, and counts the questions one call asks.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client, MCPError  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from system_info import mcp  # noqa: E402


def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always responds to elicitation the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)


async def main() -> None:
    print("== one call per answer, terminate_process(pid=999999) ==")
    for action, content in (
        ("accept", {"confirm": True}),
        ("accept", {"confirm": False}),
        ("decline", None),
        ("cancel", None),
    ):
        async with answering(action, content) as c:
            result = await c.call_tool("terminate_process", {"pid": 999999})
        label = f"{action} {json.dumps(content)}" if content else action
        print(f"{label:<26} {result.result_type}  isError={json.dumps(result.is_error)}  {result.content[0].text}")

    print("\n== the same call from a client with no elicitation_callback ==")
    try:
        async with Client(mcp) as c:
            await c.call_tool("terminate_process", {"pid": 999999})
    except BaseException as exc:  # the client's task group may wrap it
        layers = 0
        while getattr(exc, "exceptions", None):
            exc, layers = exc.exceptions[0], layers + 1
        print(f"arrived inside {layers} ExceptionGroup layer(s)")
        print(f"{type(exc).__name__}: {exc}")
        if isinstance(exc, MCPError):
            print(f"code {exc.error.code}, data {json.dumps(exc.error.data)}")

    print("\n== how many questions one call asks, terminate_process(pid=999999) ==")
    seen: list[str] = []
    async with answering("decline", seen=seen) as c:
        await c.call_tool("terminate_process", {"pid": 999999})
    print(f"questions: {len(seen)}")
    print(f"text: {seen[0]}")
    print(f"the client's round cap, input_required_max_rounds: {Client(mcp).input_required_max_rounds}")


asyncio.run(main())
