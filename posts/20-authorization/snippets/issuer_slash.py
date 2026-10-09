"""Post 20, section 8: one trailing slash, and an issuer comparison that refuses it.

Run from the series root:
    uv run python posts/20-authorization/snippets/issuer_slash.py

Prints what pydantic's `AnyHttpUrl` does to a path-less URL and what the SDK's
`AuthSettings` keeps; then rebuilds the first version of the project's toy
authorization server (its issuer string built by hand, its routes given the
parsed URL), reads the issuer it publishes and the `iss` it puts on the
authorization redirect, and hands both to the SDK client's RFC 9207 check. It
does the same for the project's current toy server, and finally runs the check
on three spellings of one issuer. Everything runs in process over ASGI.
"""

from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "20-auth"
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import httpx2  # noqa: E402
from conftest import pkce_pair  # noqa: E402
from mcp.client.auth.exceptions import OAuthFlowError  # noqa: E402
from mcp.client.auth.utils import validate_authorization_response_iss  # noqa: E402
from mcp.server.auth.routes import create_auth_routes  # noqa: E402
from mcp.server.auth.settings import AuthSettings  # noqa: E402
from mcp.shared.auth import OAuthMetadata  # noqa: E402
from pydantic import AnyHttpUrl  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from toy_as.app import build_app  # noqa: E402
from toy_as.provider import DEMO_CLIENT_ID, DEMO_REDIRECT_URI, ToyAuthorizationServer, demo_client  # noqa: E402

RAW = "http://127.0.0.1:9123"


def first_version() -> Starlette:
    """The toy authorization server as it was first written: issuer built by hand."""
    provider = ToyAuthorizationServer(issuer=RAW)
    provider.add_client(demo_client())
    return Starlette(routes=create_auth_routes(provider=provider, issuer_url=AnyHttpUrl(RAW)))


async def published_and_returned(app: Starlette) -> tuple[OAuthMetadata, str]:
    """The metadata the server publishes, and the `iss` on one authorization redirect."""
    _, challenge = pkce_pair()
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url=RAW) as client:
        metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
        r = await client.get("/authorize", params={
            "response_type": "code", "client_id": DEMO_CLIENT_ID,
            "redirect_uri": DEMO_REDIRECT_URI, "code_challenge": challenge,
            "code_challenge_method": "S256", "scope": "system:read",
            "state": "xyz", "resource": "http://127.0.0.1:8123/mcp",
        })
    iss = parse_qs(urlparse(r.headers["location"]).query)["iss"][0]
    return OAuthMetadata.model_validate(metadata), iss


def check(iss: str, metadata: OAuthMetadata) -> str:
    try:
        validate_authorization_response_iss(iss, metadata)
    except OAuthFlowError as exc:
        return f"rejected: {exc}"
    return "accepted"


async def main() -> None:
    print(f"str(AnyHttpUrl({RAW!r}))           = {str(AnyHttpUrl(RAW))!r}")
    print(f"str(AnyHttpUrl({RAW + '/oauth'!r})) = {str(AnyHttpUrl(RAW + '/oauth'))!r}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        settings = AuthSettings(issuer_url=RAW, resource_server_url=None)
    print(f"AuthSettings(issuer_url={RAW!r}).issuer_url = {str(settings.issuer_url)!r}")
    print()

    for label, app in [("first version", first_version()), ("current project", build_app())]:
        metadata, iss = await published_and_returned(app)
        print(f"== {label} ==")
        print(f"published issuer: {str(metadata.issuer)!r}")
        print(f"iss on redirect:  {iss!r}")
        print(f"SDK client check: {check(iss, metadata)}")
        print()

    # Passed as strings, so the metadata model's url_preserve_empty_path applies.
    metadata = OAuthMetadata.model_validate({
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
    })
    print(f"== three spellings against a published issuer of {str(metadata.issuer)!r} ==")
    for iss in ["https://auth.example.com", "https://auth.example.com/", "https://AUTH.example.com"]:
        print(f"{iss!r:<30} {check(iss, metadata)}")


if __name__ == "__main__":
    asyncio.run(main())
