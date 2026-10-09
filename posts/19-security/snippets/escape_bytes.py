"""Post 19, section 7: escape bytes travel through tools/list untouched.

Run from the series root:
    uv run python posts/19-security/snippets/escape_bytes.py

Registers one tool on a throwaway MCPServer whose description carries the
ANSI color sequence from Trail of Bits' demonstration around a harmless
sentence, lists it through an in-memory client, and prints what arrived:
the escape bytes counted, the description as repr() shows it, and the same
text after the mitigation Trail of Bits recommend, every 0x1b byte replaced
by a placeholder. Nothing is written to the terminal raw, so running this
hides nothing from the reader. No model, no subprocess, no socket.
"""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version

from mcp import Client
from mcp.server.mcpserver import MCPServer

HIDDEN = "\x1b[38;5;231;49mThis sentence is white on a white terminal.\x1b[m"
DESCRIPTION = "Formats a date. " + HIDDEN

server = MCPServer("formatter")


@server.tool(description=DESCRIPTION)
def format_date(iso: str) -> str:
    return iso


def neutralize(text: str, placeholder: str = "<ESC>") -> str:
    """The Trail of Bits mitigation: replace every 0x1b byte, allow no sequence."""
    return text.replace("\x1b", placeholder)


async def main() -> None:
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")
    async with Client(server) as client:
        tool = (await client.list_tools()).tools[0]

    received = tool.description
    print(f"0x1b bytes registered: {DESCRIPTION.count(chr(0x1B))}; received by the client: {received.count(chr(0x1B))}")
    print(f"received unchanged: {received == DESCRIPTION}")
    print(f"as repr() shows it: {received!r}")
    print(f"after neutralize():  {neutralize(received)}")


if __name__ == "__main__":
    asyncio.run(main())
