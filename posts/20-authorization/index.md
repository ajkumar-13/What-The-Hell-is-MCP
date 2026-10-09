# 20 - Authorization: OAuth 2.1 for MCP servers

> **TL;DR.** A Model Context Protocol (MCP) server that needs authorization is an Open Authorization (OAuth) 2.1 resource server and nothing more exotic than that, so the whole job is validating a token it did not issue, for an audience that is itself. This post covers when the specification says *not* to add authorization, the handful of normative requirements a resource server owes, why Dynamic Client Registration is deprecated in favor of Client ID Metadata Documents, and the two attacks that explain the fiddly parts. Every response, header and status below is printed by a script in `snippets/` that runs the project in [code/20-auth/](../../code/20-auth/) in process against `mcp==2.3.0`, and the project's own suite asserts the values that come from the project.
>
> **Prerequisites:** [Post 19](../19-security/index.md).
> **Safe to skip?** Skip it if the reader can already publish Request for Comments (RFC) 9728 protected resource metadata, refuse a correctly signed token issued for another service, tell a `401` from a `403`, and say why an issuer must be compared byte for byte.
>
> **After reading, you will be able to:**
>
> - Decide whether a server needs authorization at all, and leave it off correctly on a local server.
> - Publish protected resource metadata and the `WWW-Authenticate` challenge that points at it.
> - Validate an inbound token, including the audience check, and answer `401` and `403` the way clients expect.
> - Explain the mix-up attack and the confused deputy to whoever reviews the design.

