"""Entry point.

    python -m mcp_app_demo            # stdio
    python -m mcp_app_demo --http     # Streamable HTTP on 127.0.0.1:8000/mcp

Hosts that render MCP Apps generally reach this server over HTTP, because the
widget is fetched with an ordinary `resources/read` and the host wants a URL it
can add as a connector. stdio works identically at the protocol level.

The path is `/mcp`, with no trailing slash. `/mcp/` is answered with a `307`
redirect, which costs a round trip at best and fails outright at worst.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .server import mcp

# stdout is the protocol channel under stdio. Everything diagnostic goes to
# stderr, which every host collects.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-app-demo")
    parser.add_argument("--http", action="store_true", help="serve over Streamable HTTP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port, streamable_http_path="/mcp")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
