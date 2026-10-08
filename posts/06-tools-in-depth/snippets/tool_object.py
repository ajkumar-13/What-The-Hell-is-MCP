"""Post 06, sections 2 and 8: one complete Tool object, and its annotations.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/tool_object.py

Imports the project under code/05-first-server/ and connects an in-memory
client to it, as that project's tests/test_server.py does: no subprocess, no
socket, the real protocol path. Prints the terminate_process entry of
tools/list in its wire spelling, the parameters @mcp.tool() accepts, what the
bare decorator raises, the annotations get_system_info publishes, and how the
two spellings of an annotation behave in Python.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from importlib.metadata import version
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import ToolAnnotations  # noqa: E402

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


def bare_decorator() -> None:
    server = MCPServer("broken")

    @server.tool  # the mistake: no parentheses
    def ping() -> str:
        return "pong"


async def main() -> None:
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")

    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    # by_alias=True gives the wire spelling (inputSchema, readOnlyHint).
    wire = tools["terminate_process"].model_dump(mode="json", by_alias=True, exclude_none=True)
    print("\n== terminate_process, as tools/list carries it ==")
    print(compact(wire))

    params = [p for p in inspect.signature(MCPServer.tool).parameters if p != "self"]
    print("\n== parameters of @mcp.tool() ==")
    print(", ".join(params))

    print("\n== @mcp.tool without parentheses ==")
    try:
        bare_decorator()
        print("no error")
    except TypeError as exc:
        print(f"TypeError: {exc}")

    ann = tools["get_system_info"].annotations
    print("\n== get_system_info annotations, on the wire ==")
    print(json.dumps(ann.model_dump(mode="json", by_alias=True, exclude_none=True)))

    print("\n== the same annotations, read in Python ==")
    print(f"annotations.read_only_hint -> {ann.read_only_hint}")
    try:
        print(f"annotations.readOnlyHint -> {ann.readOnlyHint}")
    except AttributeError as exc:
        print(f"annotations.readOnlyHint -> AttributeError: {exc}")
    built = ToolAnnotations(readOnlyHint=True)
    print(f"ToolAnnotations(readOnlyHint=True).read_only_hint -> {built.read_only_hint}")


asyncio.run(main())