![Three vertical lifelines. On the left, an MCP client in the host. In the middle, highlighted, the MCP server acting as an OAuth 2.1 resource server. On the right, an authorization server. Seven numbered exchanges run between them: an unauthenticated request, a refusal with 401 and a WWW-Authenticate challenge, a fetch of the protected resource metadata document, then three exchanges between the client and the authorization server alone (its metadata, an authorize request carrying Proof Key for Code Exchange (PKCE) and a resource parameter that returns a code and an issuer, and a token exchange), and finally the same MCP request repeated with a bearer token and answered with 200. A band at the foot states the resource server's job and notes that it never issues a token.](diagrams/01-three-party-flow.svg)

*The MCP server appears in four of the seven exchanges, and issues nothing.*

---

## 1. The question: which servers need authorization at all?

Two servers from earlier in the series make the point.

The system-information server in [code/05-first-server/](../../code/05-first-server/) is launched by the host as a child process and talks over standard input and output (stdio). The database analyst in [code/13-postgres-analyst/](../../code/13-postgres-analyst/) could run the same way, or it could run on a machine in a company's infrastructure and answer Hypertext Transfer Protocol (HTTP) requests from several people's laptops.

Only the second one needs what this post describes. The specification's Authorization page is unusually direct about it:

> Authorization is **OPTIONAL** for MCP implementations. When supported:
>
> * Implementations using an HTTP-based transport **SHOULD** conform to this specification.
> * Implementations using an STDIO transport **SHOULD NOT** follow this specification, and instead retrieve credentials from the environment.
> * Implementations using alternative transports **MUST** follow established security best practices for their protocol.

The middle bullet is the one to read twice. A stdio server is spawned by the host, inherits the host's environment, and is reachable by nothing else. Bolting an OAuth flow onto it adds a browser round trip, a token store, and a refresh loop in exchange for no security boundary that did not already exist. The credential goes in an environment variable, read at startup. [Post 23](../23-multi-client/index.md) shows where each host lets one be set.

So the question this post answers is narrower than "how does OAuth work": if an MCP server on the Streamable HTTP transport, reachable by more than one person, is an OAuth 2.1 resource server and nothing more exotic, what exactly does it owe, and what does it validate?

## 2. The three roles, and which one the server plays

OAuth has three parties and MCP maps onto them without inventing anything:

| OAuth 2.1 role | Who plays it | What it does |
|---|---|---|
| Resource server | **the MCP server** | Accepts requests bearing an access token, validates the token, serves or refuses |
| Client | the MCP client inside the host | Obtains a token and attaches it to every request |
| Authorization server | somebody else, usually | Authenticates the user and issues tokens |

Verbatim, from the specification: "A protected *MCP server* acts as an OAuth 2.1 resource server, capable of accepting and responding to protected resource requests using access tokens." And: "The implementation details of the authorization server are beyond the scope of this specification. It may be hosted with the resource server or a separate entity."

That last sentence is the load-bearing one. A server author is almost certainly not writing an authorization server. 

Two things a resource server explicitly does not do. It does not run a login page. It does not mint, refresh, or revoke anything. A design in which the MCP server stores passwords has accidentally started writing an authorization server, and the fix is to stop and point at an existing one instead. The project's toy authorization server in `src/toy_as/` exists only so the three-party flow can run in a test, and its header says in capitals never to deploy it.

## 3. What the specification requires of a resource server

There are more normative statements in the authorization pages than anyone wants to read, but the ones aimed at a resource server fit on one screen. Each is quoted from the Authorization page and its sections, revision 2026-07-28, unless another page is named.

**Publish metadata.** "MCP servers **MUST** implement OAuth 2.0 Protected Resource Metadata (RFC9728)." The document served "**MUST** include the `authorization_servers` field containing at least one authorization server."

**Advertise where that metadata lives.** A server **MUST** implement at least one of two discovery mechanisms: the `resource_metadata` parameter in a `WWW-Authenticate` header on a `401 Unauthorized`, or a well-known Uniform Resource Identifier (URI). Clients **MUST** support both.

**Validate the token.** "MCP servers, acting in their role as an OAuth 2.1 resource server, **MUST** validate access tokens as described in OAuth 2.1 Section 5.2."

**Validate the audience.** "MCP servers **MUST** validate that access tokens were issued specifically for them as the intended audience." And, in the security considerations: servers "**MUST** only accept tokens specifically intended for themselves and **MUST** reject tokens that do not include them in the audience claim or otherwise verify that they are the intended recipient of the token."

**Never forward the client's token.** Where the server calls an upstream application programming interface (API), the specification is explicit: "If the MCP server makes requests to upstream APIs, it may act as an OAuth client to them. The access token used at the upstream API is a separate token, issued by the upstream authorization server. The MCP server **MUST NOT** pass through the token it received from the MCP client."

**Check every request, and let nothing stand in for the token.** "Note that authorization **MUST** be included in every HTTP request from client to server." Security Best Practices adds: "MCP servers that implement authorization **MUST** verify all inbound requests. MCP servers **MUST NOT** treat possession of a state handle as authentication."

That last rule deserves a note. Revision 2026-07-28 removed protocol sessions outright, along with `Mcp-Session-Id` and the `initialize` handshake (Specification Enhancement Proposals SEP-2567 and SEP-2575), so there is no protocol session left to misuse; a reader meets those only in older code. The rule now bites on state a server or deployment keeps for itself: a handle a tool mints, a cookie a reverse proxy sets after the first authorized call, an in-memory map from connection to user, a cache keyed on client address. Every request carries its own token, so the server validates that token, every time.

And two rules that are easy to get right and expensive to get wrong. Tokens travel in the `Authorization` header: "Access tokens **MUST NOT** be included in the URI query string." Separately, from the Streamable HTTP transport page, a server "**MUST** validate the `Origin` header on all incoming connections to prevent DNS rebinding attacks", where DNS is the Domain Name System, and where `Origin` is present and invalid, "servers **MUST** respond `403 Forbidden`". Section 10 shows that check armed on this project's server, and [post 21](../21-deploying/index.md) shows a deployment where it is silently off.

## 4. Protected resource metadata, and the chain it starts

The chain comes before the fields. A client that knows nothing but the endpoint asks four questions in order: where to authenticate, what that authorization server supports, how to identify itself to it, and what token it ends up with.

![Four panels with the exact Uniform Resource Locator (URL) at each hop. Panel A, an unauthenticated POST to the MCP endpoint returns 401 with a WWW-Authenticate header whose resource_metadata parameter is a URL, which the client fetches. Panel B shows the fallback used when no header is present, probing the well-known URI with the resource path inserted and then the well-known URI at the root. Panel C prints the protected resource metadata document, whose fields resource, authorization_servers, scopes_supported and bearer_methods_supported are each labeled with what the client does with them. Panel D shows the client probing the authorization server's OAuth 2.0 metadata before OpenID Connect discovery.](diagrams/02-metadata-discovery.svg)

*Two ways in, one document, and a fallback the client is required to try.*

The wire format first. `snippets/four_requests.py` builds the project's server in its static configuration (section 10) and sends it a POST with no token. The answer:

```http
HTTP/1.1 401 Unauthorized
content-type: application/json
www-authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp"

{"error": "invalid_token", "error_description": "Authentication required"}
```

The client reads `resource_metadata`, fetches it, and gets the document RFC 9728 defines, printed by the same script:

```json
{
  "resource": "http://127.0.0.1:8123/mcp",
  "authorization_servers": [
    "https://auth.example.com"
  ],
  "scopes_supported": [
    "system:read"
  ],
  "bearer_methods_supported": [
    "header"
  ]
}
```

Four fields, four jobs. `resource` is the canonical identifier of the server itself, and it is the value the client puts in the `resource` parameter of its authorization and token requests so that the token it receives is audienced at this server. `authorization_servers` names who can issue tokens the server will accept. `scopes_supported` is the server's published minimum, not its full catalog. `bearer_methods_supported` says the token arrives in a header.

Note the well-known path. RFC 9728 inserts the well-known segment between the host and the resource path, so a server at `https://example.com/public/mcp` publishes at `https://example.com/.well-known/oauth-protected-resource/public/mcp`. A server at the root publishes at `https://example.com/.well-known/oauth-protected-resource`. Those are different Uniform Resource Locators (URLs), and a client that finds no header falls back to probing them in that order. On the demonstration server the script prints `200` for the path-inserted URL and `404` for the root URL, which is correct, and is why a client that probes only the root cannot discover a server that lives on a path.

The rest of the chain is the client's business, and `snippets/three_party_flow.py` walks all of it against the project's toy authorization server. Its output (line 1 is the figure's first two exchanges, the request and its refusal):

```
1. POST /mcp, no token                   -> 401, resource_metadata=http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp
2. GET protected resource metadata      -> 200, resource=http://127.0.0.1:8123/mcp, authorization_servers=['http://127.0.0.1:9123/']
3. GET authorization server metadata    -> 200, issuer=http://127.0.0.1:9123/
   code_challenge_methods_supported:       ['S256']
   registration_endpoint:                  (absent)
   client_id_metadata_document_supported:  (absent)
4. GET /authorize, PKCE S256, resource  -> 302, code=code_<48 hex digits>, state=xyz, iss=http://127.0.0.1:9123/
   validate_authorization_response_iss: iss equals the metadata issuer, accepted
5. POST /token                          -> 200, token_type=Bearer, expires_in=300, scope=system:read
   access token claims: iss=http://127.0.0.1:9123/, aud=http://127.0.0.1:8123/mcp, sub=user-nobody-authenticated
6. POST /mcp with the bearer token      -> 200, tools=['system_info']
7. the same code, redeemed again        -> 400, error=invalid_grant
```

The `resource` from step 2 goes out on steps 4 and 5 and comes back as the token's `aud`. That is the RFC 8707 chain section 5 depends on. Step 7 replays the code and is refused: a code is single use.

## 5. Validating the token, including the check everyone skips

![A vertical gate diagram. A bearer token enters at the top and passes through five checks in order: is a token present, is it cryptographically valid, has it expired, was it issued for this server as its audience, and does it carry the scopes this operation needs. Each check has a labeled failure exit on the right: the first four exit with 401 Unauthorized, the fifth exits with 403 Forbidden and an insufficient_scope challenge. Below the gate, two arrows to an upstream API are contrasted: forwarding the client's own token is crossed out and annotated MUST NOT pass through, while calling that upstream with a separate token the server obtained itself is ticked.](diagrams/03-token-validation-gate.svg)

*Five checks, two failure codes, and one arrow that must never be drawn.*

Four of these five checks are the ones any bearer-token middleware already does. The exception is the fourth, the audience check, and it is the one the specification spends the most words on.

Here is the failure it prevents. A company runs one identity provider. It issues tokens to a dozen internal services. A user authorizes a token for the calendar service, and something later presents that token to the MCP server. A server that validates only the signature and the expiry finds the token perfectly valid and serves the request. It has just accepted a credential that a user never granted it, and the Security Best Practices page names the consequence: "This breaks a fundamental OAuth security boundary, allowing attackers to reuse legitimate tokens across different services than intended."

The fix is to check that the token names this server. For a JavaScript Object Notation (JSON) Web Token (JWT) that is the `aud` claim; for an opaque token it is whatever the introspection endpoint returns as the audience or resource. Either way the value compared against is the `resource` published in the metadata, and the client is required to have asked for it: "MCP clients **MUST** implement Resource Indicators for OAuth 2.0 as defined in RFC 8707" and **MUST** include the `resource` parameter "in both authorization requests and token requests".

`snippets/four_requests.py` signs three tokens with the toy authorization server's key, every one carrying the `system:read` scope and the right issuer, and sends them to the project's server in its JWT configuration:

```
aud = http://127.0.0.1:8123/mcp    200
aud = .../some-other-service       401
aud right, expired a second ago    401
```

The second token is the calendar token of the story: correctly signed, unexpired, carrying every scope required, and refused because it names another service.

The second half of the same rule is about what the server does *after* the token is good. If it calls a third-party API, it needs its own token from that service's authorization server. The client's token is not that token, and forwarding it is the anti-pattern the specification calls token passthrough: "MCP servers **MUST NOT** accept any tokens that were not explicitly issued for the MCP server." [Post 19](../19-security/index.md) places token passthrough among the attack classes; the one-line version is that the downstream service logs a different identity from the MCP server that actually forwarded the request, and the MCP server cannot tell its clients apart.

## 6. Scopes, and the difference between 401 and 403

Two status codes, two meanings, and clients branch on them:

| Status | Meaning | When |
|---|---|---|
| `401` | Unauthorized | "Authorization required or token invalid" |
| `403` | Forbidden | "Invalid scopes or insufficient permissions" |
| `400` | Bad Request | "Malformed authorization request" |

A `401` tells the client to go and get a token. A `403` tells it that the token is fine but does not carry enough, which starts a step-up flow rather than a fresh login. A server that answers `401` where it meant `403` turns every insufficient-scope failure into a needless re-authentication.

For the runtime insufficient-scope case the specification says a server **SHOULD** answer `403` with a `WWW-Authenticate` header carrying `error="insufficient_scope"`, a `scope` parameter naming the minimum scopes needed, and the `resource_metadata` URL. It also says to name all the scopes an operation needs at once: "Challenging incrementally (returning one missing scope, then another on the subsequent retry) forces multiple authorization round-trips for a single operation and degrades user experience." Section 9 shows what the Python software development kit (SDK) actually sends.

Minimization is the other half. Publishing every scope a server has in `scopes_supported` invites clients to request all of them, and the security guidance names the common mistakes plainly: "Publishing all possible scopes in `scopes_supported`", "Using wildcard or omnibus scopes (`*`, `all`, `full-access`)". The published list is the minimum that makes discovery and reading work, and the server challenges upward for the rest.

## 7. Getting a client ID, and why DCR is now the fallback

A client cannot start an OAuth flow without a `client_id`. Historically MCP clients got one through Dynamic Client Registration (DCR, RFC 7591): call the authorization server's `/register` endpoint, get credentials, proceed. That is now deprecated, and a reader meets it in older code and older authorization servers.

The replacement is Client ID Metadata Documents (CIMD, SEP-991), and the idea is neat. Instead of registering, the client *hosts* its own metadata at a Hypertext Transfer Protocol Secure (HTTPS) URL and uses that URL as its `client_id`. The authorization server sees a URL-shaped identifier, fetches it, and reads the client's name and redirect URIs out of the document. `snippets/client_id_document.py` prints an example of one and checks it against the SDK's models:

```json
{
  "client_id": "https://app.example.com/oauth/client-metadata.json",
  "client_name": "Example MCP Client",
  "redirect_uris": [
    "http://127.0.0.1:3000/callback"
  ],
  "grant_types": [
    "authorization_code"
  ],
  "response_types": [
    "code"
  ],
  "token_endpoint_auth_method": "none"
}
```

The `client_id` URL "**MUST** use the `https` scheme and contain a path component", the document **MUST** carry at least `client_id`, `client_name` and `redirect_uris`, and the `client_id` inside **MUST** match the URL exactly. The SDK client's test for a usable URL agrees: the script prints `True` for the URL above and `False` for the same path over `http`, for `https://app.example.com`, and for `https://app.example.com/`. No registration call, no per-authorization-server credential, and the identifier is portable: "Client IDs based on Client ID Metadata Documents are portable across authorization servers, since they are self-hosted HTTPS URLs resolved by the authorization server on demand."

Clients that support everything are told to pick in this order:

1. Pre-registered client information, if the client already has it for this server.
2. Client ID Metadata Documents, if the authorization server advertises `client_id_metadata_document_supported`.
3. Dynamic Client Registration, if the authorization server advertises a `registration_endpoint`.
4. Prompt the user to enter the client information.

The toy authorization server advertises neither, as step 3 of the flow in section 4 prints, so a client of it lands on option 1: the demonstration client is registered out of band, the way an administrator would do it.

DCR sits at position three with an explicit warning attached: "Dynamic Client Registration is deprecated. New implementations should use Client ID Metadata Documents instead. This option remains available for backwards compatibility with authorization servers that do not support Client ID Metadata Documents." The deprecated features registry gives it the standard window: deprecated in `2026-07-28`, with earliest removal in the first revision released on or after `2027-07-28`.

None of this is the resource server's problem, with one exception: a deployment that also runs the authorization server and supports CIMD is fetching a URL an unknown party supplied, which is a server-side request forgery (SSRF) risk the specification calls out by name.

## 8. Mix-up, confused deputy, and comparing an issuer byte for byte

Two attacks explain most of the remaining requirements.

**The mix-up attack.** A client talks to many authorization servers over its life. One of them is hostile. The hostile one tries to make the client hand it an authorization code that an honest server issued, which it can then redeem. The specification is blunt about why the obvious defense, Proof Key for Code Exchange (PKCE), is not enough: "PKCE alone does not prevent this attack because the client transmits the `code_verifier` to the attacker's token endpoint."

The mitigation is issuer identification (RFC 9207, adopted for MCP by SEP-2468, under which authorization servers **SHOULD** send the parameter and clients **MUST** validate it). Before redirecting the user, the client records the `issuer` from the authorization server metadata it validated. The authorization server returns an `iss` parameter in the authorization response. The client compares the two before sending the code anywhere.

The comparison has to be exact, and the specification spells out what "exact" excludes. Clients **MUST NOT** "apply scheme or host case folding, default-port elision, trailing-slash, or percent-encoding normalization" before comparing. `https://auth.example.com` and `https://auth.example.com/` are different issuers. So are `https://AUTH.example.com` and `https://auth.example.com`. This is simple string comparison in the RFC 3986 sense, and a URL parser that tidies its input breaks it. `snippets/issuer_slash.py` hands the SDK client's RFC 9207 check, `validate_authorization_response_iss`, three spellings against a published issuer of `https://auth.example.com`:

```
'https://auth.example.com'     accepted
'https://auth.example.com/'    rejected: Authorization response iss mismatch: https://auth.example.com/ != https://auth.example.com
'https://AUTH.example.com'     rejected: Authorization response iss mismatch: https://AUTH.example.com != https://auth.example.com
```

That precision leaks into server configuration too. The Python SDK sets `url_preserve_empty_path=True` on its settings model with a comment saying exactly why: a path-less issuer passed as a string must keep its canonical form, because "RFC 8414/9207 issuer comparison is exact string comparison, so a spurious trailing slash would break it."

**This is not hypothetical, and it cost this series a test.** The toy authorization server in [code/20-auth/](../../code/20-auth/) originally built its issuer string by hand, as `http://127.0.0.1:9123`, and put it on every redirect and in every token. Its routes, though, were handed the issuer as a pydantic `AnyHttpUrl`, and that type appends a slash to a URL with no path, so the metadata document advertised `http://127.0.0.1:9123/`. `snippets/issuer_slash.py` rebuilds that first version and runs the same SDK check on it:

```
str(AnyHttpUrl('http://127.0.0.1:9123'))           = 'http://127.0.0.1:9123/'
str(AnyHttpUrl('http://127.0.0.1:9123/oauth')) = 'http://127.0.0.1:9123/oauth'
AuthSettings(issuer_url='http://127.0.0.1:9123').issuer_url = 'http://127.0.0.1:9123'

== first version ==
published issuer: 'http://127.0.0.1:9123/'
iss on redirect:  'http://127.0.0.1:9123'
SDK client check: rejected: Authorization response iss mismatch: http://127.0.0.1:9123 != http://127.0.0.1:9123/

== current project ==
published issuer: 'http://127.0.0.1:9123/'
iss on redirect:  'http://127.0.0.1:9123/'
SDK client check: accepted
```

A client doing exactly what the paragraphs above require would have rejected every authorization response that server ever produced, before a single token was issued. The current project builds the issuer once, through `AnyHttpUrl`, and every other value reads it from there.

It only bites an issuer with no path, which is the ordinary case. And a test that compared the token against the same hand-built constant would have passed, because the constant was wrong in both places at once. The project's regression test, `test_the_published_issuer_and_the_minted_iss_are_byte_identical`, asserts the published issuer, the `iss` on the redirect and the `iss` in the token against *each other*, which is the only arrangement that could have caught it.

**The confused deputy.** This one bites servers that proxy to a third-party interface. The server holds one static client ID with the third party. A user authorizes once, the third-party authorization server drops a consent cookie, and from then on the consent screen is skipped for that static client ID. An attacker registers a client against the proxy, with a redirect URI the attacker controls, sends the user a crafted authorization link, rides the cookie in the user's browser and collects an authorization code without the user ever seeing a prompt.

The requirement is per-client consent, owned by the proxy and checked before it forwards anything: "MCP proxy servers using static client IDs **MUST** obtain user consent for each dynamically registered client before forwarding to third-party authorization servers."

## 9. Wiring it up, and what the SDK provides

The Python SDK's server-side surface here is two names. `snippets/sdk_surface.py` reads them out of the installed package:

```
mcp 2.3.0

TokenVerifier is a Protocol: True; methods: ['verify_token']
  verify_token(token: str) -> AccessToken | None

AccessToken fields:
  token       required
  client_id   required
  scopes      required
  expires_at  default None
  resource    default None
  subject     default None
  claims      default None

AuthSettings required fields: ['issuer_url', 'resource_server_url']
AuthSettings.validate_token_resource default: None
```

`TokenVerifier` is a `Protocol` with exactly one method. Return an `AccessToken` and the request proceeds to the scope check; return `None` and the request is refused with `401`.

The audience check is the verifier's to write, because nothing in the SDK's bearer authentication reads an access token's `aud` claim. Here is a verifier that does it, the first listing of `snippets/my_verifier.py`, where `introspect` (defined further down the script) reads a dictionary that stands in for the JWT library or the introspection call:

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

`AuthSettings` is the other half. Its two required fields are `issuer_url` and `resource_server_url`, and the script's second listing follows directly:

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
        validate_token_resource=True,
    ),
)
```

What those lines buy is most of section 3. The SDK installs bearer authentication middleware, wraps the MCP route so every request is checked, publishes the RFC 9728 document at the well-known path derived from `resource_server_url`, and attaches the `WWW-Authenticate` challenge to `401` and `403` responses with the metadata URL already filled in. That is the `401` and the metadata document quoted in section 4, which the project's server produces from the same settings: its `AuthSettings` also sets `validate_token_resource=True`, and its static verifier reports the server's own identifier as the resource of every token it knows.

The middleware also adds two checks of its own on whatever the verifier returns. It refuses a token whose reported `expires_at` has passed, and, when `validate_token_resource=True`, a token whose reported `resource` is not `resource_server_url`. Left unset, the field defaults to `None`, behaves as `False`, and raises an `MCPDeprecationWarning` that says 3.0 will make `True` the default. `snippets/my_verifier.py` sends four tokens through `MyVerifier` and through three careless verifiers on the same settings:

```
MyVerifier        for-us=200  for-calendar=401  for-us-expired=401  unknown=401
TrustsTheToken    for-us=200  for-calendar=401  for-us-expired=401  unknown=401
ClaimsItIsForUs   for-us=200  for-calendar=200  for-us-expired=401  unknown=401
ReportsNoResource for-us=401  for-calendar=401  for-us-expired=401  unknown=401
```

`MyVerifier` never looks at `exp`, and the expired token is refused anyway: that is the middleware. `TrustsTheToken` skips the audience check but reports the token's own `aud` as the resource, and the middleware's resource check catches the calendar token. `ClaimsItIsForUs` reports this server's identifier whatever the token says, and the calendar token gets through, because the SDK's check reads only what the verifier reports. `ReportsNoResource` is the mistake section 12 describes. The SDK's check is a second line, not a replacement for reading `aud`, and the SDK compares the two URLs leniently (its source comment: "Compare as URLs (so case and default-port spelling do not matter), a trailing slash aside."), unlike the issuer comparison of section 8.

The constructor is strict, and its three refusal messages are worth knowing before meeting them in production. `snippets/sdk_surface.py` prints them:

```
MCPServer constructor:
  provider and verifier, with auth: ValueError: Cannot specify both auth_server_provider and token_verifier
  auth with neither:                ValueError: Must specify either auth_server_provider or token_verifier when auth is enabled
  verifier without auth:            ValueError: Cannot specify auth_server_provider or token_verifier without auth settings
  provider without auth:            ValueError: Cannot specify auth_server_provider or token_verifier without auth settings
```

**One gap to know about.** The specification says servers **SHOULD** include a `scope` parameter in the insufficient-scope challenge. Under `mcp==2.3.0` the header carries `error`, `error_description` and `resource_metadata`, and no `scope`; `snippets/four_requests.py` prints it and then `scope parameter present: False`:

```http
HTTP/1.1 403 Forbidden
www-authenticate: Bearer error="insufficient_scope", error_description="Required scope: system:read", resource_metadata="http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp"
```

The required scope is in the human-readable description rather than the machine-readable parameter, so a client following the letter of the specification finds nothing to parse. A server that needs the parameter adds it in its own middleware. The project's `test_insufficient_scope_challenge_carries_no_scope_parameter` pins the gap, so a release that closes it fails the suite instead of quietly making this paragraph wrong.

The SDK also still ships Dynamic Client Registration handlers, and in 2.3.0 they carry no deprecation marker: `snippets/sdk_surface.py` finds no occurrence of "deprecat" in the registration handler's source. They are mounted only when `ClientRegistrationOptions(enabled=True)` is passed, and the script prints `ClientRegistrationOptions().enabled = False`. Leave it that way.

## 10. Testing it, with four requests

A resource server can be tested without an authorization server. It needs a `TokenVerifier` that accepts fixed strings, and four requests. The project's static configuration has `required_scopes=["system:read"]` and a verifier that accepts `good-token` with that scope and `weak-token` with none, and refuses everything else. `snippets/four_requests.py` sends the four, in process:

```
no Authorization header            401   Bearer error="invalid_token"
Authorization: Bearer nope         401   Bearer error="invalid_token"
Authorization: Bearer weak-token   403   Bearer error="insufficient_scope"
Authorization: Bearer good-token   200   (no challenge)
                                         tools: ['system_info']
```

Four assertions, and they cover the shape of everything in this post: the challenge is present and points somewhere real, an unknown token is refused, a valid token with thin scopes gets `403` rather than `401`, and a good token gets through. A fifth is for the audience: a token whose `aud` names a different service must get `401`, which section 5 printed. That test is the one that fails on a server nobody has audited.

All five are in [tests/test_resource_server.py](../../code/20-auth/tests/test_resource_server.py), along with a sixth that is easy to forget: a *correctly* audienced token must still be accepted. Without it, the audience test would pass just as happily if the verifier refused everything, and a test that cannot tell the behavior it is checking from total failure is not a test.

None of this needs a socket. `streamable_http_app()` returns a Starlette application, so the suite and the scripts drive the server in process over the Asynchronous Server Gateway Interface (ASGI), and the whole three-party exchange in [tests/test_flow.py](../../code/20-auth/tests/test_flow.py) runs with no browser and nothing to wait for.

The same in-process route also shows the `Origin` check of section 3 is armed in this configuration. `streamable_http_app()` binds its protection to the loopback host by default, and a good token sent with `Origin: https://evil.example` prints `403   Invalid Origin header`. [Post 21](../21-deploying/index.md) measures the deployment where that protection is off. The well-known paths are covered too, because a client that probes only the root misses a server on a path: the tests fetch both and assert `200` and `404` respectively.

## 11. Make it run: the project and the scripts behind this post

The project is the post's code, and its suite needs no network and no browser. From inside `code/20-auth/`:

```bash
uv sync --extra dev                  # once: the environment, with the test tools
uv run --extra dev pytest            # 22 passed
uv run python -m auth_demo           # resource server, static tokens, http://127.0.0.1:8123
uv run python -m auth_demo --jwt     # the same, trusting the toy authorization server
uv run python -m toy_as              # toy authorization server, http://127.0.0.1:9123
```

The end of a real run of the suite:

```
tests\test_flow.py ......                                                [ 27%]
tests\test_resource_server.py ................                           [100%]

============================= 22 passed in 0.33s ==============================
```

Twenty-two tests, sixteen in `tests/test_resource_server.py` and six in `tests/test_flow.py`, with no warnings; the count is the result, and the seconds vary by machine. One of them, `test_the_sdk_refuses_a_token_reported_for_another_resource`, gives the static verifier another service's resource and asserts the `401` that `validate_token_resource=True` produces. With `python -m auth_demo` running, the requests of section 10 can be sent by hand, which is the post's lab:

```bash
curl -i -X POST http://127.0.0.1:8123/mcp                                        # 401
curl -i -X POST http://127.0.0.1:8123/mcp -H "Authorization: Bearer nope"        # 401
curl -i -X POST http://127.0.0.1:8123/mcp -H "Authorization: Bearer weak-token"  # 403
curl -i http://127.0.0.1:8123/.well-known/oauth-protected-resource/mcp           # 200
curl -i http://127.0.0.1:8123/.well-known/oauth-protected-resource               # 404
```

The wrong-audience request needs a signed token, which `snippets/four_requests.py` makes with the toy authorization server's signing key.

Six scripts under `snippets/` produce every response, header and status quoted above. Each runs from the series root with no arguments in a few seconds, imports the project from `code/20-auth/src` (and its test helpers from `code/20-auth/tests`) under the root environment, which pins `mcp[cli]==2.3.0` (and with it `httpx2`) and `pyjwt`, and serves nothing on a port:

```bash
uv run python posts/20-authorization/snippets/four_requests.py       # sections 4, 5, 6, 10 and 12: challenges, metadata, the four requests, audience, Origin
uv run python posts/20-authorization/snippets/three_party_flow.py    # sections 4 and 7: the whole exchange, hop by hop
uv run python posts/20-authorization/snippets/client_id_document.py  # section 7: a Client ID Metadata Document and its URL rules
uv run python posts/20-authorization/snippets/issuer_slash.py        # section 8: the trailing slash and the RFC 9207 check
uv run python posts/20-authorization/snippets/sdk_surface.py         # section 9: TokenVerifier, AccessToken, AuthSettings, the constructor
uv run python posts/20-authorization/snippets/my_verifier.py         # sections 9 and 12: the audience check and the SDK's second check
```

Running the full flow against a real identity provider is left to that provider's documentation; the toy authorization server exists so the three-party exchange is reproducible on a laptop.

## 12. What can go wrong?

**Leaving `validate_token_resource` unset with a verifier that trusts the token.** The server starts, and the only sign is a warning on standard error. `snippets/my_verifier.py` runs the careless `TrustsTheToken` verifier with the field left out:

```
TrustsTheToken again, with validate_token_resource left unset:
                  for-us=200  for-calendar=200  for-us-expired=401  unknown=401
warning: MCPDeprecationWarning: `AuthSettings.validate_token_resource` is not set, so bearer tokens are not checked against `resource_server_url`
```

The calendar token gets through. The fix is the audience check inside `verify_token` and `validate_token_resource=True` beside it.

**Turning the flag on with a verifier that reports no resource.** Every token, including a correct one, comes back `401`, as the `ReportsNoResource` row of section 9 shows. The response says only `invalid_token`; the cause is in the server's log, which the script captures:

```
logged for ReportsNoResource, first of 1: WARNING mcp.server.auth.middleware.bearer_auth: Bearer token resource None is not resource_server_url https://mcp.example.com/mcp
```

The verifier must set `AccessToken.resource` to the audience it confirmed; a table-lookup verifier, which has no `aud` to read, has to be told which resource to report, which is why the project's `build_static_server` passes `resource_url(host, port)` to its `StaticTokenVerifier`.

**Testing by hand with a good token and no body.** A bare `curl -X POST` with `good-token` gets past authorization and is then refused by the transport: `snippets/four_requests.py` prints `400   Invalid Content-Type header`. That is a transport refusal, not an authorization one. A real request carries `Content-Type: application/json`, the `Accept`, `MCP-Protocol-Version` and `Mcp-Method` headers, and a body whose `params._meta` holds the protocol version and client capabilities, as [post 03](../03-wire-protocol/index.md) builds by hand.

## Common pitfalls

1. **Adding OAuth to a stdio server.** The specification says implementations on stdio **SHOULD NOT** follow the authorization specification and should read credentials from the environment instead; a browser flow for a process the host already spawned buys nothing.
2. **Validating the signature and stopping.** A well-formed, unexpired, correctly signed token issued for a different service is still a token the server must refuse, and the SDK's resource check sees only what the verifier reports.
3. **Forwarding the client's token upstream.** A server that calls a third-party API gets its own token from that service's authorization server; passing the client's token through is token passthrough, and the specification forbids it outright.
4. **Returning `401` for insufficient scope.** It sends the client back through a full login when a step-up would have done: `401` means "get a token", `403` means "that token is fine and not enough".
5. **Normalizing the issuer before comparing it.** Trailing slashes, case folding and default-port elision all break RFC 9207 issuer validation; compare the strings as given, byte for byte.
6. **Caching an authorization decision per connection.** Servers **MUST** verify all inbound requests, and no cookie, connection or state handle stands in for the token; revision 2026-07-28 has no protocol sessions, so the only sessions that can bite are the ones a deployment built.

## Further reading

- Specification, *"Authorization"*, revision 2026-07-28, including its Client Registration, Authorization Server Discovery, and Security Considerations sections. Every requirement quoted in sections 1 to 8. <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization>
- Specification, *"Client registration"*, revision 2026-07-28. Client ID Metadata Documents, and Dynamic Client Registration as deprecated (section 7). <https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration>
- Specification, *"Security Best Practices"*. Token passthrough, the confused deputy, session hijacking, and scope minimization. <https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices>
- Specification, *"Transports: Streamable HTTP"*, revision 2026-07-28. The `Origin` rule of section 3. <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- Specification, deprecated features registry. The window for Dynamic Client Registration. <https://modelcontextprotocol.io/specification/2026-07-28/deprecated>
- RFC 9728, *OAuth 2.0 Protected Resource Metadata*; RFC 8707, *Resource Indicators for OAuth 2.0*; RFC 9207, *OAuth 2.0 Authorization Server Issuer Identification*; RFC 7591, *OAuth 2.0 Dynamic Client Registration Protocol*; RFC 7636, *Proof Key for Code Exchange (PKCE)*.
- SEP-991, *OAuth Client ID Metadata Documents (CIMD)*; SEP-2468, *RFC 9207 issuer validation*.
- MCP Python SDK, release 2.3.0. `TokenVerifier`, `AuthSettings`, the bearer middleware, and the client's issuer check. <https://github.com/modelcontextprotocol/python-sdk>
- [code/20-auth/](../../code/20-auth/). The resource server, the toy authorization server, and the twenty-two tests run in section 11.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 21 - Deploying to production: containers, scaling, and observability](../21-deploying/index.md):** the container that runs a server like this one, and the measured case where `Origin` validation is off.
- **[Post 23 - Project 4: One server, every client](../23-multi-client/index.md):** one server configured in six hosts over both transports, and the point at which per-user data makes this post's resource server necessary.
