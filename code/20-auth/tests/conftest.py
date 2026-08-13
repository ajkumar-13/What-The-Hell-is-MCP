"""Shared helpers: the two ASGI apps, and the smallest legal MCP request.

Post 20. Everything in this suite runs in process. `streamable_http_app()`
returns a Starlette application, `httpx2.ASGITransport` speaks to it directly,
and no socket is ever bound. That matters for more than speed: the DNS rebinding
protection the software development kit (SDK) arms for a loopback host is the
same middleware seeing the same `Host` header here as in production, so the
tests exercise the real request path rather than a mock of it.

The client library is `httpx2`, not `httpx`. The 2.x SDK ships its own fork, and
importing `httpx` here would get a different library whose exception types the
SDK never raises.

## Why there is not a single `yield` fixture in this file

The first version of this suite handed each test a client from an async yield
fixture, and all eight tests failed with:

    RuntimeError: Attempted to exit cancel scope in a different task
    than it was entered in

The application's lifespan and the client both own anyio task groups, and a task
group must be exited by the task that entered it. A yield fixture is torn down
from a different task than the one that ran the test body, so the exit lands in
the wrong place. Post 12 section 4 gives this its own section, and
`code/05-first-server/tests/` hits the identical rule.

So the helpers below are async context managers, and every test opens one with
`async with` in its own body. That is the house pattern for this series, and it
is not a style preference: it is the only arrangement that works.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets
from collections.abc import AsyncIterator

import httpx2
from starlette.applications import Starlette

from auth_demo.app import DEFAULT_HOST, DEFAULT_PORT, build_jwt_server, build_static_server
from toy_as.app import DEFAULT_AS_PORT, build_app, issuer_for
from toy_as.provider import DEMO_SIGNING_KEY

PROTOCOL_VERSION = "2026-07-28"

#: Every envelope key the server demands on `params._meta`.
#:
#: Worth knowing before you spend an afternoon on it: the specification lists
#: `clientInfo` as SHOULD, but `mcp==2.0.0b2` rejects a request without it,
#: with `-32602` and the message "params._meta must carry the reserved
#: protocol-version, client-info and client-capabilities envelope keys". All
#: three are required in practice. Omit one while debugging a token and the
#: failure arrives as a `400` that says nothing about authorization at all,
#: which is a confusing place to land.
REQUEST_META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "post-20-tests", "version": "0"},
}


def mcp_request(method: str = "tools/list", request_id: int = 1) -> dict:
    """The smallest legal MCP request body."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {"_meta": dict(REQUEST_META)},
    }


def mcp_headers(method: str = "tools/list", token: str | None = None) -> dict[str, str]:
    """Headers for an MCP POST, with the two routing headers SEP-2243 requires."""
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    RFC 7636 section 4.2: the challenge is the base64url encoding, with padding
    stripped, of the SHA-256 digest of the verifier. The SDK's `TokenHandler`
    recomputes exactly this and compares, which is why nothing in this project
    implements the comparison itself.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@contextlib.asynccontextmanager
async def _serve(app: Starlette, base_url: str) -> AsyncIterator[httpx2.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=base_url) as client:
            yield client


def static_server() -> contextlib.AbstractAsyncContextManager[httpx2.AsyncClient]:
    """The resource server in the configuration post 20 sections 4, 9 and 10 quote."""
    app = build_static_server().streamable_http_app()
    return _serve(app, f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")


def authorization_server() -> contextlib.AbstractAsyncContextManager[httpx2.AsyncClient]:
    """The toy authorization server."""
    return _serve(build_app(), issuer_for(DEFAULT_AS_PORT))


def jwt_server() -> contextlib.AbstractAsyncContextManager[httpx2.AsyncClient]:
    """The resource server configured to trust the toy authorization server."""
    app = build_jwt_server(
        issuer=issuer_for(DEFAULT_AS_PORT),
        signing_key=DEMO_SIGNING_KEY,
    ).streamable_http_app()
    return _serve(app, f"http://{DEFAULT_HOST}:{DEFAULT_PORT}")
