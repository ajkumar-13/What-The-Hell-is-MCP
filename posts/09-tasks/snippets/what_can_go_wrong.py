"""Post 09, section 13: two mistakes a first tasks implementation makes.

Run from the series root:
    uv run python posts/09-tasks/snippets/what_can_go_wrong.py

1. Copying a 2025-11-25 tutorial: passing a per-tool `execution` setting to
   the SDK's tool decorator, which has no such parameter.
2. Running a tool's work out of band through MCPServer.call_tool, with no
   request behind it, when the tool reports progress through its Context.

Both run in memory against the real watch_cpu of code/05-first-server/; the
second takes about one second, the length of one sample.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from system_info.progress import watch_cpu  # noqa: E402


async def main() -> None:
    server = MCPServer("pitfalls")

    print("== a per-tool execution setting, pasted from a 2025-11-25 tutorial ==")
    try:
        server.tool(execution={"taskSupport": "required"})
    except TypeError as err:
        print(f"TypeError: {err}")

    print("\n== watch_cpu run through MCPServer.call_tool with no request behind it ==")
    server.add_tool(watch_cpu, title="Watch CPU over time")
    try:
        await server.call_tool("watch_cpu", {"seconds": 1})
    except Exception as err:
        print(f"{type(err).__name__}: {err}")
        print(f"  caused by {type(err.__cause__).__name__}: {err.__cause__}")

    print("\n== the same function called directly, with ctx left at None ==")
    watch = await watch_cpu(seconds=1)
    print(f"CpuWatch with {len(watch.samples)} sample, no progress reported")


asyncio.run(main())
