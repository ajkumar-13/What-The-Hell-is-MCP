"""The protected MCP server: an OAuth 2.1 resource server and nothing more.

Post 20. This is the server every response quoted in that post came out of, and
the numbers in it are load-bearing rather than decorative: `127.0.0.1:8123`,
resource `http://127.0.0.1:8123/mcp`, required scope `system:read`, issuer
`https://auth.example.com`. Change one and a quoted response stops matching.

## Nine lines, and what they buy

The whole authorization configuration is a `token_verifier` and an
`AuthSettings`. From those two the SDK installs bearer authentication
middleware, wraps the `/mcp` route so that every request is checked (not the
first one, and not the connection: revision 2026-07-28 removed protocol sessions
outright, and the specification separately forbids using a session as the
authentication check), publishes the RFC 9728 protected resource metadata
document at the well-known path derived from `resource_server_url`, and attaches
the `WWW-Authenticate` challenge to `401` and `403` responses with the metadata
URL already filled in.

What it does not do is the audience check. Nothing in the SDK knows what this
server's canonical identifier is until `verifier.py` is told, and nothing looks
at `aud` on your behalf. That is the whole reason `JWTVerifier` exists.

## Two modes, because they prove different things

`build_static_server` uses `StaticTokenVerifier` and points `issuer_url` at
`https://auth.example.com`, an authorization server that does not exist and is
never contacted. It is the configuration behind post 20 sections 4, 9, and 10:
the `401` challenge, the metadata document, the `403` on thin scopes, and the
`200` on a good token. A resource server can be tested completely without an
authorization server anywhere, and that is the point being made.

`build_jwt_server` uses `JWTVerifier` and points `issuer_url` at the toy
authorization server in `src/toy_as/`. It is the configuration behind
`tests/test_flow.py`, where a real authorization code, a real PKCE exchange, and
a real `aud` claim are walked end to end, and behind the fifth test post 20
section 10 asks for: a token whose `aud` names a different service, refused.

## On the transport

`streamable_http_app()` returns a Starlette application, which means the test
suite drives this server in process over ASGI with `httpx.ASGITransport`. No
socket is opened, no port is bound, and nothing has to wait for a server to come
up. It also means the DNS rebinding protection the SDK auto-enables for a
`127.0.0.1` host is armed in the tests exactly as it is in production, because
it is the same middleware seeing the same `Host` header.
"""

from __future__ import annotations

import os
import platform

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from .verifier import JWTVerifier, StaticTokenVerifier

#: The resource server binds loopback. Post 20 quotes these exact URLs.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8123

#: The path the MCP endpoint is served at, and therefore the path RFC 9728 §3.1
#: inserts the well-known segment *before*: a resource at `/mcp` publishes its
#: metadata at `/.well-known/oauth-protected-resource/mcp`, not at the root.
MCP_PATH = "/mcp"

#: The authorization server named in static mode. It does not exist, is never
#: resolved, and never needs to: publishing who could issue tokens for you is a
#: statement about trust, not a network call.
STATIC_ISSUER = "https://auth.example.com"

#: The published minimum, not a full catalog. Post 20 section 6: publishing
#: every scope you have invites clients to ask for all of them, and wildcard or
#: omnibus scopes are named as a mistake in the security guidance.
REQUIRED_SCOPES = ["system:read"]

INSTRUCTIONS = (
    "A system-information server that requires an OAuth 2.1 access token "
    "audienced at itself and carrying the system:read scope."
)


def resource_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    """The canonical identifier of this server.

    This one string is used three times and must be the same each time: it is
    what goes in `resource` in the metadata document, it is the base the SDK
    derives the well-known metadata URL from, and it is the value an inbound
    token's `aud` claim is compared against. When those three drift apart, a
    client discovers the server, gets a token, and is refused by it.
    """
    return f"http://{host}:{port}{MCP_PATH}"


def _build(
    verifier: object,
    *,
    issuer: str,
    host: str,
    port: int,
    name: str,
) -> MCPServer:
    """Shared assembly. The only difference between the modes is the verifier.

    The constructor is strict in ways worth meeting here rather than in
    production: passing both `auth_server_provider` and `token_verifier` raises
    `ValueError`, so does `auth=` with neither, and so does either one without
    `auth=`. This server is a resource server, so it passes exactly one
    verifier and no provider.
    """
    mcp = MCPServer(
        name,
        instructions=INSTRUCTIONS,
        token_verifier=verifier,  # type: ignore[arg-type]
        auth=AuthSettings(
            issuer_url=issuer,  # type: ignore[arg-type]
            resource_server_url=resource_url(host, port),  # type: ignore[arg-type]
            required_scopes=list(REQUIRED_SCOPES),
        ),
    )

    @mcp.tool(title="System information")
    def system_info() -> dict[str, str | int | None]:
        """Report the operating system and processor count of the host.

        The payload is uninteresting on purpose. What matters is that reaching
        it at all required a token that named this server as its audience and
        carried the system:read scope.
        """
        return {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        }

    return mcp


def build_static_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    verifier: StaticTokenVerifier | None = None,
) -> MCPServer:
    """The server behind post 20 sections 4, 9, and 10.

    `StaticTokenVerifier` accepts `good-token` with `system:read` and
    `weak-token` with nothing, and refuses every other string. No signature, no
    expiry, no audience: this mode exists to demonstrate the SDK's status codes
    and headers, and it is not a model of anything you should deploy.
    """
    return _build(
        verifier or StaticTokenVerifier(),
        issuer=STATIC_ISSUER,
        host=host,
        port=port,
        name="secure-demo",
    )


def build_jwt_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    issuer: str,
    signing_key: str,
) -> MCPServer:
    """The server behind `tests/test_flow.py` and the audience test.

    `issuer` must be byte-identical to the `iss` claim the authorization server
    puts in its tokens and to the issuer it publishes in its own metadata.
    Post 20 section 8: no case folding, no trailing-slash tolerance, no
    default-port elision. It is passed in as a string for that reason.
    """
    verifier = JWTVerifier(
        secret=signing_key,
        issuer=issuer,
        resource=resource_url(host, port),
    )
    return _build(
        verifier,
        issuer=issuer,
        host=host,
        port=port,
        name="secure-demo-jwt",
    )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MCP_PATH",
    "REQUIRED_SCOPES",
    "STATIC_ISSUER",
    "build_jwt_server",
    "build_static_server",
    "resource_url",
]
