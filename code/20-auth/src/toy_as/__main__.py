"""Run the toy authorization server. Post 20.

    python -m toy_as              # http://127.0.0.1:9123

Read the header of `provider.py` before you run this, and do not run it anywhere
that is not your own machine. It authenticates nobody, asks nobody's consent,
and signs tokens with a key published in this repository.

It exists so the flow in post 20's first diagram can be walked for real, with
the SDK's own `/authorize` and `/token` handlers doing the PKCE work, rather
than approximated with a hand-written JWT.
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from .app import DEFAULT_AS_PORT, build_app
from .provider import DEMO_SIGNING_KEY


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="toy_as",
        description="A teaching-fixture OAuth 2.1 authorization server. Never deploy this.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_AS_PORT,
        help=f"Port to bind. Default {DEFAULT_AS_PORT}.",
    )
    args = parser.parse_args()

    # The key is a published constant, not a secret. The environment variable is
    # here so that running the two halves against a key of your own does not
    # require editing the source.
    signing_key = os.environ.get("MCP_DEMO_SIGNING_KEY", DEMO_SIGNING_KEY)

    app = build_app(port=args.port, signing_key=signing_key)
    # Loopback only, and not configurable. Binding this to anything reachable
    # would be handing out tokens to whoever asks.
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
