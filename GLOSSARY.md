# Glossary

Every term the series uses, one line each, alphabetized. Where a term changed meaning in
protocol revision 2026-07-28, the entry says so.

Terms marked **(removed)** or **(deprecated)** are here because you will meet them in
older code and older tutorials, not because you should use them.

Two terms are deliberately listed twice, because MCP and OAuth 2.1 use the same word for
different things: see **Audience** and, for "the three roles", **Server** and
**Resource server**.

---

**Allowlist** — A fixed set of values a server or host is willing to accept, with everything
else refused. Preferred to validation or escaping for resource template variables, for SQL
identifiers, and for a host's permission lists, because it is the form of the check a
reviewer can actually verify. Its counterpart is a denylist, and deny wins ties.

**Annotation** — A hint attached to a tool (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`) describing how it behaves. Hints only: the specification
says clients must treat them as untrusted. Never enforcement. `ToolAnnotations` also carries
a `title`, consulted after the tool's own `title` and before its `name`. A different object
of the same name — `audience`, `priority`, `lastModified` — is carried by resources, resource
templates, and every content block, and it is equally unenforced.

**Audience (annotation)** — An optional field in `annotations` on content blocks, resources,
and resource templates: an array of `user`, `assistant`, or both, naming who a piece of
content is for. A hint, and nothing enforces it.

**Audience (OAuth)** — Who an access token was issued for, carried in the `aud` claim. A
server **must** reject a token that does not name it, and the value to compare against is the
`resource` it published in its own metadata.

**Authorization server** — The OAuth 2.1 party that authenticates the user and issues tokens.
Almost always somebody else's software; an MCP server that starts storing passwords has
accidentally begun writing one.

**Bearer token** — An access token honored on presentation alone. It travels in the
`Authorization` header and **must not** appear in a query string. A task identifier, and a
handle on an unauthenticated server, are effectively bearer tokens too, which is why both
need real entropy.

**Blast radius** — How much a proposed change can affect. Limits on it belong in the
resolver, so an out-of-bounds change fails before anyone is asked to approve it, rather than
in the tool body, where the refusal arrives after a human has already been prompted.

**`CacheableResult`** — The result type carrying `ttlMs` and `cacheScope`, both required.
Servers **must** include them on `complete` results from six methods: `server/discover`,
`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and
`resources/read`. `tools/call`, `prompts/get`, and `completion/complete` are not cacheable and
carry neither field.

**`cacheScope`** — The required companion to `ttlMs` on a cacheable result: `public` or
`private`. `private` means caches **must not** be shared across authorization contexts. It is
a sharing hint, and a server **must not** rely on it alone to prevent unauthorized access.

**Capability** — A declaration that a party supports some part of the protocol. Since
2026-07-28 the client's capabilities travel in `_meta` on every request rather than being
agreed once at connection time.

**Capability catalog** — The single flat map a host builds from every connected server's
lists, from the name the model is shown to the connection that will serve it. The model picks
a name out of it and never picks a server.

**CIMD (Client ID Metadata Document)** — An OAuth client identity scheme where the client's
`client_id` is an HTTPS URL serving its own metadata. The recommended replacement for
Dynamic Client Registration.

**Client** — The protocol-speaking object inside a host, one per connected server. Owns the
transport and request correlation. Not the host, and not the user-facing application.

**Completion** — A server-provided suggestion list for a prompt argument or a resource
template variable, served by `completion/complete`.

**Conformance suite** — The independent test suite maintained alongside the specification,
which drives an implementation through the behaviors the specification requires. Your own
tests cannot tell you that what you meant matches the specification, because you wrote both.
It matters more to a client than to a server.

**Confused deputy** — An attack where a proxy with static credentials is tricked into using
its own authority on an attacker's behalf. Named explicitly in the security best practices.

**Content block** — One piece of a tool result or of a prompt message: `text`, `image`,
`audio`, `resource_link`, or an embedded `resource`.

**Cross-server shadowing** — An attack where one server's tool description changes how the
model uses a *different*, trusted server's tools. The malicious server is never called, so
nothing about it appears in the interaction log. Not the host-side *shadow* strategy for name
collisions.

**CSP (Content Security Policy)** — The browser policy the host applies to an MCP Apps view.
The host builds it from the resource's own `ui.csp` declarations — `connectDomains` becomes
`connect-src`, `resourceDomains` becomes the script, style, image, font, and media sources,
`frameDomains` becomes `frame-src` — over a `default-src 'none'` base. A declaration is a
request, and the host is free to narrow it.

**Cursor** — The opaque pagination token a list result returns as `nextCursor` and a later
request sends back. Clients **must** treat it as opaque, and an empty string is a valid
cursor rather than the end of the results. Not the editor of the same name.

**DCR (Dynamic Client Registration)** — **(deprecated)** An OAuth flow where a client
registers itself with an authorization server at runtime. Deprecated in 2026-07-28, with
earliest removal in the first revision released on or after 2027-07-28. Use CIMD.

**Deprecated features registry** — The specification's table of what is deprecated, from
which revision, and what to migrate to. It is where a feature's earliest-removal date is
stated, and the first place to check before building on anything.

**DNS rebinding** — An attack where a hostile page resolves its own name to `127.0.0.1` after
the browser has loaded it, so the page's own scripts can reach a server bound to loopback. It
is why a local server **must** validate `Origin`, and why the Python SDK's
`TransportSecuritySettings` carries `enable_dns_rebinding_protection`, `allowed_hosts`, and
`allowed_origins`.

**Dry run** — A call that validates a change and reports what would happen without executing
it and without asking anyone to approve it. The cheapest gate on a write path, because it
spends no consent.

**Elicitation** — A server asking the user a structured question mid-request. Since
2026-07-28 delivered by returning `InputRequiredResult` rather than calling back.

**Era** — The specification's term for which connection model an implementation speaks:
*modern* (2026-07-28 and later, per-request metadata), *legacy* (2025-11-25 and earlier,
`initialize` handshake), or *dual-era*.

**Extension** — An optional, independently versioned addition to the protocol, named with a
reverse-DNS identifier such as `io.modelcontextprotocol/tasks`. Always opt-in: each side
declares it in an `extensions` map, where `{}` means supported with no settings. A client that
does not declare one **must** still get core behavior back.

**Handle** — A server-minted opaque identifier passed back as an ordinary tool argument.
The stateless replacement for storing things in a session.

**Host** — The application the user interacts with. Owns the model, the conversation, and
every consent decision. Contains one client per connected server.

**`icons`** — An optional array of `{ src, mimeType, sizes }` references on a tool, resource,
template, or prompt. Nothing requires a host to render them, and one that does not is still
conformant.

**`initialize`** — **(removed)** The handshake that used to open a connection. Replaced by
`server/discover` plus per-request `_meta`. MCP Apps' `ui/initialize` is a different
handshake on a different channel and is unaffected.

**`InputRequiredResult`** — A result whose `resultType` is `input_required`, carrying
`inputRequests`, `requestState`, or both. Each field is individually optional, but at least
one **must** be present. The server's way of saying "I need something from the user before I
can finish". The heart of MRTR.

**Inspector** — The official interactive client: a local web interface that lists a server's
tools, resources, and prompts and calls them with arguments you type. A manual tool rather
than a test suite, and one that has already shipped an unauthenticated remote code execution
flaw, so keep it current and bound to localhost.

**`instructions`** — The optional free-text field in a `server/discover` result, addressed to
the model rather than to a person. On some hosts it is the only documentation a server ever
gets to show.

**`isError`** — The flag on a tool result marking a *tool execution* failure, as opposed to
a protocol error. A failing tool returns a successful JSON-RPC response with this set, and
clients should feed the message back to the model so it can retry.

**JSON-RPC 2.0** — The message format MCP is built on. Requests carry `id` and `method`,
responses carry `result` or `error`, and notifications carry no `id`.

**JWT (JSON Web Token)** — A signed, self-describing access token whose claims, including
`aud`, a server can validate locally against the authorization server's published keys, per
RFC 9068. Its opposite is an opaque token, which carries no readable claims and has to be
handed to the authorization server's introspection endpoint instead. Which one you hold
changes how you perform the audience check, not whether you perform it.

**Lethal trifecta** — The combination of access to private data, exposure to untrusted
content, and the ability to communicate externally. Any agent with all three can be made to
exfiltrate.

**Line jumping** — An attack that poisons the model's context at `tools/list` time, before
any tool is called, so human approval of tool calls never gets the chance to fire.

**Logging** — **(deprecated)** The protocol log channel: `notifications/message`, emitted
only for a request whose `_meta` carried `io.modelcontextprotocol/logLevel`. The
`logging/setLevel` method that used to drive it is **(removed)**, with no window. Log to
stderr and use OpenTelemetry instead; the notification and the key both carry deprecation
markers.

**MCP Apps** — The `io.modelcontextprotocol/ui` extension, the first official one and the
only stable one, which lets a tool point at a `ui://` resource the host renders in a
double-sandboxed frame. It versions on its own cadence, and the tool's text `content` must
still stand on its own.

**MCPB** — A bundle format packaging a server for one-click desktop installation, as a
`.mcpb` file carrying a `manifest.json`. Formerly `.dxt`, and the old `dxt_version` manifest
key is still accepted, so set `manifest_version` explicitly.

**`Mcp-Method` and `Mcp-Name`** — HTTP headers mirroring `method` and `params.name` or
`params.uri`, so a proxy can route, log, or rate limit without parsing the body. Required
where they apply, and a server that reads the body **must** reject a request whose header and
body disagree, with `-32020`.

**`MCP-Protocol-Version`** — The HTTP header naming the revision a request is written in,
required on every POST and mirroring `io.modelcontextprotocol/protocolVersion` in the body.
Routing-relevant values appear twice on purpose, once for a proxy to read cheaply and once
for the server to execute, which is why the server must validate that they agree.

**`mcpServers`** — The top-level key of a host's configuration file, and the first thing that
differs between hosts: Claude Desktop, Claude Code, Cursor, and Gemini CLI use `mcpServers`,
VS Code uses `servers`, and Zed uses `context_servers`. The entries below it disagree too,
about whether a `type` field exists and about how a variable is spelled. None of it reaches
the wire.

**`_meta`** — The reserved metadata object carried on protocol messages. Since 2026-07-28 it
is load-bearing: it carries the protocol version, client capabilities, client identity, log
level, and OpenTelemetry trace context.

**Mix-up attack** — An attack in which a hostile authorization server tricks a client into
handing over an authorization code that an honest server issued. PKCE alone does not stop it;
the mitigation is comparing the `iss` returned in the authorization response against the
recorded issuer, as exact string comparison with no normalization.

**MRTR (Multi Round-Trip Requests)** — The mechanism replacing all server-initiated requests.
The server returns `input_required` with what it needs; the client obtains it and retries
the original request with `inputResponses` attached.

**Name collision** — Two connected servers exposing the same tool name. Names are unique only
within one server, so the host disambiguates, and the separator has to be a dot because a
slash is outside the character set a tool name may use. The prefix comes from the key in your
own configuration, never from the server's self-reported name. Qualifying every colliding name
is the strategy this series builds; the alternatives are *shadowing*, where first or last wins
and one tool silently disappears, and refusing the connection outright.

**Namespace** — In the registry, the reverse-domain prefix of a `server.json` `name`, such as
`io.github.you/`. You have to prove you own it, by GitHub identity, a DNS `TXT` record, or a
file on your own domain, and that is a separate proof from owning the package it points at.

**Notification** — A JSON-RPC message with no `id` and therefore no response.

**OpenTelemetry** — The vendor-neutral tracing standard MCP leans on now that protocol
logging is deprecated. A trace is a tree of spans; the W3C trace context in `_meta` is what
lets your server's spans join the host's tree rather than starting a second one.

**`Origin` validation** — The requirement that a server check the `Origin` header on every
incoming connection and answer `403 Forbidden` where it is present and invalid. It is what
stops a DNS rebinding attack reaching a local server through the user's own browser, and the
Python SDK arms it only when the bind address is loopback.

**`outputSchema`** — The optional JSON Schema a tool publishes for its `structuredContent`.
Publishing one is a promise: the server **must** return conforming results and clients
validate them. A return class with no class-body annotations publishes `null` here, silently.

**PKCE (Proof Key for Code Exchange)** — The OAuth extension binding an authorization code to
the client that requested it. Necessary and not sufficient: it does not prevent the mix-up
attack, because the client sends its verifier to whichever token endpoint it was pointed at.

**`progressToken`** — An unprefixed `_meta` key on a request that opts it into
`notifications/progress` on that request's own response stream. A courtesy inside a
synchronous call, and not available on tasks at all.

**Prompt** — A user-invoked template that expands into one or more pre-filled messages.
User-controlled, unlike a tool.

**Prompt injection** — Text that reaches the model as data and is followed as instruction.
Every attack in this series is a delivery mechanism for it: a tool description at
`tools/list` time, a row in a database, an issue in a repository, a page a browser tool
fetched. The protocol does not stop it, so the defenses are the host gate and what the tool
was allowed to do in the first place.

**Protected resource metadata** — The RFC 9728 document a protected server **must** publish,
naming its canonical `resource` identifier, the authorization servers whose tokens it
accepts, and its published minimum scopes. A client finds it through the `resource_metadata`
parameter on a `401` challenge, or by probing the well-known URI.

**Protocol error** — A JSON-RPC-level failure such as an unknown method or malformed
request, carried in an `error` member rather than a `result`. Distinct from a tool execution
error. Codes `-32020` to `-32099` are reserved for the specification and allocated
sequentially: `-32020` (`HeaderMismatch`), `-32021` (`MissingRequiredClientCapability`, whose
`data.requiredCapabilities` names what was missing), and `-32022`
(`UnsupportedProtocolVersion`) are the three defined so far, and all three answer `400 Bad
Request`. Resource-not-found is now `-32602`, not the retired `-32002`.

**Provider** — A model vendor's own HTTP application programming interface, reached with a
key, and outside MCP entirely. What a server calls directly now that sampling is deprecated,
which moves the bill, the consent, and the prompt onto whoever runs the server.

**Registry** — The official index of published MCP servers. In preview, and explicitly
minimally moderated.

**`requestState`** — An opaque, server-minted string returned alongside an `input_required`
result and echoed back byte for byte on the retry. It travels through the client, so a server
**must** treat it as attacker-controlled and integrity-protect it wherever it influences
authorization or business logic.

**Resolver** — The function behind a tool parameter the model cannot see, which returns an
`Elicit(...)` describing the question rather than asking it. The SDK matches its arguments to
the tool's own by name. Guards belong here, because a resolver runs before anybody is asked.

**Resource** — Read-only data a server exposes at a URI, attached to the conversation by the
application rather than called by the model.

**Resource indicator** — The `resource` parameter a client sends on both its authorization
and its token requests, per RFC 8707, naming the server the token is for. It is what makes
the audience check possible on the other side.

**Resource server** — The OAuth 2.1 role a protected MCP server plays: it accepts access
tokens, validates them, and serves or refuses. It never runs a login page and never mints,
refreshes, or revokes anything.

**Resource template** — A resource URI containing RFC 6570 variables, such as
`system://disk/{disk}`. The variables are untrusted input.

**`resultType`** — The discriminator now present on every result: `complete`,
`input_required`, or, with the tasks extension, `task`.

**Roots** — **(deprecated)** A client-side capability advertising which directories a server
may operate within. `roots/list` survives only inside MRTR `inputRequests`, and
`notifications/roots/list_changed` is **(removed)** with no window. Pass the paths as tool
arguments or server configuration instead.

**Rug pull** — A server that changes its tool definitions after being approved, so the
thing the user consented to is not the thing that runs.

**Sampling** — **(deprecated)** A server asking the host's model to generate a completion.
There is no back channel for it since 2026-07-28: `sampling/createMessage` survives only as a
`CreateMessageRequest` inside MRTR `inputRequests`, and earliest removal is the first revision
released on or after 2027-07-28. Call a model provider directly and disclose that you are
doing so.

**Scope** — A named permission carried on an access token. Publish the minimum in
`scopes_supported`, answer `403` with an `insufficient_scope` challenge rather than `401`
when a token is valid but too thin, and name every scope an operation needs at once rather
than one at a time.

**SEP (Specification Enhancement Proposal)** — The process by which MCP changes, running
propose, implement, review, publish, adopt, with a reference implementation required before
review. A Standards Track SEP changes the core specification; an Extensions Track one ships an
extension, and promoting that extension into core later needs its own Standards Track SEP.
Reading the SEP usually explains *why* far better than the normative text does.

**Server** — The process exposing tools, resources, and prompts. Usually your code. Not
necessarily remote and not necessarily a web service. When it speaks OAuth it plays the
*resource server* role, and "the three roles" there means resource server, client, and
authorization server rather than host, client, and server.

**`server/discover`** — The method a client calls to learn a server's identity, supported
protocol versions, and capabilities. Servers must implement it; clients may call it.

**`server.json`** — The static installation descriptor a registry entry is built from:
`name`, `description`, and `version` are required, plus where the artifact lives. Not a wire
artifact, which is why the stateless revision changed nothing in it.

**Session** — **(removed)** The protocol-level connection state, formerly tracked with
`Mcp-Session-Id`. Its removal is what makes MCP servers ordinary scalable web services.

**SSE (Server-Sent Events)** — The one-way `text/event-stream` format a Streamable HTTP POST
uses when a server streams notifications before its response. Each request has its own stream,
closing the stream **must** be treated as cancellation of that request, and resumability is
gone: there is no `Last-Event-ID` and no redelivery, so a client that loses a stream re-issues
with a new request id.

**SSRF (Server-Side Request Forgery)** — Making a party fetch a URL an attacker chose. In MCP
it is mostly a client problem: the URLs come from the server you connected to, so require
`https://` outside loopback, block private and link-local ranges, and re-check every redirect
hop. A server meets it too if it fetches a client-supplied CIMD document.

**Stateless** — The property, new in 2026-07-28, that every request carries everything the
server needs to handle it, so any replica can serve any request.

**stdio** — The transport where the host spawns the server as a subprocess and speaks over
stdin and stdout. Writing anything else to stdout corrupts the channel.

**Streamable HTTP** — The HTTP transport: one endpoint, ordinary POSTs, optional streaming.
Replaced the older HTTP+SSE transport, which is deprecated.

**Structured content** — A tool result's machine-readable form, returned alongside the
human-readable content blocks. Any JSON value, and where the tool published an `outputSchema`
the server **must** make it conform and the client validates it.

**`subscriptions/listen`** — The single long-lived stream a client opts into for
list-changed notifications and resource updates. Replaced `resources/subscribe` and the HTTP
GET stream. The server **must** send `notifications/subscriptions/acknowledged` before
anything else on it.

**Task** — A unit of long-running work with its own lifecycle, provided by the tasks
extension. Creation is server-directed: the client declares the extension and the server
decides per request.

**Token passthrough** — Forwarding a client's access token to an upstream API. Explicitly
forbidden: a server must not accept tokens that were not issued for it.

**Tool** — A function the model can call. Model-controlled, and the only primitive with side
effects by design.

**Tool poisoning** — Hiding instructions in a tool's description or schema so that merely
listing the tool injects them into the model's context.

**Trace context** — The W3C `traceparent`, `tracestate`, and `baggage` keys, named by the
specification as an explicit exception to the reverse-DNS prefix rule for reserved `_meta`
keys; `progressToken` is the only other reserved key with a bare name. Riding in `_meta`
rather than in an HTTP header is what lets a stdio server join the host's OpenTelemetry
trace; prefixing them produces a key nothing reads.

**Transport** — The layer carrying JSON-RPC messages: stdio or Streamable HTTP.

**`ttlMs`** — The required freshness hint on a cacheable result, in milliseconds, with the
semantics of `Cache-Control: max-age`. Zero, absent, or negative all mean immediately stale.
It is permission to skip a request, not an instruction to make one.

**`ui://`** — The URI scheme MCP Apps reserves for the HTML document a host fetches with
`resources/read` and renders. The MIME type **must** be `text/html;profile=mcp-app`, and the
document may be omitted from `resources/list` entirely.

**`ui/initialize`** — The MCP Apps handshake between a view and its host, carried over
`window.postMessage` rather than over an MCP transport, and versioned on the Apps line
(`McpUiInitializeResult.protocolVersion`) rather than the core one. Unrelated to the removed
core `initialize`, and not something to "fix".

**URI template** — See *resource template*.

**URL mode** — The elicitation mode that sends the user to an external URL. Servers **must**
use it rather than form mode for passwords, keys, tokens, and payment credentials, so the
answer never passes through the client. An `accept` means the user consented to open the URL,
not that the out-of-band work finished.

**Well-known URI** — The `/.well-known/…` path a client probes when no `WWW-Authenticate`
header pointed it at a server's metadata. RFC 9728 inserts the segment between host and path,
so a server at `/mcp` publishes at `/.well-known/oauth-protected-resource/mcp` and not at the
root.

**`WWW-Authenticate`** — The header a protected server sends on a `401`, and the client's
route to your metadata: its `resource_metadata` parameter names where the protected resource
metadata lives. The same header carries `error` and `error_description`, and on a `403` it
carries `insufficient_scope` plus the `scope` the caller still needs, which is what makes a
step-up possible without a fresh login.

**`x-mcp-header`** — A JSON Schema annotation on a tool input property, and not an HTTP
header itself. Its value supplies the name in a resulting `Mcp-Param-{Name}` header. Never
mark a secret with it: header values are visible to every intermediary on the path.
