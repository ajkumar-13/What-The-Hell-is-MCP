"""Post 09, section 2: progress from a slow tool, with no extension at all.

Run from the series root:
    uv run python posts/09-tasks/snippets/progress_today.py

Imports the project under code/05-first-server/ and connects an in-memory
client to it, as that project's tests/test_server.py does. Prints what
tools/list publishes for watch_cpu, then calls it for three seconds with a
progress callback (which makes the client put a progressToken in the request's
_meta) and prints every notifications/progress that arrives before the result.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from importlib.metadata import version
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402

from system_info import mcp  # noqa: E402


async def main() -> None:
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        tool = tools["watch_cpu"]
        print(f"tools/list: {', '.join(tools)}")
        print("watch_cpu inputSchema properties:", json.dumps(tool.input_schema["properties"]))
        print("watch_cpu outputSchema properties:", ", ".join(tool.output_schema["properties"]))

        start = time.monotonic()

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            print(f"  notifications/progress  {progress:g} of {total:g}  {message}")

        print("\ntools/call watch_cpu seconds=3, with a progress callback:")
        result = await client.call_tool("watch_cpu", {"seconds": 3}, progress_callback=on_progress)
        elapsed = time.monotonic() - start
        data = result.structured_content or {}
        print(
            f"result: resultType {result.result_type}, isError {json.dumps(result.is_error)}, "
            f"{len(data['samples'])} samples, after about {round(elapsed)} seconds"
        )


asyncio.run(main())
