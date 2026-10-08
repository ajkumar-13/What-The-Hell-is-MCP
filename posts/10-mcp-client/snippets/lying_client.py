"""Post 10, sections 1 and 6: the client that lied to its model.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/lying_client.py

Connects an in-memory client to the server under code/05-first-server/ (no
subprocess, no socket), calls find_process with a string where an integer
belongs, and reads the answer two ways: with the try/except pattern many
hand-written clients start with, and with read_result from
code/10-mcp-client/src/mcp_host/results.py. Then prints the raw result in its
wire spelling, the type of the isError field, and what an unknown tool name
returns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp import Client  # noqa: E402
from mcp_types import CallToolResult  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from mcp_host.results import read_result  # noqa: E402
from system_info import mcp  # noqa: E402

logging.disable(logging.INFO)  # the server's own INFO lines go to stderr; keep the output to what is printed


async def naive_call(session, tool_use):
    try:
        result = await session.call_tool(tool_use.name, tool_use.input)
        text = "\n".join(c.text for c in result.content if hasattr(c, "text")).strip()
    except Exception as e:
        text = f"TOOL_ERROR: {type(e).__name__}: {e}"
    return text


async def main() -> None:
    tool_use = SimpleNamespace(name="find_process", input={"limit": "not-a-number"})

    async with Client(mcp) as client:
        print("== the naive client: what it hands the model ==")
        text = await naive_call(client, tool_use)
        print(f"starts with TOOL_ERROR: {text.startswith('TOOL_ERROR')}")
        print(text.splitlines()[0])

        raw = await client.call_tool(tool_use.name, tool_use.input)
        print("\n== the same result, as the server sent it ==")
        print(json.dumps(raw.model_dump(mode="json", by_alias=True, exclude_none=True), indent=2))
        print(f"\nis_error={raw.is_error!r} result_type={raw.result_type!r}")

        outcome = read_result(tool_use.name, raw)
        print(f"read_result: ok={outcome.ok!r}")
        print(outcome.for_model().splitlines()[0])

        unknown = await client.call_tool("no_such_tool", {})
        print("\n== an unknown tool name ==")
        print(f"is_error={unknown.is_error!r} text={unknown.content[0].text!r}")

    print("\n== the isError field in Python ==")
    print(repr(CallToolResult.model_fields["is_error"]))
    try:
        CallToolResult(content=[], is_error=None)
    except ValidationError as exc:
        print(str(exc).splitlines()[0])
        print("\n".join(str(exc).splitlines()[1:3]))


asyncio.run(main())
