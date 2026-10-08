"""Post 08, section 6: what the model sees of terminate_process.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/published_schema.py

Imports the first-server project under code/05-first-server/ and connects an
in-memory client to it, as the project's tests do. Prints the function's own
parameters next to the inputSchema and annotations that tools/list publishes,
so the missing approval parameter is visible rather than asserted.
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

from system_info import mcp  # noqa: E402
from system_info.interactive import terminate_process  # noqa: E402


async def main() -> None:
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")
    print(f"Python parameters of terminate_process: {list(inspect.signature(terminate_process).parameters)}")

    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        tool = tools["terminate_process"]
        schema = tool.input_schema
        print(f"published properties:                   {list(schema['properties'])}")
        print(f"published required:                     {schema['required']}")
        print("\n== inputSchema, as tools/list carries it ==")
        print(json.dumps(schema, indent=2))
        print("\n== annotations ==")
        print(json.dumps(tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)))


asyncio.run(main())
