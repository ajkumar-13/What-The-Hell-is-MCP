# Glossary

Every term the series uses, one line each, alphabetized. Where a term changed meaning in
protocol revision 2026-07-28, the entry says so.

Terms marked **(removed)** or **(deprecated)** are here because you will meet them in
older code and older tutorials, not because you should use them.

---

**Annotation** — A hint attached to a tool (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`) describing how it behaves. Hints only: the specification
says clients must treat them as untrusted. Never enforcement.

**Audience** — An optional field on content and resources naming who a piece of content is
for, `user` or `assistant`.

**Capability** — A declaration that a party supports some part of the protocol. Since
2026-07-28 the client's capabilities travel in `_meta` on every request rather than being
agreed once at connection time.

**CIMD (Client ID Metadata Document)** — An OAuth client identity scheme where the client's
`client_id` is an HTTPS URL serving its own metadata. The recommended replacement for
Dynamic Client Registration.

**Client** — The protocol-speaking object inside a host, one per connected server. Owns the
transport and request correlation. Not the host, and not the user-facing application.

**Completion** — A server-provided suggestion list for a prompt argument or a resource
template variable, served by `completion/complete`.

**Confused deputy** — An attack where a proxy with static credentials is tricked into using
its own authority on an attacker's behalf. Named explicitly in the security best practices.

**Content block** — One piece of a tool result: `text`, `image`, `audio`, `resource_link`,
or an embedded `resource`.

**DCR (Dynamic Client Registration)** — **(deprecated)** An OAuth flow where a client
registers itself with an authorization server at runtime. Demoted in 2025-11-25, deprecated
in 2026-07-28. Use CIMD.

**Elicitation** — A server asking the user a structured question mid-request. Since
2026-07-28 delivered by returning `InputRequiredResult` rather than calling back.

**Era** — The specification's term for which connection model an implementation speaks:
*modern* (2026-07-28 and later, per-request metadata), *legacy* (2025-11-25 and earlier,
`initialize` handshake), or *dual-era*.

**Extension** — An optional, independently versioned addition to the protocol, named with a
reverse-DNS identifier such as `io.modelcontextprotocol/tasks`. Always opt-in.

**Handle** — A server-minted opaque identifier passed back as an ordinary tool argument.
The stateless replacement for storing things in a session.

**Host** — The application the user interacts with. Owns the model, the conversation, and
every consent decision. Contains one client per connected server.

**`initialize`** — **(removed)** The handshake that used to open a connection. Replaced by
`server/discover` plus per-request `_meta`.

**`InputRequiredResult`** — A result whose `resultType` is `input_required`, carrying
`inputRequests`. The server's way of saying "I need something from the user before I can
finish". The heart of MRTR.

**`isError`** — The flag on a tool result marking a *tool execution* failure, as opposed to
a protocol error. A failing tool returns a successful JSON-RPC response with this set, and
clients should feed the message back to the model so it can retry.

**JSON-RPC 2.0** — The message format MCP is built on. Requests carry `id` and `method`,
responses carry `result` or `error`, and notifications carry no `id`.

**Line jumping** — An attack that poisons the model's context at `tools/list` time, before
any tool is called, so human approval of tool calls never gets the chance to fire.

**Lethal trifecta** — The combination of access to private data, exposure to untrusted
content, and the ability to communicate externally. Any agent with all three can be made to
exfiltrate.

**Logging** — **(deprecated)** The `logging/setLevel` method and `notifications/message`.
Log to stderr instead, and use the `io.modelcontextprotocol/logLevel` `_meta` key.

**MCPB** — A bundle format packaging a server for one-click desktop installation. Formerly
`.dxt`.

**`_meta`** — The reserved metadata object carried on protocol messages. Since 2026-07-28 it
is load-bearing: it carries the protocol version, client capabilities, client identity, log
level, and OpenTelemetry trace context.

**MRTR (Multi Round-Trip Request)** — The mechanism replacing all server-initiated requests.
The server returns `input_required` with what it needs; the client obtains it and retries
the original request with `inputResponses` attached.

**Notification** — A JSON-RPC message with no `id` and therefore no response.

**Prompt** — A user-invoked template that expands into one or more pre-filled messages.
User-controlled, unlike a tool.

**Protocol error** — A JSON-RPC-level failure such as an unknown method or malformed
request. Distinct from a tool execution error.

**Registry** — The official index of published MCP servers. In preview, and explicitly
minimally moderated.

**Resource** — Read-only data a server exposes at a URI, attached to the conversation by the
application rather than called by the model.

**Resource template** — A resource URI containing RFC 6570 variables, such as
`system://disk/{disk}`. The variables are untrusted input.

**`resultType`** — The discriminator now present on every result: `complete`,
`input_required`, or, with the tasks extension, `task`.

**Roots** — **(deprecated)** A client-side capability advertising which directories a server
may operate within. Pass the paths as tool arguments or server configuration instead.

**Rug pull** — A server that changes its tool definitions after being approved, so the
thing the user consented to is not the thing that runs.

**Sampling** — **(deprecated)** A server asking the host's model to generate a completion.
There is no back channel for it since 2026-07-28. Call a model provider directly and
disclose that you are doing so.

**SEP (Specification Enhancement Proposal)** — The process by which MCP changes. Reading the
SEP usually explains *why* far better than the normative text does.

**`server/discover`** — The method a client calls to learn a server's identity, supported
protocol versions, and capabilities. Servers must implement it; clients may call it.

**Server** — The process exposing tools, resources, and prompts. Usually your code. Not
necessarily remote and not necessarily a web service.

**Session** — **(removed)** The protocol-level connection state, formerly tracked with
`Mcp-Session-Id`. Its removal is what makes MCP servers ordinary scalable web services.

**Stateless** — The property, new in 2026-07-28, that every request carries everything the
server needs to handle it, so any replica can serve any request.

**stdio** — The transport where the host spawns the server as a subprocess and speaks over
stdin and stdout. Writing anything else to stdout corrupts the channel.

**Streamable HTTP** — The HTTP transport: one endpoint, ordinary POSTs, optional streaming.
Replaced the older HTTP+SSE transport, which is deprecated.

**Structured content** — A tool result's machine-readable form, validated against the tool's
`outputSchema` and returned alongside the human-readable content blocks.

**`subscriptions/listen`** — The single long-lived stream a client opts into for
list-changed notifications and resource updates. Replaced `resources/subscribe` and the HTTP
GET stream.

**Task** — A unit of long-running work with its own lifecycle, provided by the tasks
extension. Creation is server-directed: the client declares the extension and the server
decides per request.

**Token passthrough** — Forwarding a client's access token to an upstream API. Explicitly
forbidden: a server must not accept tokens that were not issued for it.

**Tool** — A function the model can call. Model-controlled, and the only primitive with side
effects by design.

**Tool poisoning** — Hiding instructions in a tool's description or schema so that merely
listing the tool injects them into the model's context.

**Transport** — The layer carrying JSON-RPC messages: stdio or Streamable HTTP.

**URI template** — See *resource template*.
