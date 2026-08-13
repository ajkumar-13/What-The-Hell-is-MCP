"""A toy OAuth 2.1 authorization server, so post 20's flow can be run end to end.

##########################################################################
#                                                                        #
#  TEACHING FIXTURE. NEVER DEPLOY THIS.                                  #
#                                                                        #
#  In-memory state: every client, code, and token lives in a dict that    #
#  dies with the process.                                                #
#  Symmetric key: tokens are signed HS256 with a key published in this    #
#  file, so anyone who can verify a token can mint one.                   #
#  No user authentication: there is no login page and no password check.  #
#  No consent screen: /authorize approves instantly, on behalf of a user  #
#  who was never asked and does not exist.                                #
#  No refresh rotation: no refresh token is issued at all.                #
#  No revocation that works: see `revoke_token` below.                    #
#                                                                        #
#  Its entire job is to make `tests/test_flow.py` a real three-party      #
#  exchange instead of a hand-written JWT. Post 20 section 2 is blunt     #
#  about this: you are almost certainly not writing an authorization      #
#  server, and if your MCP server starts storing passwords you have       #
#  accidentally started writing one. Point at an existing one instead.    #
#                                                                        #
##########################################################################

Post 20. What this module deliberately does *not* do is as instructive as what
it does. The SDK's `create_auth_routes` already implements `/authorize` and
`/token` as RFC 6749 describes them, and `TokenHandler` already does the PKCE
verification: it hashes the submitted `code_verifier` with SHA-256, base64url
encodes it, strips the padding, and compares against the `code_challenge` stored
on the authorization code. None of that is re-implemented here, because the one
guaranteed way to get PKCE wrong is to write it yourself.

What is left for a provider is the part the SDK cannot know: which clients
exist, what an authorization code means, and what a token looks like. That is
this file, and it is roughly a hundred lines.

## The bit worth reading

`exchange_authorization_code` mints the JWT, and the claim that matters is
`aud`. It is set from `AuthorizationCode.resource`, which the SDK carried
through from the `resource` parameter the client sent on both the authorization
request and the token request, per RFC 8707. That is the chain post 20 section 5
describes: the client names the server it intends to call, the authorization
server audiences the token at that server, and the resource server refuses
anything not audienced at itself. Break any link and the token becomes reusable
across services, which is the exact boundary the security specification says
must not be crossed.

`authorize` refuses outright when no `resource` was supplied, rather than
minting a token with no audience. An unaudienced token is a token every resource
server must refuse, so issuing one would only produce a confusing `401` later.
"""

from __future__ import annotations

import secrets
import time

import jwt
from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

#: The HS256 signing key. Published, in a public repository, on purpose: it is
#: not a secret and must never be treated as one. A real authorization server
#: signs with a private key it alone holds and publishes only the public half at
#: a JWKS endpoint, so a compromised resource server cannot mint tokens.
DEMO_SIGNING_KEY = "post-20-demonstration-key-published-on-purpose"

#: The one client this fixture knows about. A real authorization server learns
#: about clients through Client ID Metadata Documents (post 20 section 7), or
#: through an administrator registering them. Dynamic Client Registration is
#: deprecated as of revision 2026-07-28 and stays off here: `create_auth_routes`
#: only mounts `/register` when `ClientRegistrationOptions(enabled=True)` is
#: passed, and nothing in this project passes it.
DEMO_CLIENT_ID = "demo-client"
DEMO_REDIRECT_URI = "http://127.0.0.1:3030/callback"

#: The user who is never asked. A real authorization server puts the
#: authenticated resource owner's identifier here, after a login and a consent
#: screen. This fixture has neither, which is the single largest reason it is
#: not an authorization server.
DEMO_SUBJECT = "user-nobody-authenticated"

#: Authorization codes are short-lived by design; RFC 6749 §4.1.2 says a maximum
#: of ten minutes and recommends much less. The SDK's `TokenHandler` enforces
#: `expires_at` on its own, so this value is genuinely load-bearing.
CODE_LIFETIME_SECONDS = 60

#: Access token lifetime. Short, because this fixture issues no refresh token,
#: and because a stateless token cannot be withdrawn before it expires.
TOKEN_LIFETIME_SECONDS = 300


