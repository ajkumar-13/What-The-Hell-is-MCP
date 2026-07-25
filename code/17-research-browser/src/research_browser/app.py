"""The server instance itself.

Everything else in this package imports `mcp` from here and hangs a tool, a
resource, or a prompt off it. Keeping the instance in its own module is what
lets the registrations live in separate files without a circular import.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .browser import BrowserSession, browser
from .cache import cache

# Under the stdio transport, stdout IS the protocol channel. Anything printed
# there is parsed as a JSON-RPC frame, and a stray print() will break the
# connection in a way that is genuinely hard to diagnose. Logging must go to
# stderr, which the host collects and shows you.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("research-browser")


@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[BrowserSession]:
    """Own the browser process for the lifetime of the server.

    Launching Chromium costs a second or two. Doing it once here, rather than
    once per tool call, is the whole reason the browser is a singleton. The
    `finally` is load-bearing: a Chromium that outlives its server is a leaked
    process the host has no way to reap.

    `browser.start()` is a no-op when a fetcher has already been injected, which
    is how the test suite runs the real protocol path without Playwright
    installed.
    """
    await browser.start()
    log.info("research browser ready (fetcher=%s)", browser.require().name)
    try:
        yield browser
    finally:
        await browser.stop()
        cache.clear()
        log.info("research browser stopped")


mcp = MCPServer("research-browser", lifespan=lifespan)
