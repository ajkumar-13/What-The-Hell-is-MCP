"""Entry point.

    python -m system_info                 # stdio, what a desktop host spawns
    python -m system_info --http          # Streamable HTTP on 127.0.0.1:8000

`mcp.run()` is synchronous and takes the transport as its first argument;
stdio is the default.
"""

from __future__ import annotations

import argparse

from . import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="system-info")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        # Bind to loopback by default. A local server listening on 0.0.0.0 is
        # reachable by anything on the network, and this one reports on your
        # machine and can terminate processes on it.
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
