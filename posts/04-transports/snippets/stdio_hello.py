"""The same tiny server, run over stdio.

    python stdio_hello.py

The host spawns this file as a child process and talks to it through the pipes
it already owns. stdout carries JSON-RPC frames and nothing else, so every
diagnostic in this file goes to stderr.

Compare with http_hello.py, which is the identical server on the other
transport. Only the last line differs.
"""

from __future__ import annotations

import logging
import platform
import sys

from mcp.server.mcpserver import MCPServer

# stdout is the protocol channel here. Sending log records to stderr is not a
# style preference; a single line of anything else on stdout is a parse error
# at the other end.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("hello")

mcp = MCPServer("hello")


@mcp.tool()
def whoami() -> str:
    """Report the operating system this server is running on."""
    log.info("whoami called")  # stderr: safe
    return f"{platform.system()} {platform.release()}"


if __name__ == "__main__":
    # run() takes the transport as its first positional argument and defaults
    # to "stdio", so a bare run() is the stdio server.
    mcp.run()
