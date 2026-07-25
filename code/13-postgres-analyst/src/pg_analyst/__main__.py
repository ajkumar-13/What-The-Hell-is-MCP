"""Entry point.

    python -m pg_analyst                 # stdio, what a desktop host spawns
    python -m pg_analyst --http          # Streamable HTTP on 127.0.0.1:8000

`mcp.run()` is synchronous and takes the transport as its first argument;
stdio is the default.
"""

from __future__ import annotations

import argparse

from . import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="postgres-analyst")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        # Loopback by default. This process holds live database credentials;
        # binding it to 0.0.0.0 hands them to anything on the network that can
        # speak MCP. Post 20 covers what has to be true before that is
        # reasonable.
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
