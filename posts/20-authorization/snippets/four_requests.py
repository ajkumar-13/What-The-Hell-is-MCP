"""Post 20, sections 4, 5, 6, 10 and 12: the challenge, the metadata document, and the four requests.

Run from the series root:
    uv run python posts/20-authorization/snippets/four_requests.py

Builds the project's protected server (code/20-auth) in the static configuration
and speaks to it in process over ASGI, so no socket is opened. Prints the 401
challenge in full, the protected resource metadata document at the path-inserted
well-known URL and the status at the root well-known URL, the four requests of
section 10, the audience cases against the JWT configuration, and what an
`Origin` from another site gets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "20-auth"
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import jwt  # noqa: E402
from conftest import jwt_server, mcp_headers, mcp_request, static_server  # noqa: E402
from toy_as.app import issuer_for  # noqa: E402
from toy_as.provider import DEMO_SIGNING_KEY  # noqa: E402

logging.getLogger("httpx2").setLevel(logging.WARNING)


def signed(aud: str, exp_offset: int = 300) -> str:
    """A token from the toy authorization server's key, audienced at `aud`."""
    now = int(time.time())
    claims = {
        "iss": issuer_for(),
        "sub": "someone",
        "aud": aud,
        "exp": now + exp_offset,
        "iat": now - 600,
        "scope": "system:read",
        "client_id": "demo-client",
    }
    return jwt.encode(claims, DEMO_SIGNING_KEY, algorithm="HS256")


async def main() -> None:
    async with static_server() as client:
        print("== POST /mcp with no Authorization header ==")
        r = await client.post("/mcp", json=mcp_request(), headers=mcp_headers())
        print(f"HTTP/1.1 {r.status_code} {r.reason_phrase}")
        print(f"content-type: {r.headers['content-type']}")
        print(f"www-authenticate: {r.headers['www-authenticate']}")
        print()
        print(r.text)
        print()

        print("== GET /.well-known/oauth-protected-resource/mcp ==")
        r = await client.get("/.well-known/oauth-protected-resource/mcp")
        print(r.status_code)
        print(json.dumps(r.json(), indent=2))
        print()
        r = await client.get("/.well-known/oauth-protected-resource")
        print(f"== GET /.well-known/oauth-protected-resource == {r.status_code}")
        print()

        print("== the four requests ==")
        cases = [
            ("no Authorization header", None),
            ("Authorization: Bearer nope", "nope"),
            ("Authorization: Bearer weak-token", "weak-token"),
            ("Authorization: Bearer good-token", "good-token"),
        ]
        for label, token in cases:
            r = await client.post("/mcp", json=mcp_request(), headers=mcp_headers(token=token))
            challenge = r.headers.get("www-authenticate")
            error = challenge.split(",")[0] if challenge else "(no challenge)"
            print(f"{label:<34} {r.status_code}   {error}")
            if r.status_code == 200:
                names = [tool["name"] for tool in r.json()["result"]["tools"]]
                print(f"{'':<34}       tools: {names}")
        print()

        print("== the 403 challenge in full ==")
        r = await client.post("/mcp", json=mcp_request(), headers=mcp_headers(token="weak-token"))
        print(f"HTTP/1.1 {r.status_code} {r.reason_phrase}")
        print(f"www-authenticate: {r.headers['www-authenticate']}")
        print(f"scope parameter present: {'scope=' in r.headers['www-authenticate']}")
        print()

        print("== good-token on a bare POST: no body, no content-type ==")
        r = await client.post("/mcp", headers={"Authorization": "Bearer good-token"})
        print(f"{r.status_code}   {r.text.strip()}")
        print()

        print("== good-token, sent with Origin: https://evil.example ==")
        headers = mcp_headers(token="good-token") | {"Origin": "https://evil.example"}
        r = await client.post("/mcp", json=mcp_request(), headers=headers)
        print(f"{r.status_code}   {r.text.strip()}")
        print()

    print("== the JWT configuration: three signed tokens, all with scope system:read ==")
    async with jwt_server() as client:
        for label, token in [
            ("aud = http://127.0.0.1:8123/mcp", signed("http://127.0.0.1:8123/mcp")),
            ("aud = .../some-other-service", signed("http://127.0.0.1:8123/some-other-service")),
            ("aud right, expired a second ago", signed("http://127.0.0.1:8123/mcp", exp_offset=-1)),
        ]:
            r = await client.post("/mcp", json=mcp_request(), headers=mcp_headers(token=token))
            print(f"{label:<34} {r.status_code}")


if __name__ == "__main__":
    asyncio.run(main())
