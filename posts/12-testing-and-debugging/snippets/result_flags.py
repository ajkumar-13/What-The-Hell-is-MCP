"""Post 12, section 7: failures that arrive as successful results, and the isError field.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/result_flags.py

Imports the first-server project under code/05-first-server/ and calls two
tools in memory in ways that fail: a misspelled tool name and a `limit` that is
not an integer. For each it prints what the SDK returned and whether anything
raised. Then it builds CallToolResult objects directly to show what the
`is_error` field accepts.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp_types import CallToolResult  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from system_info import mcp  # noqa: E402


async def main() -> None:
    print("== two failed calls, in memory ==")
    async with Client(mcp) as c:
        for name, arguments in (
            ("no_such_tool", {}),
            ("find_process", {"name": "python", "limit": "not-a-number"}),
        ):
            try:
                raw = await c.call_tool(name, arguments)
            except Exception as exc:  # noqa: BLE001
                print(f"{name}: raised {type(exc).__name__}")
                continue
            first_line = raw.content[0].text.splitlines()[0]
            print(f"{name}: raised nothing; result_type={raw.result_type!r} is_error={raw.is_error}")
            print(f"    text: {first_line}")

    print("\n== what is_error accepts ==")
    print(f"default: {CallToolResult(content=[]).is_error!r}")
    print(f"field annotation: {CallToolResult.model_fields['is_error'].annotation!r}")
    try:
        CallToolResult(content=[], is_error=None)
    except ValidationError as exc:
        print(f"is_error=None: ValidationError: {exc.errors()[0]['msg']}")
    wire = {"content": [], "resultType": "complete"}
    print(f"parsed from {json.dumps(wire)}: is_error={CallToolResult.model_validate(wire).is_error!r}")


asyncio.run(main())
