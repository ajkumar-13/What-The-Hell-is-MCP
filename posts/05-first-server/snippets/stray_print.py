"""Post 05, section 8: what one print() in a tool does to a real client over stdio.

Run from the series root:
    uv run python posts/05-first-server/snippets/stray_print.py

With no argument this file is the client: it starts itself again with --serve
as a stdio child process, through the SDK's own stdio_client, calls the one
tool, and prints what the client logged and what the call returned. With
--serve it is the broken server, and it is never meant to be started by hand.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback


def serve() -> None:
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("noisy")

    @mcp.tool()
    def answer() -> str:
        """Return the answer."""
        print("about to answer", flush=True)  # the bug: stdout is the protocol channel
        return "42"

    mcp.run()


class LastLineOfTraceback(logging.Formatter):
    """Keep the exception's type and message; drop the stack, which names local paths."""

    def formatException(self, ei) -> str:
        return "".join(traceback.format_exception_only(ei[1])).rstrip()


async def drive() -> None:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(LastLineOfTraceback("%(levelname)s:%(name)s:%(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler])

    params = StdioServerParameters(command=sys.executable, args=[__file__, "--serve"])
    print("== what the client logged ==")
    async with Client(stdio_client(params)) as client:
        result = await client.call_tool("answer", {})
    print("\n== what the call returned ==")
    print(f"text: {result.content[0].text!r}, isError: {str(result.is_error).lower()}")


if __name__ == "__main__":
    if "--serve" in sys.argv[1:]:
        serve()
    else:
        asyncio.run(drive())
