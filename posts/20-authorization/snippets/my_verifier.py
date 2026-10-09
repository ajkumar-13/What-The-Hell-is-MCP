"""Post 20, sections 9 and 12: a token verifier that checks the audience, and the SDK's second check.

Run from the series root:
    uv run python posts/20-authorization/snippets/my_verifier.py

`MyVerifier` and the `MCPServer` just below are the two listings of section 9.
`introspect`, further down, reads a dictionary that stands in for the JWT
library or the introspection call a real verifier makes. The script sends four
tokens to the server in process over ASGI (no socket), then repeats the
experiment with two careless verifiers that skip the audience check (one reports
the token's own `aud` as the resource, the other reports this server's
identifier whatever the token says) and with one that checks the audience but
reports no resource at all, printing the warning the SDK logs for it. Last, the
first careless verifier once more with `validate_token_resource` left unset, and
the first clause of the warning the SDK raises for that.
"""

from __future__ import annotations

from mcp.server.auth.provider import AccessToken, TokenVerifier

RESOURCE = "https://mcp.example.com/mcp"


class MyVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        claims = await introspect(token)          # your JWT check or introspection call
        if claims is None:
            return None
        audience = claims.get("aud")
        allowed = audience if isinstance(audience, list) else [audience]
        if RESOURCE not in allowed:               # the check everyone skips
            return None
        return AccessToken(
            token=token,
            client_id=claims["client_id"],
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            resource=RESOURCE,
            subject=claims.get("sub"),
        )


from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "secure-demo",
    token_verifier=MyVerifier(),
    auth=AuthSettings(
        issuer_url="https://auth.example.com",
        resource_server_url="https://mcp.example.com/mcp",
        required_scopes=["system:read"],
        validate_token_resource=True,
    ),
)


import asyncio
import logging
import time
import warnings

import httpx2

NOW = int(time.time())
CLAIMS = {  # what a JWT library or an introspection endpoint would return
    "for-us": {"aud": "https://mcp.example.com/mcp", "exp": NOW + 300},
    "for-calendar": {"aud": "https://calendar.example.com", "exp": NOW + 300},
    "for-us-expired": {"aud": "https://mcp.example.com/mcp", "exp": NOW - 1},
}
for claims in CLAIMS.values():
    claims.update(client_id="c1", scope="system:read", sub="alice")


async def introspect(token: str) -> dict | None:
    return CLAIMS.get(token)


class TrustsTheToken(TokenVerifier):
    """Careless: no audience check, and reports the token's own `aud` as the resource."""

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = await introspect(token)
        if claims is None:
            return None
        return AccessToken(token=token, client_id=claims["client_id"], scopes=claims["scope"].split(),
                           expires_at=claims["exp"], resource=claims["aud"])


class ClaimsItIsForUs(TrustsTheToken):
    """Careless, and wrong: reports this server's identifier whatever the token says."""

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        return access.model_copy(update={"resource": RESOURCE}) if access else None


class ReportsNoResource(MyVerifier):
    """Checks the audience, then leaves `resource` off the AccessToken it returns."""

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        return access.model_copy(update={"resource": None}) if access else None


class Records(logging.Handler):
    """Collects the bearer middleware's log records instead of printing them."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


records = Records()
bearer_log = logging.getLogger("mcp.server.auth.middleware.bearer_auth")
bearer_log.addHandler(records)
bearer_log.propagate = False

REQUEST = {
    "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                         "io.modelcontextprotocol/clientCapabilities": {}}},
}


async def statuses(server: MCPServer) -> str:
    app = server.streamable_http_app()
    results = []
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            for token in ["for-us", "for-calendar", "for-us-expired", "unknown"]:
                r = await client.post("/mcp", json=REQUEST, headers={
                    "content-type": "application/json",
                    "accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                    "Authorization": f"Bearer {token}",
                })
                results.append(f"{token}={r.status_code}")
    return "  ".join(results)


async def main() -> None:
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    print(f"MyVerifier        {await statuses(mcp)}")
    for verifier in (TrustsTheToken(), ClaimsItIsForUs(), ReportsNoResource()):
        records.lines.clear()
        server = MCPServer("secure-demo", token_verifier=verifier, auth=mcp.settings.auth)
        print(f"{type(verifier).__name__:<18}{await statuses(server)}")
    print(f"logged for ReportsNoResource, first of {len(records.lines)}: {records.lines[0]}")

    print()
    print("TrustsTheToken again, with validate_token_resource left unset:")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        unset = mcp.settings.auth.model_dump(exclude={"validate_token_resource"})
        server = MCPServer("secure-demo", token_verifier=TrustsTheToken(), auth=AuthSettings(**unset))
    print(f"{'':<18}{await statuses(server)}")
    first = str(caught[0].message).split(";")[0]
    print(f"warning: {caught[0].category.__name__}: {first}")


if __name__ == "__main__":
    asyncio.run(main())
