# 20 · Authorization: OAuth 2.1 for MCP servers

> **TL;DR.** A Model Context Protocol (MCP) server that needs authorization is an OAuth 2.1 resource server and nothing more exotic than that, so the whole job is validating a token you did not issue, for an audience that is you. This post covers when the specification tells you *not* to add authorization, the handful of normative requirements a resource server owes, why Dynamic Client Registration is deprecated in favor of Client ID Metadata Documents, and the two attacks that explain the fiddly parts. Every response shown was produced by running [code/20-auth/](../../code/20-auth/), whose test suite asserts each one.
>
> **After reading this you will be able to:**
> - Decide whether your server needs authorization at all, and leave it off correctly on a local server.
> - Publish protected resource metadata and the `WWW-Authenticate` challenge that points at it.
> - Validate an inbound token, including the audience check, and answer `401` and `403` the way clients expect.
> - Explain the mix-up attack and the confused deputy to whoever reviews your design.

![Three vertical lifelines. On the left, a client. In the middle, highlighted, the MCP server acting as an OAuth 2.1 resource server. On the right, an authorization server. Seven numbered exchanges run between them: an unauthenticated request, a refusal with 401 and a WWW-Authenticate challenge, a fetch of the protected resource metadata document, a fetch of the authorization server's own metadata, a browser authorization step carrying Proof Key for Code Exchange (PKCE) parameters and returning a code and an issuer, a token exchange, and finally the same MCP request repeated with a bearer token and answered with 200. A call-out marks the four exchanges the MCP server is involved in, and notes that it never issues a token.](diagrams/01-three-party-flow.svg)
*The MCP server appears in four of the seven exchanges, and issues nothing.*

---

## 1. Two servers, and only one of them needs this

Here are two servers from earlier in the series.

The system-information server in [code/05-first-server/](../../code/05-first-server/) is launched by the host as a child process and talks over standard input and output (stdio). The database analyst in [code/13-postgres-analyst/](../../code/13-postgres-analyst/) could run the same way, or it could run on a machine in your infrastructure and answer Hypertext Transfer Protocol (HTTP) requests from several people's laptops.

Only the second one needs what this post describes. The specification is unusually direct about it:

> Authorization is **OPTIONAL** for MCP implementations. When supported:
>
> * Implementations using an HTTP-based transport **SHOULD** conform to this specification.
> * Implementations using an STDIO transport **SHOULD NOT** follow this specification, and instead retrieve credentials from the environment.
> * Implementations using alternative transports **MUST** follow established security best practices for their protocol.

Read the middle bullet twice. A stdio server is spawned by the host, inherits the host's environment, and is reachable by nothing else. Bolting an OAuth flow onto it adds a browser round trip, a token store, and a refresh loop in exchange for no security boundary that did not already exist. Put the credential in an environment variable, read it at startup, and move on. [Post 23](../23-multi-client/index.md) shows where each host lets you set one.

So the rest of this post assumes a server on the Streamable HTTP transport, reachable by more than one person.

## 2. The three roles, and which one you are

OAuth has three parties and MCP maps onto them without inventing anything:

| OAuth 2.1 role | Who plays it | What it does |
|---|---|---|
| Resource server | **your MCP server** | Accepts requests bearing an access token, validates the token, serves or refuses |
| Client | the MCP client inside the host | Obtains a token and attaches it to every request |
| Authorization server | somebody else, usually | Authenticates the user and issues tokens |

Verbatim, from the specification: "A protected *MCP server* acts as an OAuth 2.1 resource server, capable of accepting and responding to protected resource requests using access tokens." And: "The implementation details of the authorization server are beyond the scope of this specification. It may be hosted with the resource server or a separate entity."

That last sentence is the load-bearing one. You are almost certainly not writing an authorization server. You are writing the least glamorous of the three roles, and it has a short and finite job description.

Two things a resource server explicitly does not do. It does not run a login page. It does not mint, refresh, or revoke anything. If your design has your MCP server storing passwords, you have accidentally started writing an authorization server, and you should stop and point at an existing one instead.

## 3. What the specification actually requires of you

There are more normative statements in the authorization pages than anyone wants to read, but the ones aimed at a resource server fit on one screen. Each is quoted from the specification pages linked at the end.

**Publish metadata.** "MCP servers **MUST** implement OAuth 2.0 Protected Resource Metadata (RFC9728)." The document you serve "**MUST** include the `authorization_servers` field containing at least one authorization server."

**Advertise where that metadata lives.** A server **MUST** implement at least one of two discovery mechanisms: the `resource_metadata` parameter in a `WWW-Authenticate` header on a `401 Unauthorized`, or a well-known Uniform Resource Identifier (URI). Clients **MUST** support both.

**Validate the token.** "MCP servers, acting in their role as an OAuth 2.1 resource server, **MUST** validate access tokens as described in OAuth 2.1 Section 5.2."

**Validate the audience.** "MCP servers **MUST** validate that access tokens were issued specifically for them as the intended audience." And, in the security considerations: servers "**MUST** only accept tokens specifically intended for themselves and **MUST** reject tokens that do not include them in the audience claim or otherwise verify that they are the intended recipient of the token."

**Never forward the client's token.** Where your server calls an upstream application programming interface (API), the specification is explicit: "If the MCP server makes requests to upstream APIs, it may act as an OAuth client to them. The access token used at the upstream API is a separate token, issued by the upstream authorization server. The MCP server **MUST NOT** pass through the token it received from the MCP client."

**Check every request, and do not use a session as the check.** "MCP servers that implement authorization **MUST** verify all inbound requests. MCP Servers **MUST NOT** use sessions for authentication."

That last pair deserves a note. Revision 2026-07-28 removed protocol sessions outright, along with `Mcp-Session-Id` and the `initialize` handshake (SEP-2567 and SEP-2575), so there is no protocol session left to misuse. The rule now bites on sessions you invent yourself: a cookie your reverse proxy sets after the first authorized call, an in-memory map from connection to user, a cache keyed on client address. Every request carries its own token, so validate that token, every time.

And two rules that are easy to get right and expensive to get wrong. Tokens travel in the `Authorization` header: "Access tokens **MUST NOT** be included in the URI query string." Separately, from the transports page, a server "**MUST** validate the `Origin` header on all incoming connections to prevent DNS rebinding attacks", where DNS is the Domain Name System, and where `Origin` is present and invalid, "servers **MUST** respond `403 Forbidden`". [Post 21](../21-deploying/index.md) shows a measured case where that protection is silently off.

## 4. Protected resource metadata, and the chain it starts

Picture the chain before the fields. A client that knows nothing but your endpoint asks four questions in order: where do I authenticate, what does that authorization server support, how do I identify myself to it, and what token do I end up with.

![A left-to-right chain of four steps with the exact Uniform Resource Locator (URL) at each hop. Step one, an unauthenticated POST to the MCP endpoint returns 401 with a WWW-Authenticate header whose resource_metadata parameter is a URL. Step two, a GET of that URL returns the protected resource metadata document, whose fields resource, authorization_servers, scopes_supported and bearer_methods_supported are each labeled with what the client does with them. Step three shows the fallback path used when no header is present, probing the well-known URI with the resource path inserted and then the well-known URI at the root. Step four shows the client building the authorization server metadata URL and probing OAuth 2.0 metadata before OpenID Connect discovery.](diagrams/02-metadata-discovery.svg)
*Two ways in, one document, and a fallback the client is required to try.*

The wire format first. An unauthenticated request to a protected server produces this, which is a real response from the demonstration server described in section 9:

```http
HTTP/1.1 401 Unauthorized
content-type: application/json
www-authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp"

{"error": "invalid_token", "error_description": "Authentication required"}
```

The client reads `resource_metadata`, fetches it, and gets the document RFC 9728 defines:

```json
{
  "resource": "http://127.0.0.1:8123/mcp",
  "authorization_servers": ["https://auth.example.com"],
  "scopes_supported": ["system:read"],
  "bearer_methods_supported": ["header"]
}
```

Four fields, four jobs. `resource` is the canonical identifier of *you*, and it is the value the client will put in the `resource` parameter of its authorization and token requests so that the token it receives is audienced at you. `authorization_servers` names who can issue tokens you will accept. `scopes_supported` is your published minimum, not your full catalog. `bearer_methods_supported` says the token arrives in a header.

Note the well-known path. RFC 9728 inserts the well-known segment between the host and the resource path, so a server at `https://example.com/public/mcp` publishes at `https://example.com/.well-known/oauth-protected-resource/public/mcp`. A server at the root publishes at `https://example.com/.well-known/oauth-protected-resource`. Those are different URLs, and a client that cannot find the header falls back to probing them in that order. On the demonstration server, the path-inserted URL returns `200` and the root URL returns `404`, which is correct and is also the single most common "why can my client not discover my server" cause.

## 5. Validating the token, including the check everyone skips

![A vertical gate diagram. A bearer token enters at the top and passes through five checks in order: is a token present, is it cryptographically valid, has it expired, was it issued for this server as its audience, and does it carry the scopes this operation needs. Each check has a labeled failure exit on the right: the first four exit with 401 Unauthorized, the fifth exits with 403 Forbidden and an insufficient_scope challenge. Below the gate, a separate blocked arrow shows the validated token being forwarded to an upstream API with a cross through it, annotated MUST NOT pass through.](diagrams/03-token-validation-gate.svg)
*Five checks, two failure codes, and one arrow that must never be drawn.*

Four of these five checks are the ones any bearer-token middleware already does. The fourth is the one that gets skipped, and it is the one the specification spends the most words on.

Here is the failure it prevents. Your company runs one identity provider. It issues tokens to a dozen internal services. A user authorizes a token for the calendar service, and something later presents that token to your MCP server. If you validate only the signature and the expiry, the token verifies perfectly and you serve the request. You have just accepted a credential that a user never granted you, and the security specification names the consequence: "This breaks a fundamental OAuth security boundary, allowing attackers to reuse legitimate tokens across different services than intended."

The fix is to check that the token names you. For a JavaScript Object Notation (JSON) Web Token that is the `aud` claim; for an opaque token it is whatever your introspection endpoint returns as the audience or resource. Either way the value you compare against is the `resource` you published in your metadata, and the client is required to have asked for it: "MCP clients **MUST** implement Resource Indicators for OAuth 2.0 as defined in RFC 8707" and **MUST** include the `resource` parameter "in both authorization requests and token requests".

The second half of the same rule is about what you do *after* the token is good. If your server calls a third-party API, it needs its own token from that service's authorization server. The client's token is not that token, and forwarding it is the anti-pattern the specification calls token passthrough: "MCP servers **MUST NOT** accept any tokens that were not explicitly issued for the MCP server." [Post 19](../19-security/index.md) walks the attack all the way through; the one-line version is that the downstream service logs the request as coming from you, and neither of you can tell who really made it.

## 6. Scopes, and the difference between 401 and 403

Two status codes, two meanings, and clients branch on them:

| Status | Meaning | When |
|---|---|---|
| `401` | Unauthorized | "Authorization required or token invalid" |
| `403` | Forbidden | "Invalid scopes or insufficient permissions" |
| `400` | Bad Request | "Malformed authorization request" |

The distinction is worth being pedantic about. A `401` tells the client to go and get a token. A `403` tells it that the token is fine but does not carry enough, which starts a step-up flow rather than a fresh login. Return `401` where you meant `403` and every insufficient-scope failure turns into a needless re-authentication.

For the runtime insufficient-scope case the specification says a server **SHOULD** answer `403` with a `WWW-Authenticate` header carrying `error="insufficient_scope"`, a `scope` parameter naming the minimum scopes needed, and the `resource_metadata` URL. It also says to emit all the scopes an operation needs at once: "Challenging incrementally (returning one missing scope, then another on the subsequent retry) forces multiple authorization round-trips for a single operation and degrades user experience."

Minimization is the other half. Publishing every scope you have in `scopes_supported` invites clients to request all of them, and the security guidance names the common mistakes plainly: "Publishing all possible scopes in `scopes_supported`", "Using wildcard or omnibus scopes (`*`, `all`, `full-access`)". Publish the minimum that makes discovery and reading work, and challenge upward for the rest.

## 7. Getting a client ID, and why DCR is now the fallback

A client cannot start an OAuth flow without a `client_id`. Historically MCP clients got one through Dynamic Client Registration (DCR, RFC 7591): call the authorization server's `/register` endpoint, get credentials, proceed. That is now deprecated.

The replacement is Client ID Metadata Documents (CIMD, SEP-991), and the idea is neat. Instead of registering, the client *hosts* its own metadata at an HTTPS URL and uses that URL as its `client_id`. The authorization server sees a URL-shaped identifier, fetches it, and reads the client's name and redirect URIs out of the document:

```json
{
  "client_id": "https://app.example.com/oauth/client-metadata.json",
  "client_name": "Example MCP Client",
  "redirect_uris": ["http://127.0.0.1:3000/callback"],
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

The `client_id` URL "**MUST** use the `https` scheme and contain a path component", the document **MUST** carry at least `client_id`, `client_name` and `redirect_uris`, and the `client_id` inside **MUST** match the URL exactly. No registration call, no per-authorization-server credential, and the identifier is portable: "Client IDs based on Client ID Metadata Documents are portable across authorization servers, since they are self-hosted HTTPS URLs resolved by the authorization server on demand."

Clients that support everything are told to pick in this order:

1. Pre-registered client information, if the client already has it for this server.
2. Client ID Metadata Documents, if the authorization server advertises `client_id_metadata_document_supported`.
3. Dynamic Client Registration, if the authorization server advertises a `registration_endpoint`.
4. Prompt the user to enter the client information.

DCR sits at position three with an explicit warning attached: "Dynamic Client Registration is deprecated. New implementations should use Client ID Metadata Documents instead. This option remains available for backwards compatibility with authorization servers that do not support Client ID Metadata Documents." The deprecated features registry gives it the standard window, deprecated in `2026-07-28` with earliest removal in the first revision released on or after `2027-07-28`.

None of this is your problem as a resource server, with one exception: if you also run the authorization server, supporting CIMD means fetching a URL an unknown party supplied, which is a server-side request forgery risk the specification calls out by name.

## 8. Mix-up, confused deputy, and comparing an issuer byte for byte

Two attacks explain most of the remaining requirements.

**The mix-up attack.** A client talks to many authorization servers over its life. One of them is hostile. The hostile one tries to make the client hand it an authorization code that an honest server issued, which it can then redeem. The specification is blunt about why the obvious defense is not enough: "PKCE alone does not prevent this attack because the client transmits the `code_verifier` to the attacker's token endpoint."

The mitigation is issuer identification (RFC 9207, adopted for MCP as SEP-2468). Before redirecting the user, the client records the `issuer` from the authorization server metadata it validated. The authorization server returns an `iss` parameter in the authorization response. The client compares them before sending the code anywhere.

The comparison has to be exact, and the specification spells out what "exact" excludes. Clients **MUST NOT** "apply scheme or host case folding, default-port elision, trailing-slash, or percent-encoding normalization" before comparing. `https://auth.example.com` and `https://auth.example.com/` are different issuers. So are `https://AUTH.example.com` and `https://auth.example.com`. This is simple string comparison in the RFC 3986 sense, and every helpful URL library you might reach for will quietly break it.

That precision leaks into server configuration too. The Python software development kit (SDK) sets `url_preserve_empty_path=True` on its settings model with a comment saying exactly why: a path-less issuer passed as a string must keep its canonical form, because "RFC 8414/9207 issuer comparison is exact string comparison, so a spurious trailing slash would break it."

**This is not hypothetical, and it cost this series a test.** The toy authorization server in [code/20-auth/](../../code/20-auth/) originally built its issuer string by hand, as `http://127.0.0.1:9123`, and minted the `iss` claim from it. But `build_metadata` publishes the issuer *after* pydantic's `AnyHttpUrl` has normalized it, and normalizing a URL with no path appends a slash. So the metadata document advertised `http://127.0.0.1:9123/` while every token it issued said `http://127.0.0.1:9123`. A client doing exactly what the paragraph above requires, comparing byte for byte with no normalization, would have rejected every token that server ever produced.

Two things make this worth your attention. It only bites an issuer with no path, which is the ordinary case. And a test that compared the token against the same hand-built constant would have passed, because the constant was wrong in both places at once. The regression test asserts the published issuer and the minted `iss` against *each other*, which is the only arrangement that could have caught it.

**The confused deputy.** This one bites servers that proxy to a third-party interface. The server holds one static client ID with the third party. A user authorizes once, the third-party authorization server drops a consent cookie, and from then on the consent screen is skipped for that static client ID. An attacker who can get a client registered against your server, with a redirect URI they control, rides that cookie and collects an authorization code without the user ever seeing a prompt.

The requirement is per-client consent, owned by you and checked before you forward anything: "MCP proxy servers using static client IDs **MUST** obtain user consent for each dynamically registered client before forwarding to third-party authorization servers."

## 9. Wiring it up, and what the SDK gives you free

The Python SDK's server-side surface here is two names. `TokenVerifier` is a `Protocol` with exactly one method:

```python
class TokenVerifier(Protocol):
    """Protocol for verifying bearer tokens."""

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token and return access info if valid."""
```

That is the entire interface. Return an `AccessToken` and the request proceeds; return `None` and the request is refused. `AccessToken` carries `token`, `client_id`, `scopes`, and optional `expires_at`, `resource`, `subject` and `claims`.

The audience check is yours to write, and it goes inside `verify_token`. Nothing in the SDK knows what your canonical resource identifier is until you tell it, and nothing checks the `aud` claim on your behalf:

```python
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
```

`AuthSettings` is the other half. Two of its fields are required, `issuer_url` and `resource_server_url`, and the rest have defaults:

```python
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "secure-demo",
    token_verifier=MyVerifier(),
    auth=AuthSettings(
        issuer_url="https://auth.example.com",
        resource_server_url="https://mcp.example.com/mcp",
        required_scopes=["system:read"],
    ),
)
```

The constructor is strict, and the errors are worth knowing before you meet them. Passing both `auth_server_provider` and `token_verifier` raises `ValueError`. So does setting `auth` with neither, and so does setting either one without `auth`.

What those nine lines buy you is most of section 3. The SDK installs bearer authentication middleware, wraps the MCP route so every request is checked, publishes the RFC 9728 document at the well-known path derived from `resource_server_url`, and attaches the `WWW-Authenticate` challenge to `401` responses with the metadata URL already filled in. That is the `401` and the metadata document quoted in section 4, both produced by exactly this configuration.

**One gap to know about.** The specification says servers **SHOULD** include a `scope` parameter in the `WWW-Authenticate` challenge. Running `mcp==2.0.0b2`, the header carries `error`, `error_description` and `resource_metadata`, and no `scope`:

```http
HTTP/1.1 403 Forbidden
www-authenticate: Bearer error="insufficient_scope", error_description="Required scope: system:read", resource_metadata="http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp"
```

The required scope is in the human-readable description rather than the machine-readable parameter, so a client following the letter of the specification finds nothing to parse. If you need the parameter, add it in your own middleware. Whether the stable 2.0 release closes this is not something this post can tell you.

The SDK also still ships Dynamic Client Registration handlers, and in `2.0.0b2` they carry no deprecation marker. They are off unless you pass `ClientRegistrationOptions(enabled=True)`, and the default is `False`. Leave it that way.

## 10. Testing it, with four requests

You do not need an authorization server to test a resource server. You need a `TokenVerifier` that accepts a fixed string, and four requests. These are real responses from [code/20-auth/](../../code/20-auth/) with `required_scopes=["system:read"]`, a verifier that accepts `good-token` with that scope and `weak-token` with none, and everything else rejected:

```
no Authorization header      401   www-authenticate: Bearer error="invalid_token", ...
Authorization: Bearer nope   401
Authorization: Bearer weak-token   403   www-authenticate: Bearer error="insufficient_scope", ...
Authorization: Bearer good-token   200
```

Four assertions, and they cover the shape of everything in this post: the challenge is present and points somewhere real, an unknown token is refused, a valid token with thin scopes gets `403` rather than `401`, and a good token gets through. Add a fifth for the audience: hand your verifier a token whose `aud` names a different service and assert `401`. That test is the one that fails on a server nobody has audited.

All five are in [tests/test_resource_server.py](../../code/20-auth/tests/test_resource_server.py), along with a sixth that is easy to forget: a *correctly* audienced token must still be accepted. Without it, the audience test would pass just as happily if the verifier refused everything, and a test that cannot tell the behavior it is checking from total failure is not a test.

None of this needs a socket. `streamable_http_app()` returns a Starlette application, so the suite drives the server in process over ASGI, and the whole three-party exchange in [tests/test_flow.py](../../code/20-auth/tests/test_flow.py) runs with no browser and nothing to wait for.

One thing this level of testing still cannot see, and you should check by hand once: nothing here proves the `Origin` check is armed, and section 4 of [post 21](../21-deploying/index.md) has a measured case where it is not. The well-known paths *are* covered, because the split between the path-inserted URL and the root is the most common discovery failure there is: the tests fetch both and assert `200` and `404` respectively.

---

## Common pitfalls

- **Adding OAuth to a stdio server.** The specification says implementations on stdio **SHOULD NOT** follow the authorization specification, and should read credentials from the environment instead. A browser flow for a process the host already spawned buys nothing.
- **Validating the signature and stopping.** A well-formed, unexpired, correctly signed token issued for a different service is still a token you must refuse. The audience check is the whole point of the resource server role.
- **Forwarding the client's token upstream.** If your server calls a third-party API, get your own token from that service's authorization server. Passing the client's token through is the confused deputy, and the specification forbids it outright.
- **Returning `401` for insufficient scope.** It sends the client back through a full login when a step-up would have done. `401` means "get a token"; `403` means "that token is fine and not enough".
- **Publishing metadata at the root when your server is on a path.** RFC 9728 inserts the well-known segment between host and path. A server at `/mcp` publishes at `/.well-known/oauth-protected-resource/mcp`, and a client that finds nothing there gives up.
- **Normalizing the issuer before comparing it.** Trailing slashes, case folding and default-port elision all break RFC 9207 issuer validation. Compare the strings you were given, byte for byte.
- **Caching an authorization decision per connection.** Servers **MUST** verify all inbound requests and **MUST NOT** use sessions for authentication. There are no protocol sessions left in 2026-07-28, so the only sessions that can bite you are the ones you built.

---

## Further reading

- Specification, *"Authorization"*, revision 2026-07-28.
- Specification, *"Client Registration"* and *"Authorization Server Discovery"*, revision 2026-07-28.
- Specification, *"Authorization Security Considerations"*, revision 2026-07-28.
- Specification, *"Security Best Practices"* (token passthrough, confused deputy, session hijacking, scope minimization).
- RFC 9728, *OAuth 2.0 Protected Resource Metadata*; RFC 8707, *Resource Indicators for OAuth 2.0*; RFC 9207, *Authorization Server Issuer Identification*.
- SEP-991, *OAuth Client ID Metadata Documents*; SEP-2468, *RFC 9207 issuer validation*.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 21 — Deploying to production: containers, scaling, and observability](../21-deploying/index.md)**: the container that runs this server, and the measured case where `Origin` validation is off by default.
- **[Post 19 — Security: the attacks the protocol does not stop](../19-security/index.md)**: the attack classes underneath the confused deputy and token passthrough, with the incidents that motivated them.
