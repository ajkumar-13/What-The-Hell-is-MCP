"""Every response post 20 quotes, asserted against the server that produced it.

Post 20 sections 4, 9 and 10. Each test here pins one figure printed in that
post, so a change in the software development kit (SDK) that would make the post
wrong fails the suite instead of going unnoticed.

That is the entire point of this file. The post's own words are "Every response
shown was produced by running the code"; these tests are what make that sentence
checkable by a reader rather than a claim they have to take on trust.

Every test opens its own client with `async with` in the test body. See the
header of `conftest.py` for why a yield fixture cannot work here.
"""

from __future__ import annotations

import time

import jwt
import pytest

from auth_demo.app import REQUIRED_SCOPES, build_static_server
from auth_demo.verifier import StaticTokenVerifier, audience_names_us
from conftest import jwt_server, mcp_headers, mcp_request, static_server
from toy_as.app import issuer_for
from toy_as.provider import DEMO_SIGNING_KEY

#: The metadata URL every challenge in post 20 points at.
METADATA_URL = "http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp"


# ----------------------------------------------------------------------
# Section 10: the four requests, in the order the post lists them
# ----------------------------------------------------------------------


async def test_no_authorization_header_is_401_with_the_challenge():
    """Row one. The challenge must point somewhere a client can actually fetch."""
    async with static_server() as client:
        response = await client.post("/mcp", json=mcp_request(), headers=mcp_headers())

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        'Bearer error="invalid_token", '
        'error_description="Authentication required", '
        f'resource_metadata="{METADATA_URL}"'
    )


async def test_unknown_token_is_401():
    """Row two. An unrecognized string is refused, and refused the same way."""
    async with static_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token="nope")
        )

    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_valid_token_with_thin_scopes_is_403_not_401():
    """Row three, and the distinction post 20 section 6 spends a table on.

    `401` means "get a token". `403` means "that token is fine and it is not
    enough". Answering `401` here sends the client back through a full login
    when a step-up would have done.
    """
    async with static_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token="weak-token")
        )

    assert response.status_code == 403
    challenge = response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert f'error_description="Required scope: {REQUIRED_SCOPES[0]}"' in challenge


async def test_good_token_reaches_the_tool():
    """Row four. Authorization is only interesting if the allowed case works."""
    async with static_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token="good-token")
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["resultType"] == "complete"
    assert [tool["name"] for tool in payload["result"]["tools"]] == ["system_info"]


# ----------------------------------------------------------------------
# The fifth test post 20 section 10 explicitly asks for
# ----------------------------------------------------------------------


async def test_token_audienced_at_another_service_is_refused():
    """Post 20 section 10: "Add a fifth for the audience."

    This is the test that fails on a server nobody has audited. The token below
    is signed by the right authority, is unexpired, and carries the required
    scope. Everything about it is valid except who it was issued for.
    """
    now = int(time.time())
    stolen = jwt.encode(
        {
            "iss": issuer_for(),
            "sub": "someone",
            "aud": "http://127.0.0.1:8123/some-other-service",
            "exp": now + 300,
            "iat": now,
            "scope": "system:read",
            "client_id": "demo-client",
        },
        DEMO_SIGNING_KEY,
        algorithm="HS256",
    )

    async with jwt_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token=stolen)
        )

    assert response.status_code == 401


async def test_a_correctly_audienced_token_is_accepted():
    """The control for the test above.

    Without this, the audience test would still pass if `JWTVerifier` refused
    everything, and a test that cannot distinguish the thing it is testing from
    a total failure is not a test.
    """
    now = int(time.time())
    good = jwt.encode(
        {
            "iss": issuer_for(),
            "sub": "someone",
            "aud": "http://127.0.0.1:8123/mcp",
            "exp": now + 300,
            "iat": now,
            "scope": "system:read",
            "client_id": "demo-client",
        },
        DEMO_SIGNING_KEY,
        algorithm="HS256",
    )

    async with jwt_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token=good)
        )

    assert response.status_code == 200


