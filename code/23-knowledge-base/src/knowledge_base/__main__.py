"""Entry point.

    python -m knowledge_base                  # stdio, what a desktop host spawns
    python -m knowledge_base --http           # Streamable HTTP on 127.0.0.1:8000/mcp

Two things here are worth more attention than they usually get, because both
are places the previous edition of this post was wrong.

## `run()` takes the transport positionally, and the transport options are
## keyword-only

The v2 signature is `run(transport="stdio", **kwargs)`, and the keyword
arguments are forwarded to `run_streamable_http_async(...)`, whose real
signature is:

    run_streamable_http_async(*, host="127.0.0.1", port=8000,
                              streamable_http_path="/mcp", json_response=False,
                              stateless_http=False, event_store=None,
                              retry_interval=None, transport_security=None)

`host` and `port` therefore live on `run()`, never on the `MCPServer(...)`
constructor. `MCPServer("knowledge-base", port=9000)` is a `TypeError`.

## The endpoint path is `/mcp`, with no trailing slash

`streamable_http_path` defaults to `"/mcp"`, and that is the only path served.
Measured against this server, with the body elided:

    POST /mcp   -> 200
    POST /mcp/  -> 307, Location: http://127.0.0.1:8000/mcp

So `/mcp/` is not served; Starlette answers it with a redirect. Whether that
rescues you depends entirely on the client at the other end. The SDK's own HTTP
client follows redirects and works. A client configured not to follow them
fails, and so does anything that drops the request body on the redirect. Even
when it works you have bought a wasted round trip on every single call, and any
proxy or authenticating gateway in front of the server is one more place for
that redirect to go wrong.

Write `/mcp`. Every URL in `clients/` ends that way.
"""

from __future__ import annotations

import argparse

from . import mcp
from .app import log

# The SDK's default. Named here so the log line below and the `clients/` files
# cannot drift apart from what is actually served.
STREAMABLE_HTTP_PATH = "/mcp"


def main() -> None:
    parser = argparse.ArgumentParser(prog="knowledge-base")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        # Bind to loopback by default. This server is read-only, but "read-only"
        # and "safe to expose to your whole network" are different claims, and
        # only one of them is true by default.
        #
        # Note also that the SDK arms DNS-rebinding protection automatically for
        # 127.0.0.1, localhost, and ::1. Behind a real hostname you must pass
        # transport_security=TransportSecuritySettings(...) or every request
        # comes back 421 Misdirected Request.
        log.info(
            "serving Streamable HTTP on http://%s:%d%s",
            args.host,
            args.port,
            STREAMABLE_HTTP_PATH,
        )
        mcp.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            streamable_http_path=STREAMABLE_HTTP_PATH,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
