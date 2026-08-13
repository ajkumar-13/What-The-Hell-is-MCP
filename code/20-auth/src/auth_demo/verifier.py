"""Two token verifiers, and the one check that makes a resource server a resource server.

Post 20. Nothing in this module talks to a network, which is why the whole of
`tests/test_resource_server.py` runs in process with no socket, no browser, and
no authorization server anywhere.

`TokenVerifier` is the entire server-side authorization surface of the Python
SDK. It is a `Protocol` with one method:

    async def verify_token(self, token: str) -> AccessToken | None

Return an `AccessToken` and the request proceeds. Return `None` and the request
is refused with `401`. Scope enforcement happens after that, in the SDK's
`RequireAuthMiddleware`, which compares the scopes on the returned `AccessToken`
against `AuthSettings.required_scopes` and answers `403` when they fall short.
So this module owns four of the five checks in post 20's gate diagram, and the
SDK owns the fifth.

## Why there are two of them

`StaticTokenVerifier` is a lookup table. It exists because the four requests
post 20 section 10 quotes do not need cryptography to be true: an unknown token
is refused, a known token with thin scopes gets `403`, a known token with the
right scope gets `200`. Testing that against a table rather than against a
signature keeps those assertions about the SDK's behavior instead of about
PyJWT's.

`JWTVerifier` is the one that matters. It performs the audience check, and the
audience check is the whole point of the resource server role. A token can be
correctly signed by an authority you trust, unexpired, and carrying every scope
you require, and still be a token you must refuse, because it was issued for
somebody else. `audience_names_us` below is that refusal, written out as its own
named function so it is impossible to skim past.

## What is deliberately not production-shaped here

`JWTVerifier` validates an HS256 signature against a shared symmetric key. That
is a fixture choice, and it has a real consequence: anybody who can verify a
token can also mint one, so the resource server and the authorization server
hold the same secret. A real deployment uses an asymmetric algorithm and fetches
the authorization server's public keys from its JWKS endpoint, so the resource
server holds nothing that can sign. The algorithm list is still pinned to a
single entry, because accepting "whatever the header says" is how algorithm
confusion attacks work, and that part is production advice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import jwt

from mcp.server.auth.provider import AccessToken

#: The two tokens post 20 section 10 names, and the scopes each carries.
#: `good-token` has the published scope; `weak-token` authenticates fine and
#: authorizes nothing, which is exactly the case that must produce `403` and not
#: `401`. Every other string is unknown and produces `401`.
POST_20_TOKENS: dict[str, list[str]] = {
    "good-token": ["system:read"],
    "weak-token": [],
}


@dataclass
class StaticTokenVerifier:
    """A lookup table of token string to granted scopes.

    Useful for exactly one thing: proving that a resource server answers `401`,
    `403`, and `200` in the right places. It performs no signature check, no
    expiry check, and no audience check, so it must never be reachable from
    anything but a test. `JWTVerifier` is the one that models a real deployment.
    """

    tokens: Mapping[str, list[str]] = field(default_factory=lambda: dict(POST_20_TOKENS))
    client_id: str = "demo-client"

    async def verify_token(self, token: str) -> AccessToken | None:
        """Look the token up. Unknown strings are refused."""
        scopes = self.tokens.get(token)
        if scopes is None:
            return None
        return AccessToken(
            token=token,
            client_id=self.client_id,
            scopes=list(scopes),
        )


def audience_names_us(claims: Mapping[str, Any], resource: str) -> bool:
    """Does this token's `aud` claim name *this* server?

    This is the check post 20 section 5 says everyone skips, and it is four
    lines long. The failure it prevents: one identity provider issues tokens for
    a dozen internal services, a user authorizes a token for the calendar
    service, and something presents that token here. Signature valid. Expiry
    fine. Issuer trusted. Scopes present. And it is still a credential nobody
    granted this server.

    Three details worth keeping.

    `aud` is permitted to be a string or an array of strings (RFC 7519 §4.1.3),
    so both shapes are handled and a bare string is not iterated character by
    character.

    The comparison is exact. No case folding, no trailing-slash tolerance, no
    default-port elision. The same reasoning post 20 section 8 gives for issuer
    comparison applies here: the value being compared is an identifier, not a
    URL to be resolved, and every helpful URL library will quietly break it.

    A missing `aud` is a refusal, not a pass. "The token did not say who it was
    for" is not evidence that it was for us.
    """
    audience = claims.get("aud")
    if audience is None:
        return False
    allowed = audience if isinstance(audience, list) else [audience]
    return resource in allowed


@dataclass
class JWTVerifier:
    """Validate a JWT access token, audience included.

    `resource` is the canonical identifier this server published in its
    protected resource metadata document, and it is the string an inbound token
    must name in `aud`. The client is required to have asked for it: RFC 8707
    says the client sends `resource` on both the authorization request and the
    token request, and the authorization server puts it in the token. That chain
    is what `tests/test_flow.py` walks end to end.

    `issuer` is compared by PyJWT, exactly, against the `iss` claim.
    """

    secret: str
    issuer: str
    resource: str
    algorithms: tuple[str, ...] = ("HS256",)
    leeway: float = 0.0

    async def verify_token(self, token: str) -> AccessToken | None:
        """Run the four checks a resource server owes, then hand back the token.

        In order: is it a well-formed JWT signed by a key we trust, has it
        expired, was it issued by the authorization server we named in our
        metadata, and does it name us as its audience. Any failure is `None`,
        which the SDK turns into `401`.

        Every failure returns the same `None`. That is deliberate: an error
        message that distinguishes "bad signature" from "wrong audience" tells
        an attacker which half of a forged token to fix next.
        """
        try:
            claims = jwt.decode(
                token,
                self.secret,
                # A fixed list, never the token's own `alg` header. Letting the
                # token choose is algorithm confusion, and `none` is on the
                # other end of it.
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                leeway=self.leeway,
                # `aud` is verified below rather than here, so the check is a
                # named function a reader can find, and so a future change to
                # PyJWT's audience semantics cannot silently alter it.
                options={
                    "verify_aud": False,
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                },
            )
        except jwt.PyJWTError:
            return None

        if not audience_names_us(claims, self.resource):
            return None

        scope = claims.get("scope") or ""
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or ""),
            scopes=scope.split(),
            expires_at=int(claims["exp"]),
            # `resource` on the AccessToken records what this server decided the
            # token was for. It is our value, not the token's, so a downstream
            # reader cannot be misled by a claim we did not accept.
            resource=self.resource,
            subject=str(claims["sub"]),
            claims=dict(claims),
        )


__all__ = [
    "POST_20_TOKENS",
    "JWTVerifier",
    "StaticTokenVerifier",
    "audience_names_us",
]
