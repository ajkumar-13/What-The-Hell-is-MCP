"""Post 14, section 4: three ways to annotate a resolved approval, and the stand-in a resolver can return.

Run from the series root:
    uv run python posts/14-database-writes/snippets/resolver_spellings.py

Registers small tools on a throwaway MCPServer, one per spelling of the
approval parameter, and calls each through an in-memory client whose user
accepts, declines, or cancels. Prints what the tool body received, or what the
model got back. The third spelling is refused when the decorator runs. Last, a
resolver that returns a DeclinedElicitation() instead of an Elicit, as the
project's _confirm_write does for its four no-ask cases, shows what the tool
body receives in that case.

Needs only the SDK, so it runs in the root environment as it is.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from mcp import Client
from mcp.server.mcpserver import (
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)
from mcp_types import ElicitResult
from pydantic import BaseModel


class Approval(BaseModel):
    confirm: bool


def ask(row: int) -> Elicit[Approval]:
    return Elicit(f"Delete row {row}?", Approval)


def skip(row: int) -> Elicit[Approval] | ElicitationResult[Approval]:
    return DeclinedElicitation()


server = MCPServer("spellings")


@server.tool()
def full_union(row: int, ap: Annotated[ElicitationResult[Approval], Resolve(ask)]) -> str:
    return f"body got {type(ap).__name__}"


@server.tool()
def optional_inside(row: int, ap: Annotated[ElicitationResult[Approval] | None, Resolve(ask)]) -> str:
    return f"body got {type(ap).__name__}"


@server.tool()
def stand_in(row: int, ap: Annotated[ElicitationResult[Approval], Resolve(skip)]) -> str:
    return (
        f"body got {type(ap).__name__}, data={ap.data!r}, "
        f"isinstance(ap, DeclinedElicitation)={isinstance(ap, DeclinedElicitation)}"
    )


def optional_outside(row: int, ap: Annotated[ElicitationResult[Approval], Resolve(ask)] | None = None) -> str:
    return "never registered"


async def run(tool: str, action: str) -> str:
    seen: list[str] = []

    async def user(context, params):
        seen.append(params.message)
        content = {"confirm": True} if action == "accept" else None
        return ElicitResult(action=action, content=content)

    async with Client(server, elicitation_callback=user) as client:
        result = await client.call_tool(tool, {"row": 1})
    error = " isError=true " if result.is_error else " "
    return f"asked={len(seen)}{error}{result.content[0].text}"


async def main() -> None:
    for tool, spelling in (
        ("full_union", "Annotated[ElicitationResult[T], Resolve(f)]"),
        ("optional_inside", "Annotated[ElicitationResult[T] | None, Resolve(f)]"),
    ):
        print(f"== {spelling} ==")
        for action in ("accept", "decline", "cancel"):
            print(f"  {action:<8} {await run(tool, action)}")

    print("== Annotated[ElicitationResult[T], Resolve(f)] | None ==")
    try:
        server.tool()(optional_outside)
    except Exception as exc:  # noqa: BLE001 - printing what the SDK raises
        print(f"  at registration: {type(exc).__name__}: {exc}")

    print("== a resolver that returns DeclinedElicitation() instead of Elicit(...) ==")
    print(f"  {await run('stand_in', 'accept')}")


asyncio.run(main())
