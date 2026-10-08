"""Post 06, section 7: what a failing tool sends, and what the server logs.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/error_paths.py

Registers three throwaway tools on an in-memory server: one that refuses an
out-of-range argument with ToolError, one that crashes with an exception it did
not anticipate, and one that raises MCPError. Calls each, prints the result or
the protocol error the client receives, and prints the first line of each log
record the server wrote. Nothing is served over a transport and nothing is left
running.
"""

from __future__ import annotations

import asyncio
import json
import logging

from mcp import Client
from mcp.server.mcpserver import MCPServer


class Records(logging.Handler):
    """Keep the server's log records instead of printing them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


LOG = Records()
logging.basicConfig(level=logging.INFO, handlers=[LOG])

mcp = MCPServer("errors-demo")

from mcp.server.mcpserver.exceptions import ToolError

@mcp.tool(title="Sample CPU for a window")
def sample_window(seconds: int) -> str:
    """Sample CPU for `seconds` seconds, between 1 and 30."""
    if not 1 <= seconds <= 30:
        raise ToolError(
            f"seconds must be between 1 and 30, got {seconds}. "
            "Retry with a value in range."
        )
    return f"would sample for {seconds} seconds"


@mcp.tool()
def average(total: int, count: int) -> float:
    """Divide, without guarding against a zero count."""
    return total / count


@mcp.tool()
def strict() -> str:
    """Refuse on the protocol side."""
    from mcp.shared.exceptions import MCPError
    from mcp_types import INVALID_PARAMS

    raise MCPError(INVALID_PARAMS, "Unknown tool: nope")


def wire(result) -> str:
    data = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("_meta", None)  # serverInfo, the same on every result
    return json.dumps(data, indent=2)


def drain() -> None:
    for record in LOG.records:
        if record.name.startswith("mcp"):
            print(f"server log: {record.levelname} {record.getMessage().splitlines()[0]}")
            if record.exc_info:
                print(f"            (with a traceback ending in {record.exc_info[1].__cause__!r})")
    LOG.records.clear()


async def main() -> None:
    async with Client(mcp) as client:
        for name, arguments in (("sample_window", {"seconds": 500}), ("average", {"total": 10, "count": 0})):
            print(f"== tools/call {name} {json.dumps(arguments)} ==")
            print(wire(await client.call_tool(name, arguments)))
            drain()
            print()

        print("== tools/call strict {} ==")
        try:
            await client.call_tool("strict", {})
            print("no error")
        except Exception as exc:  # noqa: BLE001 - printing the failure is the point
            print(f"{type(exc).__name__}: code={exc.code} message={exc.message!r}")
        drain()


asyncio.run(main())
