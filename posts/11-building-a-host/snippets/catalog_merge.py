"""Post 11, section 5: many servers, one catalog.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/catalog_merge.py

Builds the ToolCatalog of code/10-mcp-client/ from plain tool lists, then
opens two real servers in memory: the server under code/05-first-server/ and
a second server, `mini`, defined here exactly as the project's
tests/test_catalog.py defines it, whose find_process collides with the first
server's. Prints the merged catalog, the names a dot and an underscore
separator produce, the refusals of illegal names, what resolves, where a
qualified call is routed, and what an unknown name produces. Last, it opens
the same server twice under two configuration keys.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import Tool, ToolAnnotations  # noqa: E402

from mcp_host.catalog import ToolCatalog, UnknownTool, open_pool  # noqa: E402
from mcp_host.connection import ServerSpec  # noqa: E402

from system_info import mcp as system_info_server  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed

mini = MCPServer("mini")


@mini.tool(
    title="Find a process (fake)",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def find_process(name: str, limit: int = 5) -> str:
    """A deliberately different find_process, to force a name collision."""
    return f"mini found nothing matching {name!r}"


@mini.tool(annotations=ToolAnnotations(read_only_hint=True))
def ping() -> str:
    """Return pong."""
    return "pong"


def tool(name: str) -> Tool:
    return Tool(name=name, input_schema={"type": "object"})


def pure_catalogs() -> None:
    print("== ToolCatalog.build on plain tool lists ==")
    both = {"docs": [tool("search")], "files": [tool("search")]}
    print(f"dot:        {sorted(ToolCatalog.build(both).names())}")
    print(f"underscore: {sorted(ToolCatalog.build(both, separator='_').names())}")
    for servers in ({"bad": [tool("group/search")]}, {"my docs": [tool("search")], "files": [tool("search")]}):
        try:
            ToolCatalog.build(servers)
        except ValueError as exc:
            print(f"ValueError: {exc}")
    alone = ToolCatalog.build({"system-info": [tool("get_system_info")]})
    entry = alone.resolve("system-info.get_system_info")
    print(f"resolve('system-info.get_system_info') -> published name {entry.name!r}, server {entry.server!r}")
    try:
        alone.resolve("teleport")
    except UnknownTool as exc:
        print(f"resolve('teleport') raises UnknownTool({exc})")


async def two_servers() -> None:
    print("\n== system-info and mini, in memory ==")
    specs = [ServerSpec.memory("system-info", system_info_server), ServerSpec.memory("mini", mini)]
    async with open_pool(specs) as pool:
        for e in pool.catalog:
            mark = "   <- namespaced" if e.name != e.tool_name else ""
            print(f"{e.name:<28} server={e.server:<12} wire name={e.tool_name}{mark}")
        theirs = await pool.call("mini.find_process", {"name": "zzz"})
        print(f"\npool.call('mini.find_process', ...) -> {theirs.text}")
        unknown = await pool.call("teleport", {})
        print(f"pool.call('teleport', {{}}) -> {unknown.for_model()}")


async def one_server_twice() -> None:
    print("\n== the same server under two configuration keys ==")
    specs = [ServerSpec.memory("left", system_info_server), ServerSpec.memory("right", system_info_server)]
    async with open_pool(specs) as pool:
        for name, conn in pool.connections.items():
            print(f"key {name!r}: the server calls itself {conn.server_info.name!r}")
        print(f"catalog: {pool.catalog.names()}")


pure_catalogs()
asyncio.run(two_servers())
asyncio.run(one_server_twice())
