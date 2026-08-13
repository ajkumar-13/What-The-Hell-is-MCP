"""The whole three-party exchange, walked in process. Post 20 section 4's diagram.

No browser, no socket, no clock skew. The toy authorization server and the
resource server are two Starlette applications spoken to over ASGI, and between
them they perform a real authorization code exchange with real Proof Key for
Code Exchange (PKCE) verification and a real audience claim.

What this file is actually for is the RFC 8707 chain. The client names the
resource server it wants a token for, on both the authorization request and the
token request; the authorization server copies that value into the token's `aud`
claim; and the resource server refuses anything not audienced at itself. Break
any one of those three links and the token becomes reusable somewhere it was
never meant to go. `test_the_resource_indicator_survives_the_whole_exchange`
asserts each link in turn.

Read the header of `src/toy_as/provider.py` before borrowing anything here. The
authorization server is a fixture, not a product.
"""

from __future__ import annotations

import jwt
import pytest
from httpx2 import Response

from auth_demo.app import resource_url
from conftest import authorization_server, jwt_server, mcp_headers, mcp_request, pkce_pair
from toy_as.app import issuer_for
from toy_as.provider import DEMO_CLIENT_ID, DEMO_REDIRECT_URI, DEMO_SIGNING_KEY

SCOPE = "system:read"


async def _metadata(client) -> dict:
    """Step one of the diagram: what does this authorization server support?"""
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    return response.json()


async def _authorize(client, *, challenge: str, resource: str, state: str = "xyz") -> Response:
    """Step two: approve and redirect back with a code.

    `follow_redirects` stays off because the redirect target is the client's own
    callback on a port nothing is listening on. The `Location` header is the
    payload, not the page behind it.
    """
    return await client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": SCOPE,
            "state": state,
            "resource": resource,
        },
    )


def _code_from(response: Response) -> tuple[str, dict[str, str]]:
    """Pull the authorization code out of the redirect, with the rest of the query."""
    from urllib.parse import parse_qs, urlparse

    assert response.status_code in (302, 307), response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    flat = {key: values[0] for key, values in query.items()}
    return flat["code"], flat


async def _exchange(client, *, code: str, verifier: str, resource: str) -> Response:
    """Step three: trade the code for a token, proving possession of the verifier."""
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI,
            "resource": resource,
        },
    )


# ----------------------------------------------------------------------


async def test_the_resource_indicator_survives_the_whole_exchange():
    """The RFC 8707 chain, link by link, ending at a `200` from the MCP server."""
    resource = resource_url()
    verifier, challenge = pkce_pair()

    async with authorization_server() as auth:
        metadata = await _metadata(auth)

        # RFC 9207 / SEP-2468: the issuer a client compares byte for byte.
        assert metadata["issuer"] == issuer_for()
        assert "S256" in metadata["code_challenge_methods_supported"]

        redirect = await _authorize(auth, challenge=challenge, resource=resource)
        code, query = _code_from(redirect)

        # The `iss` on the redirect is what lets a client detect the mix-up
        # attack before it sends the code anywhere.
        assert query["iss"] == issuer_for()
        assert query["state"] == "xyz"

        token_response = await _exchange(auth, code=code, verifier=verifier, resource=resource)

    assert token_response.status_code == 200
    body = token_response.json()
    assert body["token_type"] == "Bearer"

    claims = jwt.decode(
        body["access_token"],
        DEMO_SIGNING_KEY,
        algorithms=["HS256"],
        audience=resource,
        issuer=issuer_for(),
    )
    # The link that matters: the resource the client asked for is the audience
    # the token carries.
    assert claims["aud"] == resource
    assert claims["scope"] == SCOPE

    async with jwt_server() as rs:
        response = await rs.post(
            "/mcp",
            json=mcp_request(),
            headers=mcp_headers(token=body["access_token"]),
        )

    assert response.status_code == 200
    assert response.json()["result"]["resultType"] == "complete"


async def test_the_published_issuer_and_the_minted_iss_are_byte_identical():
    """RFC 9207 comparison is exact, so one byte of drift breaks every token.

    This is a regression test for a real bug in this project. `build_metadata`
    publishes the issuer after pydantic's `AnyHttpUrl` has normalized it, which
    appends a trailing slash to a path-less URL. The first version of the toy
    authorization server minted `iss` from the un-normalized string, so the
    document advertised `http://127.0.0.1:9123/` while every token said
    `http://127.0.0.1:9123`. A client following post 20 section 8 to the letter,
    comparing byte for byte with no normalization, would have rejected all of
    them.

    Asserting the two against each other, rather than each against a constant,
    is what makes this catchable: a shared constant would have been wrong in
    both places at once and the test would have passed.
    """
    resource = resource_url()
    verifier, challenge = pkce_pair()

    async with authorization_server() as auth:
        metadata = await _metadata(auth)
        redirect = await _authorize(auth, challenge=challenge, resource=resource)
        code, query = _code_from(redirect)
        token_response = await _exchange(auth, code=code, verifier=verifier, resource=resource)

    claims = jwt.decode(
        token_response.json()["access_token"],
        DEMO_SIGNING_KEY,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )

    assert metadata["issuer"] == claims["iss"]
    assert metadata["issuer"] == query["iss"]


async def test_a_wrong_code_verifier_is_rejected():
    """PKCE, doing its job. The SDK performs this check; nothing here reimplements it."""
    resource = resource_url()
    _, challenge = pkce_pair()
    other_verifier, _ = pkce_pair()

    async with authorization_server() as auth:
        redirect = await _authorize(auth, challenge=challenge, resource=resource)
        code, _ = _code_from(redirect)
        token_response = await _exchange(
            auth, code=code, verifier=other_verifier, resource=resource
        )

    assert token_response.status_code == 400
    assert token_response.json()["error"] == "invalid_grant"


async def test_an_authorization_code_is_single_use():
    """RFC 6749 section 10.5. A replayed code must not mint a second token."""
    resource = resource_url()
    verifier, challenge = pkce_pair()

    async with authorization_server() as auth:
        redirect = await _authorize(auth, challenge=challenge, resource=resource)
        code, _ = _code_from(redirect)

        first = await _exchange(auth, code=code, verifier=verifier, resource=resource)
        second = await _exchange(auth, code=code, verifier=verifier, resource=resource)

    assert first.status_code == 200
    assert second.status_code == 400


async def test_authorize_refuses_without_a_resource_indicator():
    """No resource indicator, no audience, no token.

    An unaudienced token is one every correct resource server refuses, so
    failing here produces a legible error instead of an unexplained `401` three
    hops later.
    """
    _, challenge = pkce_pair()

    async with authorization_server() as auth:
        response = await auth.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": DEMO_CLIENT_ID,
                "redirect_uri": DEMO_REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": SCOPE,
                "state": "xyz",
            },
        )

    # The error comes back on the redirect, per RFC 6749 section 4.1.2.1.
    assert response.status_code in (302, 307)
    assert "invalid_target" in response.headers["location"]


async def test_dynamic_client_registration_is_not_mounted():
    """DCR is deprecated in 2026-07-28, and `create_auth_routes` leaves it off.

    Post 20 section 9: the handlers still ship and carry no deprecation marker,
    but they are only mounted when `ClientRegistrationOptions(enabled=True)` is
    passed. Nothing here passes it, so `/register` does not exist.
    """
    async with authorization_server() as auth:
        response = await auth.post("/register", json={"redirect_uris": ["http://x/cb"]})

    assert response.status_code == 404
