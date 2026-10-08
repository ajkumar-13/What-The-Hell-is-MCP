"""Post 06, section 9: what the SDK does with a tool name outside the advised set.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/naming.py

Registers a tool named files/search on a throwaway in-memory server, prints the
warning lines the SDK logs at registration, and lists the server to show the
name it publishes. Nothing is served over a transport and nothing is left
running.
"""

from __future__ import annotations

import asyncio
import logging

from mcp import Client
from mcp.server.mcpserver import MCPServer


class Records(logging.Handler):
    """Keep the SDK's log records instead of printing them."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


LOG = Records()
logging.basicConfig(level=logging.INFO, handlers=[LOG])

mcp = MCPServer("naming-demo")


@mcp.tool(name="files/search")
def search(query: str) -> str:
    """Search the files."""
    return query


async def main() -> None:
    warnings = [r for r in LOG.records if r.name == "mcp.shared.tool_name_validation"]
    print(f"== {len(warnings)} lines logged by mcp.shared.tool_name_validation at registration ==")
    for record in warnings:
        print(f"{record.levelname}: {record.getMessage()}")

    async with Client(mcp) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    print(f"\ntools/list names: {names}")


asyncio.run(main())
