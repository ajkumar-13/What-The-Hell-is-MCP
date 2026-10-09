"""Post 20, sections 4 and 7: the whole three-party exchange, hop by hop.

Run from the series root:
    uv run python posts/20-authorization/snippets/three_party_flow.py

Starts the project's toy authorization server and its resource server in the
JWT configuration (code/20-auth), both in process over ASGI, and walks the flow
of the post's first figure: the 401 challenge, the protected resource metadata,
the authorization server's metadata, the authorization request with PKCE and a
resource indicator, the RFC 9207 `iss` check done by the SDK's own client helper,
the token exchange, and the repeated MCP request. Nothing listens on a port; the
redirect is read from its `Location` header and never followed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "20-auth"
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import jwt  # noqa: E402
from conftest import authorization_server, jwt_server, mcp_headers, mcp_request, pkce_pair  # noqa: E402
from mcp.client.auth.utils import validate_authorization_response_iss  # noqa: E402
from mcp.shared.auth import OAuthMetadata  # noqa: E402
from toy_as.provider import DEMO_CLIENT_ID, DEMO_REDIRECT_URI, DEMO_SIGNING_KEY  # noqa: E402

logging.getLogger("httpx2").setLevel(logging.WARNING)


async def main() -> None:
    verifier, challenge = pkce_pair()

    async with jwt_server() as rs, authorization_server() as auth:
        r = await rs.post("/mcp", json=mcp_request(), headers=mcp_headers())
        header = r.headers["www-authenticate"]
        prm_url = header.split('resource_metadata="')[1].rstrip('"')
        print(f"1. POST /mcp, no token                   -> {r.status_code}, resource_metadata={prm_url}")

        r = await rs.get(urlparse(prm_url).path)
        prm = r.json()
        resource = prm["resource"]
        print(f"2. GET protected resource metadata      -> {r.status_code}, resource={resource}, "
              f"authorization_servers={prm['authorization_servers']}")

        r = await auth.get("/.well-known/oauth-authorization-server")
        as_meta = r.json()
        print(f"3. GET authorization server metadata    -> {r.status_code}, issuer={as_meta['issuer']}")
        print(f"   code_challenge_methods_supported:       {as_meta['code_challenge_methods_supported']}")
        for key in ("registration_endpoint", "client_id_metadata_document_supported"):
            print(f"   {key + ':':<40}{as_meta.get(key, '(absent)')}")

        r = await auth.get("/authorize", params={
            "response_type": "code",
            "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "system:read",
            "state": "xyz",
            "resource": resource,
        })
        query = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
        print(f"4. GET /authorize, PKCE S256, resource  -> {r.status_code}, "
              f"code={query['code'][:5]}<{len(query['code']) - 5} hex digits>, state={query['state']}, iss={query['iss']}")

        validate_authorization_response_iss(query["iss"], OAuthMetadata.model_validate(as_meta))
        print("   validate_authorization_response_iss: iss equals the metadata issuer, accepted")

        r = await auth.post("/token", data={
            "grant_type": "authorization_code",
            "code": query["code"],
            "code_verifier": verifier,
            "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI,
            "resource": resource,
        })
        body = r.json()
        claims = jwt.decode(body["access_token"], DEMO_SIGNING_KEY, algorithms=["HS256"],
                            options={"verify_aud": False})
        print(f"5. POST /token                          -> {r.status_code}, token_type={body['token_type']}, "
              f"expires_in={body['expires_in']}, scope={body['scope']}")
        print(f"   access token claims: iss={claims['iss']}, aud={claims['aud']}, sub={claims['sub']}")

        r = await rs.post("/mcp", json=mcp_request(), headers=mcp_headers(token=body["access_token"]))
        tools = [t["name"] for t in r.json()["result"]["tools"]]
        print(f"6. POST /mcp with the bearer token      -> {r.status_code}, tools={tools}")

        r = await auth.post("/token", data={
            "grant_type": "authorization_code",
            "code": query["code"],
            "code_verifier": verifier,
            "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI,
            "resource": resource,
        })
        print(f"7. the same code, redeemed again        -> {r.status_code}, error={r.json()['error']}")


if __name__ == "__main__":
    asyncio.run(main())
