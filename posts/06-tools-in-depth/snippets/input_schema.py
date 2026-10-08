"""Post 06, section 3: input schemas, as published and as redesigned.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/input_schema.py

Prints the inputSchema the project's find_process and get_system_info publish,
then registers a redesigned find_process on a throwaway in-memory server and
prints its schema and what the SDK answers when a call breaks a bound or the
enumeration. Nothing is served over a transport and nothing is left running.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from pydantic import Field  # noqa: E402

import system_info  # noqa: E402

mcp = MCPServer("redesign")


@mcp.tool(name="find_process")
def find_process(
    name: Annotated[str, Field(description="Case-insensitive substring of the process name, for example 'chrome'.")],
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum processes to return.")] = 25,
    sort_by: Annotated[Literal["memory", "pid", "name"], Field(description="Sort key for the returned list.")] = "memory",
) -> str:
    """Find running processes by name."""
    return f"would search for {name!r}, {limit} at most, sorted by {sort_by}"


def compact(value, indent: int = 0) -> str:
    """JSON with two-space indentation, one line for any object of scalars."""
    pad = " " * indent
    flat = json.dumps(value)
    scalars = isinstance(value, (dict, list)) and all(
        not isinstance(v, (dict, list))
        for v in (value.values() if isinstance(value, dict) else value)
    )
    if not isinstance(value, (dict, list)) or (scalars and len(flat) + indent <= 88):
        return flat
    if isinstance(value, dict):
        inner = [f'{pad}  {json.dumps(k)}: {compact(v, indent + 2)}' for k, v in value.items()]
        return "{\n" + ",\n".join(inner) + f"\n{pad}}}"
    inner = [f"{pad}  {compact(v, indent + 2)}" for v in value]
    return "[\n" + ",\n".join(inner) + f"\n{pad}]"


async def main() -> None:
    async with Client(system_info.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    print("== the project's find_process inputSchema ==")
    print(compact(tools["find_process"].input_schema))
    print("\n== the project's get_system_info inputSchema ==")
    print(json.dumps(tools["get_system_info"].input_schema))

    async with Client(mcp) as client:
        redesigned = (await client.list_tools()).tools[0]
        print("\n== the redesigned find_process inputSchema ==")
        print(compact(redesigned.input_schema))

        for arguments in ({"name": "python", "limit": 500}, {"name": "python", "sort_by": "cpu"}):
            result = await client.call_tool("find_process", arguments)
            print(f"\n== tools/call find_process with {json.dumps(arguments)} ==")
            print(f"isError: {json.dumps(result.is_error)}")
            print(result.content[0].text)


asyncio.run(main())
