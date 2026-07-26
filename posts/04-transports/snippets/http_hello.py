"""The same tiny server, run over Streamable HTTP.

    python http_hello.py            # POST http://127.0.0.1:8000/mcp

Nothing spawns this one. It listens, and any number of clients POST to the one
endpoint it exposes. Drive it with the no-SDK client from Post 03:

    python ../../03-wire-protocol/snippets/raw_discover.py http://127.0.0.1:8000/mcp

Compare with stdio_hello.py. The tool is identical; only the run() call differs.
"""

from __future__ import annotations

import logging
import platform
import sys

from mcp.server.mcpserver import MCPServer

# stdout is free on this transport, because the protocol travels over HTTP.
# Keeping logs on stderr anyway means the same file works either way.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("hello")

mcp = MCPServer("hello")


@mcp.tool()
def whoami() -> str:
    """Report the operating system this server is running on."""
    log.info("whoami called")
    return f"{platform.system()} {platform.release()}"


if __name__ == "__main__":
    # 127.0.0.1, not 0.0.0.0. A local server bound to every interface is
    # reachable by everything on the network. Binding to loopback also arms
    # the SDK's DNS-rebinding protection.
    mcp.run("streamable-http", host="127.0.0.1", port=8000, streamable_http_path="/mcp")
