"""The server instance itself.

Everything else in this package imports `mcp` from here and hangs a tool off
it. Keeping the instance in its own module is what lets the read-only tools and
the writing tools live in separate files without a circular import, and that
separation is the whole architectural point of this server.

Under the stdio transport, stdout IS the protocol channel. Anything printed
there is parsed as a JSON-RPC frame, and a stray print() will break the
connection in a way that is genuinely hard to diagnose. Logging goes to stderr,
which the host collects and shows you.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("k8s-responder")

mcp = MCPServer(
    "k8s-responder",
    instructions=(
        "A Kubernetes first responder. The inspect_* and diagnose_* tools only "
        "read; they cannot change the cluster. The restart, scale, and rollback "
        "tools change it, and each one asks a human to approve the exact change "
        "before it is applied."
    ),
)
