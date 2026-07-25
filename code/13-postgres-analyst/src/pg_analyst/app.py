"""The server instance itself.

Everything else in this package imports `mcp` from here and hangs a tool or a
resource off it. Keeping the instance in its own module is what lets the
registrations live in separate files without a circular import.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from .database import db, writes_enabled

# Read .env before anything else looks at os.environ. Both posts in this pair
# show a .env file, and neither earlier draft of this code actually called
# load_dotenv(), so DATABASE_URL was only ever picked up when the host happened
# to inject it into the process environment itself. Loading it here, once, at
# import time, is the fix. Every other module reads its configuration lazily,
# at call time, so this line is guaranteed to have run first.
load_dotenv()

# Under the stdio transport, stdout IS the protocol channel. Anything printed
# there is parsed as a JSON-RPC frame, and a stray print() will break the
# connection in a way that is genuinely hard to diagnose. Logging must go to
# stderr, which the host collects and shows you.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("postgres-analyst")


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[None]:
    """Open the pools on startup, close them on shutdown.

    The lifespan runs once per process, including under the in-memory test
    client, so anything expensive or fallible belongs here rather than in a
    tool body where it would run on every call.

    `ensure_audit_table()` goes through the **write** pool. An earlier version
    of this server ran it through the read-only pool, which meant a server
    pointed at a correctly hardened read-only role could not start at all:
    `CREATE TABLE` under `default_transaction_read_only` fails, the lifespan
    raised, and the host reported a server that died on launch.
    """
    # Imported here rather than at module scope because audit.py registers a
    # resource on `mcp`, which does not exist until the bottom of this file.
    from .audit import ensure_audit_table

    await db.connect()

    if writes_enabled() and db.writes_configured:
        await ensure_audit_table()
        log.info("postgres-analyst ready (reads and writes)")
    else:
        # Either writes are switched off, or ENABLE_WRITES was set without a
        # DATABASE_WRITE_URL to go with it. `connect()` has already logged the
        # second case; there is no audit table to create either way, and the
        # write tool refuses with a message that says which it was.
        log.info("postgres-analyst ready (reads only)")

    try:
        yield
    finally:
        await db.close()


mcp = MCPServer("postgres-analyst", lifespan=lifespan)
