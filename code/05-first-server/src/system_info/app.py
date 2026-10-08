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
# there before the server starts is parsed as a JSON-RPC frame, and a stray
# print() at import time breaks the connection in a way that is hard to
# diagnose (once mcp.run() is serving, the SDK diverts later prints to stderr).
# Logging goes to stderr, which a host may capture into a log.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("system-info")

mcp = MCPServer("system-info")
