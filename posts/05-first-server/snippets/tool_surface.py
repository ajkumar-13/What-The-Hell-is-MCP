"""Post 05, sections 4 and 5: the tool surface the first server publishes.

Run from the series root:
    uv run python posts/05-first-server/snippets/tool_surface.py

Imports the project under code/05-first-server/ and connects an in-memory
client to it, exactly as that project's tests/test_server.py does: no
subprocess, no socket, the real protocol path. Prints the versions it ran
against, the server's self-reported identity, the get_system_info entry of
tools/list in its wire spelling, the inputSchema of find_process, and the
result of calling find_process without its required argument.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402

from system_info import mcp  # noqa: E402


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
    print(f"mcp {version('mcp')}, mcp-types {version('mcp-types')}, Python {sys.version.split()[0]}")

    async with Client(mcp) as client:
        info = client.server_info
        print(f"serverInfo: name={info.name!r} version={info.version!r}")

        tools = {t.name: t for t in (await client.list_tools()).tools}
        print(f"\ntools/list returned {len(tools)} tools: {', '.join(tools)}")

        # by_alias=True gives the wire spelling (inputSchema, readOnlyHint);
        # without it pydantic prints the Python names (input_schema, read_only_hint).
        wire = tools["get_system_info"].model_dump(mode="json", by_alias=True, exclude_none=True)
        print("\n== get_system_info, as tools/list carries it ==")
        print(compact(wire))

        print("\n== find_process inputSchema ==")
        print(compact(tools["find_process"].input_schema))

        print("\n== tools/call find_process with arguments {} ==")
        result = await client.call_tool("find_process", {})
        print(f"isError: {json.dumps(result.is_error)}")
        print(result.content[0].text)


asyncio.run(main())
