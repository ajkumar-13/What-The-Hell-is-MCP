"""Post 05, section 8: what a stray print() does to a real client over stdio.

Run from the series root:
    uv run python posts/05-first-server/snippets/stray_print.py

With no argument this file is the client. It starts itself again twice as a
stdio child process, through the SDK's own stdio_client, each time as a server
with one stray print(): first inside the tool, then at import time, before
mcp.run() starts the transport. For each it calls the one tool and prints what
the client logged, what the server wrote to standard error, and what the call
returned. With --serve it is the broken server, and it is never meant to be
started by hand.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import traceback


def serve(where: str) -> None:
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("noisy")

    if where == "import":
        print("noisy server starting", flush=True)  # the bug: stdout is the protocol channel

    @mcp.tool()
    def answer() -> str:
        """Return the answer."""
        if where == "tool":
            print("about to answer", flush=True)  # the bug: stdout is the protocol channel
        return "42"

    mcp.run()


class LastLineOfTraceback(logging.Formatter):
    """Keep the exception's type and message; drop the stack, which names local paths."""

    def formatException(self, ei) -> str:
        return "".join(traceback.format_exception_only(ei[1])).rstrip()


class Collect(logging.Handler):
    """Keep every formatted record, so each run can print what it logged."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.extend(self.format(record).splitlines())


COLLECT = Collect()


async def drive(where: str) -> None:
    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[__file__, "--serve", where])
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
        COLLECT.lines.clear()
        async with Client(stdio_client(params, errlog=errlog)) as client:
            result = await client.call_tool("answer", {})
        errlog.seek(0)
        server_stderr = [line for line in errlog.read().splitlines() if line.strip()]
    print("what the client logged:")
    for line in COLLECT.lines or ["(nothing)"]:
        print(f"    {line}")
    print("what the server wrote to stderr:")
    for line in server_stderr or ["(nothing)"]:
        print(f"    {line}")
    print("what the call returned:")
    print(f"    text: {result.content[0].text!r}, isError: {str(result.is_error).lower()}")


async def main() -> None:
    COLLECT.setFormatter(LastLineOfTraceback("%(levelname)s:%(name)s:%(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[COLLECT])

    print("== 1. print() inside the tool ==")
    await drive("tool")
    print("\n== 2. print() at import time, before mcp.run() ==")
    await drive("import")


if __name__ == "__main__":
    if "--serve" in sys.argv[1:]:
        serve(sys.argv[sys.argv.index("--serve") + 1])
    else:
        asyncio.run(main())
