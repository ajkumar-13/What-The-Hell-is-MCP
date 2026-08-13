"""The toy authorization server as a Starlette application. Post 20.

Three lines of assembly and no handlers of our own. `create_auth_routes` returns
the RFC 8414 metadata route, `/authorize`, and `/token`, wired to the provider in
`provider.py`. `/register` and `/revoke` are absent because
`ClientRegistrationOptions` and `RevocationOptions` both default to disabled and
nothing here enables them.

Read `provider.py`'s header before running this. It is a fixture.

## The issuer string, and why it is built by hand

`build_metadata` derives the authorization and token endpoints from the issuer,
and the `iss` claim in every token comes from the same string. RFC 9207 issuer
comparison is exact string comparison, so the value has to survive the round trip
through pydantic's `AnyHttpUrl` unchanged. `ISSUER_TEMPLATE` below produces a
path-less issuer, which is the case where that round trip is least forgiving;
`tests/test_flow.py` asserts what actually comes back out of the metadata
document rather than assuming.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl
from starlette.applications import Starlette

from mcp.server.auth.routes import create_auth_routes

from .provider import DEMO_SIGNING_KEY, ToyAuthorizationServer, demo_client

#: The port the toy authorization server listens on. Distinct from the resource
#: server's 8123 so that the two halves of post 20 can run side by side and the
#: URLs in a packet capture are unambiguous.
DEFAULT_AS_PORT = 9123


def issuer_for(port: int = DEFAULT_AS_PORT) -> str:
    """The issuer identifier, as a plain string, normalized exactly once.

    Loopback HTTP is allowed here only because `validate_issuer_url` in the SDK
    carves out `localhost`, `127.0.0.1`, and `[::1]` for exactly this. RFC 8414
    requires HTTPS everywhere else, and so does the MCP specification.

    ## The trailing slash, which cost this project a test failure

    The value is round-tripped through `AnyHttpUrl` on purpose. Pydantic
    normalizes a path-less URL by appending a slash, so `http://127.0.0.1:9123`
    becomes `http://127.0.0.1:9123/`, while a URL that already has a path is
    left exactly as it was.

    That one byte matters. `build_metadata` publishes the issuer *after*
    pydantic has normalized it, and the first version of this file minted the
    `iss` claim from the raw string *before*. The published issuer and the
    issued `iss` therefore differed by a trailing slash, and RFC 9207 issuer
    comparison is exact string comparison with no normalization, which post 20
    section 8 is explicit about. A correct client would have rejected every
    token this server issued, and every test that compared a token against the
    same raw constant would have passed while it did.

    So there is one canonical form, it is the normalized one, and everything
    downstream reads it from here. The failure mode is worth internalizing: it
    only appears when the issuer has no path, which is the common case.
    """
    return str(AnyHttpUrl(f"http://127.0.0.1:{port}"))


def build_app(
    *,
    port: int = DEFAULT_AS_PORT,
    signing_key: str = DEMO_SIGNING_KEY,
) -> Starlette:
    """Assemble the authorization server and hang the provider off `app.state`.

    Tests reach the provider through `app.state.provider` to inspect issued
    codes without going around the HTTP surface, which is the only shortcut this
    fixture takes and it takes it in exactly one direction: reading.
    """
    issuer = issuer_for(port)
    provider = ToyAuthorizationServer(issuer=issuer, signing_key=signing_key)
    provider.add_client(demo_client())

    routes = create_auth_routes(provider=provider, issuer_url=AnyHttpUrl(issuer))

    app = Starlette(routes=routes)
    app.state.provider = provider
    app.state.issuer = issuer
    return app


__all__ = ["DEFAULT_AS_PORT", "build_app", "issuer_for"]
