"""Post 08, section 10: a question that changes between rounds never converges.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/drifting_question.py

Two throwaway tools ask the same confirmation. The steady one builds its
message from the tool's arguments only. The drifting one adds a counter that
moves every time the resolver runs, standing in for a timestamp or a live
reading. An in-memory client whose user always answers yes calls each one and
prints how many dialogs the user saw and how the call ended.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Annotated

from pydantic import BaseModel
from mcp import Client
from mcp.server.mcpserver import Elicit, ElicitationResult, MCPServer, Resolve
from mcp_types import ElicitResult


class Ok(BaseModel):
    confirm: bool


mcp = MCPServer("drift")
tick = itertools.count(1)


def steady(pid: int) -> Elicit[Ok]:
    return Elicit(f"Terminate process {pid}? This cannot be undone.", Ok)


def drifting(pid: int) -> Elicit[Ok]:
    return Elicit(f"Terminate process {pid}? (reading {next(tick)})", Ok)


@mcp.tool()
def steady_tool(pid: int, approval: Annotated[ElicitationResult[Ok], Resolve(steady)]) -> str:
    return f"finished: {approval.action}"


@mcp.tool()
def drifting_tool(pid: int, approval: Annotated[ElicitationResult[Ok], Resolve(drifting)]) -> str:
    return f"finished: {approval.action}"


async def main() -> None:
    discarded: list[str] = []

    class Count(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            discarded.append(record.getMessage())

    resolve_log = logging.getLogger("mcp.server.mcpserver.resolve")
    resolve_log.addHandler(Count())
    resolve_log.propagate = False  # count the SDK's INFO lines instead of printing each one

    for name in ("steady_tool", "drifting_tool"):
        shown: list[str] = []

        async def user(context, params):
            shown.append(params.message)
            return ElicitResult(action="accept", content={"confirm": True})

        discarded.clear()
        async with Client(mcp, elicitation_callback=user) as client:
            try:
                result = await client.call_tool(name, {"pid": 4242})
                ending = result.content[0].text
            except Exception as exc:  # noqa: BLE001 - printing how the call ended
                ending = f"{type(exc).__name__}: {exc}"
        print(f"== {name} ==")
        print(f"dialogs shown: {len(shown)}; first two: {shown[:2]}")
        print(f"answers the server discarded: {len(discarded)}")
        if discarded:
            print(f"server log line: {discarded[0]}")
        print(f"ended: {ending}\n")


asyncio.run(main())
