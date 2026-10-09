"""Post 12, section 5: the published-surface assertions, against a good server and three bad ones.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/schema_assertions.py

Runs the output-schema assertion of code/05-first-server/tests/test_server.py,
and the exact-set check on the published input schema, against the project's
server in memory, then against three small servers built here: one that
registers nothing, one whose return class has no class-body annotations, and
one with a tool that returns a content block. It prints what each check says.
Nothing is served over a transport and nothing is left running.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import TextContent  # noqa: E402

from system_info import mcp  # noqa: E402


async def every_tool_publishes_an_output_schema(server) -> None:
    """The assertion of test_every_tool_publishes_an_output_schema, on any server."""
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def check(label: str, server) -> None:
    try:
        await every_tool_publishes_an_output_schema(server)
    except BaseException as exc:  # an assert inside the client block leaves it wrapped
        layers = 0
        while getattr(exc, "exceptions", None):
            exc, layers = exc.exceptions[0], layers + 1
        print(f"{label:<36} FAILED  {type(exc).__name__}: {exc}  ({layers} group layers)")
    else:
        print(f"{label:<36} passed")


empty = MCPServer("empty")

silent = MCPServer("silent")


class Snapshot:
    """Attributes assigned only in __init__: no class-body annotations."""

    def __init__(self, cpu_percent: float):
        self.cpu_percent = cpu_percent


@silent.tool()
def snapshot() -> Snapshot:
    """Return a snapshot."""
    return Snapshot(cpu_percent=12.5)


prose = MCPServer("prose")


@prose.tool()
def summary() -> TextContent:
    """Return a sentence for the model to read."""
    return TextContent(type="text", text="All quiet.")


async def main() -> None:
    print("== every tool publishes an output schema ==")
    await check("code/05-first-server (system-info)", mcp)
    await check("a server that registered nothing", empty)
    await check("a return class with no annotations", silent)
    await check("a tool returning a content block", prose)

    print("\n== the published input schema of terminate_process ==")
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    props = tools["terminate_process"].input_schema["properties"]
    print(f"published properties: {sorted(props)}")
    print(f"set(props) == {{'pid'}}: {set(props) == {'pid'}}")


asyncio.run(main())