async def test_expired_token_is_refused():
    """An expired token is refused even though its audience is right."""
    now = int(time.time())
    stale = jwt.encode(
        {
            "iss": issuer_for(),
            "sub": "someone",
            "aud": "http://127.0.0.1:8123/mcp",
            "exp": now - 1,
            "iat": now - 600,
            "scope": "system:read",
            "client_id": "demo-client",
        },
        DEMO_SIGNING_KEY,
        algorithm="HS256",
    )

    async with jwt_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token=stale)
        )

    assert response.status_code == 401


def test_audience_check_handles_both_claim_shapes():
    """RFC 7519 section 4.1.3 permits `aud` to be a string or an array.

    A bare string must not be iterated character by character, and a missing
    claim is a refusal rather than a pass.
    """
    us = "http://127.0.0.1:8123/mcp"

    assert audience_names_us({"aud": us}, us) is True
    assert audience_names_us({"aud": [us, "https://other.example"]}, us) is True
    assert audience_names_us({"aud": "https://other.example"}, us) is False
    assert audience_names_us({"aud": []}, us) is False
    assert audience_names_us({}, us) is False
    # A prefix of our identifier is not our identifier.
    assert audience_names_us({"aud": "http://127.0.0.1:8123"}, us) is False


# ----------------------------------------------------------------------
# Section 4: the metadata document and the well-known path
# ----------------------------------------------------------------------


async def test_metadata_is_served_at_the_path_inserted_url():
    """RFC 9728 section 3.1 inserts the well-known segment between host and path.

    Post 20 section 4 calls a client that cannot find this "the single most
    common 'why can my client not discover my server' cause".
    """
    async with static_server() as client:
        response = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "http://127.0.0.1:8123/mcp",
        "authorization_servers": ["https://auth.example.com"],
        "scopes_supported": ["system:read"],
        "bearer_methods_supported": ["header"],
    }


async def test_metadata_is_not_served_at_the_root():
    """The other half of the same claim: the root URL returns 404."""
    async with static_server() as client:
        response = await client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 404


# ----------------------------------------------------------------------
# Section 9: the SDK gap, pinned so that closing it is noticed
# ----------------------------------------------------------------------


async def test_insufficient_scope_challenge_carries_no_scope_parameter():
    """Post 20 section 9 reports a gap. This test is what keeps that report honest.

    The specification says a server **should** put a `scope` parameter in the
    `WWW-Authenticate` challenge. Running `mcp==2.0.0b2` it does not: the
    required scope appears only in the human-readable `error_description`, so a
    client parsing the machine-readable parameter finds nothing.

    If a later SDK release adds the parameter, this test fails, and the post
    stops being wrong quietly. That is the outcome worth engineering for.
    """
    async with static_server() as client:
        response = await client.post(
            "/mcp", json=mcp_request(), headers=mcp_headers(token="weak-token")
        )

    challenge = response.headers["www-authenticate"]
    assert response.status_code == 403
    assert "scope=" not in challenge
    assert 'error_description="Required scope: system:read"' in challenge


# ----------------------------------------------------------------------
# The verifier itself
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected_scopes"),
    [("good-token", ["system:read"]), ("weak-token", [])],
)
async def test_static_verifier_returns_the_documented_scopes(token, expected_scopes):
    access = await StaticTokenVerifier().verify_token(token)

    assert access is not None
    assert access.scopes == expected_scopes


async def test_static_verifier_refuses_anything_else():
    assert await StaticTokenVerifier().verify_token("something-else") is None


def test_server_publishes_the_scope_it_requires():
    """The published minimum and the enforced minimum are the same list.

    They are read from one constant precisely so they cannot drift: a server
    that advertises `system:read` and enforces something else sends every
    correct client into a loop.
    """
    server = build_static_server()
    assert server.settings.auth is not None
    assert server.settings.auth.required_scopes == REQUIRED_SCOPES
