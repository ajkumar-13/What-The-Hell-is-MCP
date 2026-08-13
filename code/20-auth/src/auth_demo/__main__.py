"""Run the protected MCP server over Streamable HTTP. Post 20.

    python -m auth_demo                 # static verifier, the post 20 fixture
    python -m auth_demo --jwt           # validate real JWTs from the toy AS

The port defaults to 8123 because that is the number in every response post 20
quotes. Changing it changes the resource identifier, the well-known metadata
path, and the `aud` value an inbound token has to carry, all three at once,
which is the coupling `app.resource_url` exists to make visible.

There is no stdio mode here, and that omission is the argument. The
specification says implementations on stdio **SHOULD NOT** follow the
authorization specification and should read credentials from the environment
instead. A server the host already spawned as a child process gains no security
boundary from a browser round trip.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from .app import DEFAULT_HOST, DEFAULT_PORT, build_jwt_server, build_static_server


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="auth_demo",
        description="The OAuth 2.1 resource server from post 20.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind. Default {DEFAULT_PORT}, which is what post 20 quotes.",
    )
    parser.add_argument(
        "--jwt",
        action="store_true",
        help=(
            "Validate HS256 JWTs issued by the toy authorization server instead "
            "of using the static token table. Requires `python -m toy_as` to be "
            "running for tokens to be obtainable."
        ),
    )
    parser.add_argument(
        "--as-port",
        type=int,
        default=None,
        help="Port the toy authorization server is on. Only meaningful with --jwt.",
    )
    args = parser.parse_args()

    if args.jwt:
        # Imported here, not at module scope, so the static mode has no
        # dependency at all on the authorization server package.
        #
        # This import is also the honest cost of a symmetric signing key: the
        # resource server has to hold the very key that mints tokens, so the two
        # halves of the demonstration cannot be separated. A real deployment
        # signs with a private key the authorization server alone holds and
        # publishes the public half at a JWKS endpoint, and then this import
        # would not exist. The environment variable is offered so the key can at
        # least be supplied from outside the repository.
        from toy_as.app import DEFAULT_AS_PORT, issuer_for
        from toy_as.provider import DEMO_SIGNING_KEY

        as_port = args.as_port if args.as_port is not None else DEFAULT_AS_PORT
        signing_key = os.environ.get("MCP_DEMO_SIGNING_KEY", DEMO_SIGNING_KEY)
        mcp = build_jwt_server(
            port=args.port,
            issuer=issuer_for(as_port),
            signing_key=signing_key,
        )
    else:
        mcp = build_static_server(port=args.port)

    app = mcp.streamable_http_app(host=DEFAULT_HOST)
    uvicorn.run(app, host=DEFAULT_HOST, port=args.port)


if __name__ == "__main__":
    main()
