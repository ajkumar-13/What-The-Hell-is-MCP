"""The server instance itself.

Everything else in this package imports `mcp` from here and hangs a tool, a
resource, or a prompt off it. Keeping the instance in its own module is what
lets the registrations live in separate files without a circular import.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

# Under the stdio transport, stdout IS the protocol channel. Anything printed
# there is parsed as a JSON-RPC frame, and a stray print() will break the
# connection in a way that is genuinely hard to diagnose. Logging must go to
# stderr, which the host collects and shows you.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("system-info")

mcp = MCPServer("system-info")