def demo_client(
    client_id: str = DEMO_CLIENT_ID,
    redirect_uri: str = DEMO_REDIRECT_URI,
    scope: str = "system:read",
) -> OAuthClientInformationFull:
    """The pre-registered public client used by the flow test.

    `token_endpoint_auth_method="none"` makes it a public client, which is what
    an MCP client running on somebody's laptop is: it cannot keep a secret, so
    it does not get one, and PKCE is what stands in for client authentication at
    the token endpoint. The SDK's `ClientAuthenticator` skips the secret check
    for this method and the token request carries only `client_id`.
    """
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=None,
        redirect_uris=[AnyUrl(redirect_uri)],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code"],
        response_types=["code"],
        scope=scope,
    )


class ToyAuthorizationServer(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """The provider half of the toy authorization server. Read the module docstring first."""

    def __init__(
        self,
        *,
        issuer: str,
        signing_key: str = DEMO_SIGNING_KEY,
        token_lifetime: int = TOKEN_LIFETIME_SECONDS,
        code_lifetime: int = CODE_LIFETIME_SECONDS,
    ) -> None:
        #: The exact string that goes in the `iss` claim and in the RFC 9207
        #: `iss` authorization-response parameter. It is stored as a string, not
        #: as a URL object, because post 20 section 8 is about what happens when
        #: something helpfully normalizes it.
        self.issuer = issuer
        self.signing_key = signing_key
        self.token_lifetime = token_lifetime
        self.code_lifetime = code_lifetime
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}

    def add_client(self, client: OAuthClientInformationFull) -> None:
        """Register a client out of band, the way an administrator would."""
        self.clients[str(client.client_id)] = client

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Refused. Dynamic Client Registration is deprecated and off here.

        The SDK still ships the `/register` handler and `2.0.0b2` attaches no
        deprecation marker to it, but the route is only mounted when
        `ClientRegistrationOptions(enabled=True)` is passed, and the default is
        `False`. Nothing in this project turns it on, so this method is
        unreachable through HTTP; raising rather than quietly succeeding keeps
        it that way if somebody later flips the flag by accident.
        """
        raise NotImplementedError(
            "Dynamic Client Registration is deprecated in revision 2026-07-28 and "
            "is not enabled on this authorization server. Use Client ID Metadata "
            "Documents, or register the client out of band."
        )

    # ------------------------------------------------------------------
    # The authorization endpoint
    # ------------------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Approve instantly and redirect back with a code.

        A real authorization server does three things between these two lines
        that this fixture does not do at all: authenticate the user, show them
        what the client is asking for, and record their answer. Everything below
        assumes all three already happened and went well, which is why this file
        is a fixture and not a product.

        The `iss` parameter on the redirect is RFC 9207 issuer identification,
        adopted for MCP as SEP-2468, and it is what lets the client detect the
        mix-up attack post 20 section 8 describes. The client compares it byte
        for byte against the issuer from the metadata it validated, before it
        sends the code anywhere.
        """
        if params.resource is None:
            # RFC 8707. Without a resource indicator there is nothing to put in
            # `aud`, and a token with no audience is one every correct resource
            # server refuses. Fail here, where the error is legible, rather than
            # three hops later as an unexplained 401.
            raise AuthorizeError(
                error="invalid_target",
                error_description=(
                    "A resource indicator (RFC 8707) is required. Send the "
                    "canonical URL of the MCP server this token is for."
                ),
            )

        # RFC 6749 §10.10 requires at least 128 bits of entropy in an
        # authorization code and recommends 160. `token_hex(24)` is 192.
        code = f"code_{secrets.token_hex(24)}"
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + self.code_lifetime,
            client_id=str(client.client_id),
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=DEMO_SUBJECT,
        )
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
            iss=self.issuer,
        )

    # ------------------------------------------------------------------
    # The token endpoint
    # ------------------------------------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Mint the JWT. This is the only place in the project that issues anything.

        By the time this runs, the SDK's `TokenHandler` has already confirmed
        that the code exists, belongs to this client, has not expired, was
        presented with the same `redirect_uri` it was issued against, and that
        the `code_verifier` hashes to the stored `code_challenge`. What is left
        is the claim set.

        `aud` comes from `authorization_code.resource`, not from anything this
        method invents. That single assignment is the RFC 8707 chain post 20
        section 5 describes, and it is what `JWTVerifier.audience_names_us` on
        the resource server checks against its own published identifier.
        """
        # Single use. RFC 6749 §10.5: an authorization code must not be
        # redeemable twice, and the SDK does not delete it for us.
        self.codes.pop(authorization_code.code, None)

        if authorization_code.resource is None:  # pragma: no cover - `authorize` refuses first
            raise TokenError(
                error="invalid_target",
                error_description="This authorization code carries no resource indicator.",
            )

        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": authorization_code.subject or DEMO_SUBJECT,
            "aud": authorization_code.resource,
            "exp": now + self.token_lifetime,
            "iat": now,
            "scope": " ".join(authorization_code.scopes),
            "client_id": authorization_code.client_id,
        }
        access_token = jwt.encode(claims, self.signing_key, algorithm="HS256")

        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.token_lifetime,
            scope=" ".join(authorization_code.scopes) or None,
            # No refresh token. Issuing one means owning rotation, reuse
            # detection, and a revocation story, none of which a fixture should
            # pretend to have.
            refresh_token=None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Always `None`: this server issues no refresh tokens, so none exist."""
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Unreachable, and raises rather than returning junk.

        The SDK's `TokenHandler` calls `load_refresh_token` first and answers
        `invalid_grant` when it returns `None`, so nothing reaches this method
        through HTTP. It raises anyway, because an unsupported grant that
        returns a plausible-looking token is how a fixture becomes a security
        hole the day somebody copies it.
        """
        raise TokenError(
            error="unsupported_grant_type",
            error_description="This authorization server does not issue refresh tokens.",
        )

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        """The SEP-990 jwt-bearer grant, refused explicitly.

        `AuthSettings.identity_assertion_enabled` defaults to `False`, so the
        route rejects the grant before reaching here. Overriding the inherited
        default with the same refusal keeps the decision visible in this file
        rather than inherited from a protocol somebody would have to go and read.
        """
        raise TokenError(
            error="unsupported_grant_type",
            error_description="The JWT bearer grant is not supported by this authorization server.",
        )

    # ------------------------------------------------------------------
    # Token introspection and revocation
    # ------------------------------------------------------------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Decode one of our own tokens.

        Nothing in this project calls this: the resource server runs its own
        `JWTVerifier`, which is the point of the authorization-server/resource-
        server split. It exists because the SDK's `ProviderTokenVerifier` would
        use it if this provider were ever wired into an `MCPServer` directly,
        and an unimplemented method there would silently accept nothing.

        Note that this deliberately does *not* check the audience. Deciding
        whether a token is for you is the resource server's job, and an
        authorization server that answered it would be answering for a server it
        is not.
        """
        try:
            claims = jwt.decode(
                token,
                self.signing_key,
                algorithms=["HS256"],
                issuer=self.issuer,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None

        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or ""),
            scopes=str(claims.get("scope") or "").split(),
            expires_at=int(claims["exp"]),
            resource=claims.get("aud") if isinstance(claims.get("aud"), str) else None,
            subject=claims.get("sub"),
            claims=dict(claims),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """A no-op, and the honest name for it is "this does not work".

        RFC 7009 revocation is not mounted here (`RevocationOptions.enabled`
        defaults to `False`), and it would not help if it were. These access
        tokens are self-contained JWTs that the resource server validates
        offline against a shared key; it never asks this server whether a token
        is still good. Nothing said here can withdraw one before its `exp`.

        That is not a fixture shortcut, it is the standing trade of stateless
        tokens, and the two ways out are short lifetimes or an introspection
        endpoint the resource server actually calls on every request.
        """
        return None


__all__ = [
    "CODE_LIFETIME_SECONDS",
    "DEMO_CLIENT_ID",
    "DEMO_REDIRECT_URI",
    "DEMO_SIGNING_KEY",
    "DEMO_SUBJECT",
    "TOKEN_LIFETIME_SECONDS",
    "ToyAuthorizationServer",
    "demo_client",
]
