# 24 · MCP Apps, extensions, and where the protocol goes next

> **TL;DR.** The core of the Model Context Protocol (MCP) is deliberately finished, so
> everything new now arrives as an **extension**: a reverse-DNS identifier, an entry in
> per-request capabilities, opt-in always, and a documented fallback when the other side says
> no. MCP Apps (`io.modelcontextprotocol/ui`) is the first official one, and it lets a tool
> return an interface that the host renders in a double-sandboxed frame. This post builds the
> smallest working example, says plainly which parts of the Apps specification predate the
> stateless revision, and closes the series.
>
> **After reading this you will be able to:**
> - Declare, detect, and gracefully degrade any MCP extension, not just this one.
> - Ship a tool that returns a rendered interface, with a text answer that stands on its own.
> - Read the `postMessage` dialect a view and a host speak, and tell it apart from MCP itself.
> - Take a change through the Specification Enhancement Proposal process.

![A tools/call result flowing two ways. Downward into the model as a text content block. Sideways into a host-rendered frame, fed by structuredContent and by an HTML document the host fetched earlier from a ui:// resource. The tool definition at the top carries a _meta.ui.resourceUri field that links the two paths.](diagrams/01-a-tool-result-that-renders.svg)
*One result, two audiences. The model reads `content`; the view reads `structuredContent`.*

---

## 1. A protocol that stopped growing on purpose

Read the 2026-07-28 changelog and the striking thing is how much of it is subtraction. No
`initialize`. No sessions. No `ping`, no `logging/setLevel`, no server-to-client request
channel, no Server-Sent Events (SSE) resumability. Tasks, which had been an experimental core
feature, was moved *out* of core.

That is not a protocol running out of ideas. It is a protocol deciding what it is. The core now
covers three primitives, two transports, one discovery method, and a small set of patterns, and
the maintainers' bet is that this is close to the right surface area for a thing every host and
every server must implement.

Everything else goes in an **extension**. [Post 09](../09-tasks/index.md) met the mechanism from
the inside, through tasks. This post looks at it as the growth model for the whole ecosystem,
because that is what it now is.

### What an extension is

Specification Enhancement Proposal (SEP) 2133 created the mechanism and defines it:

> An MCP extension is an optional addition to the specification that defines capabilities beyond
> the core protocol. Extensions enable functionality that may be modular (e.g., distinct
> features like authentication), specialized (e.g., industry-specific logic), or experimental
> (e.g., features being incubated for potential core inclusion).

Four rules govern all of them.

**They are identified by reverse Domain Name System (DNS) notation.** The format is
`{vendor-prefix}/{extension-name}`, following the same grammar as `_meta` keys except that the
prefix is mandatory. Any prefix whose *second label* is `modelcontextprotocol` or `mcp` is
reserved: `io.modelcontextprotocol/`, `dev.mcp/`, and `com.mcp.tools/` are all off limits, while
`com.example.mcp/` is fine because its second label is `example`. A breaking change requires a
new identifier rather than a version bump inside the old one.

**They are declared in an `extensions` map on capabilities**, from identifier to a settings
object, where `{}` means "supported, no settings". Since there is no handshake in this revision,
the client's declaration rides in `_meta` on **every request**, and the server's appears in the
`server/discover` result:

```jsonc
// client to server, on every single request
"_meta": {
  "io.modelcontextprotocol/clientCapabilities": {
    "extensions": {
      "io.modelcontextprotocol/ui": { "mimeTypes": ["text/html;profile=mcp-app"] }
    }
  }
}
```

```jsonc
// server to client, in the server/discover result
"capabilities": {
  "tools": {},
  "extensions": { "io.modelcontextprotocol/ui": {} }
}
```

**They are disabled by default and require explicit opt-in.** Not "on unless configured off".
Off unless a developer turned them on, on both sides.

**They must degrade gracefully.** SEP-2133 is normative about it:

> If one party supports an extension but the other does not, the supporting party MUST either
> revert to core protocol behavior or reject the request with an appropriate error if the
> extension is mandatory.

There is a security default in the same document worth carrying into every extension you touch:
both sides **should** treat any field an extension introduces as untrusted and validate it
comprehensively. An extension is not a trusted channel.

### One warning before you read the official pages

As of writing, `/extensions/overview`, SEP-2133 itself, and the client support matrix all still
document extension negotiation as something that happens inside an `initialize` handshake, with
a `2025-06-18` protocol version in the examples. Those pages have not been updated for the
stateless revision. The authoritative text is the specification's versioning page, section
"Extension negotiation", which shows bare `capabilities` objects with no `initialize` wrapper.
This will not be the last time in this post that an extension document is a revision behind.

## 2. MCP Apps: a tool that returns an interface

`io.modelcontextprotocol/ui` is the first official MCP extension, announced as generally
available in January 2026 and, unlike tasks, unambiguously stable: the specification file lives
at `specification/2026-01-26/apps.mdx` and its header reads `Status: Stable (2026-01-26)`.

The idea is small. A tool that returns a list of flights returns text, and the host prints the
text. With MCP Apps the same tool also points at a user interface (UI) document the host can
render inline, fed by the same result. Two things are reserved by the extension: the `ui://`
scheme, and the `io.modelcontextprotocol/ui` label.

### The linkage: one field on the tool

```json
{
  "name": "world_clock",
  "title": "World clock",
  "description": "Report the current time in several time zones.",
  "inputSchema": { "type": "object", "properties": { "zones": { "type": "array" } } },
  "_meta": {
    "ui": { "resourceUri": "ui://world-clock/app.html" }
  }
}
```

That is the whole of it on the tool side. `_meta.ui.resourceUri` names a resource, and the host
knows to fetch and render it.

There is a second field, `_meta.ui.visibility`, an array defaulting to `["model", "app"]`. Drop
`"model"` and the host **must not** put the tool in the model's tool list, which is how you
write a `refresh_dashboard` tool that only the interface can call. Drop `"app"` and a view may
not call it. The `"app"` scope is per-server; a view calling into a *different* server is always
blocked.

One deprecation to recognize in older code: the flat `_meta["ui/resourceUri"]` form is
deprecated in favor of the nested `_meta.ui.resourceUri` above.

### The `ui://` resource

The document itself is an ordinary MCP resource with two constraints. The URI **must** use the
`ui://` scheme, and the MIME type **must** be `text/html;profile=mcp-app`. Content arrives in
`text` or base64 `blob`, and it must be a valid HTML5 document.

```json
{
  "uri": "ui://world-clock/app.html",
  "name": "World clock",
  "mimeType": "text/html;profile=mcp-app",
  "text": "<!DOCTYPE html><html lang=\"en\">...</html>"
}
```

The host **must** fetch it with `resources/read`, and **may** prefetch and cache it. Because UI
documents are discovered through tool metadata rather than through browsing, a server **may**
omit them from `resources/list` entirely.

Predeclaring the document rather than embedding it in each result buys four things the
specification names: performance, because the host can preload before the tool ever runs;
security, because the host can review templates at connection time; caching, because the static
template is separated from the dynamic data; and auditability, because every UI document on a
server is enumerable.

### Two audiences, one result

This is the design point that decides whether your app is good.

| Field | Audience | Rule |
|---|---|---|
| `content` | the model, and every text-only host | **Must** be meaningful even when a UI exists |
| `structuredContent` | the view | Not added to the model's context |
| `_meta` | neither | Timestamps and versions, not for the model |

A tool whose `content` says "see the widget" has broken graceful degradation, and it has also
made itself useless to the model that called it. The specification requires a meaningful
`content` array whether or not the host can render anything.

## 3. The sandbox, which is two iframes

All view content **must** render in a sandboxed iframe. And when the host is itself a web page,
it **must** wrap the view in an intermediate proxy, giving the double-iframe architecture.

![Three nested regions. The outermost is the host page. Inside it, on a different origin, sits the sandbox proxy iframe with allow-scripts and allow-same-origin. Inside that sits the view iframe carrying the server's HTML under a Content Security Policy built from the resource metadata. Arrows show the handshake order: sandbox-proxy-ready upward, sandbox-resource-ready downward, then ui/initialize and initialized between view and host, with the proxy forwarding every message that is not a reserved sandbox message.](diagrams/02-app-sandbox.svg)
*The middle frame exists for one reason: the host and the view must not share an origin.*

The rules, in order:

1. The host and the sandbox **must** have **different origins**.
2. The sandbox iframe **must** carry `allow-scripts` and `allow-same-origin`.
3. When it is ready, the sandbox sends `ui/notifications/sandbox-proxy-ready`.
4. The host answers with `ui/notifications/sandbox-resource-ready`, carrying the raw HTML.
5. The sandbox loads that HTML under a Content Security Policy (CSP) built from the resource's
   metadata, with restrictive defaults if there is none.
6. The sandbox forwards messages in both directions for every method that does not begin
   `ui/notifications/sandbox-`. It **should not** originate requests of its own.

Why two frames rather than one? Because `allow-same-origin` is what lets the inner document use
ordinary web platform features, and a single iframe that is both same-origin with the host and
running server-supplied script is not a sandbox at all. The middle frame donates an origin the
host does not care about. The guarantees that fall out are the ones you want: no access to the
parent Document Object Model, no access to the host's cookies or local storage, no parent
navigation, and no script execution in the parent context.

One ordering rule catches implementers: **the host must not send any request or notification to
the view before it has received the view's `initialized` notification.**

### Content security and permissions

The resource carries its own policy in `_meta.ui`:

```json
"_meta": {
  "ui": {
    "csp": {
      "connectDomains": ["https://api.example.com"],
      "resourceDomains": ["https://cdn.example.com"]
    },
    "permissions": { "clipboardWrite": {} },
    "prefersBorder": true
  }
}
```

`connectDomains` becomes `connect-src`. `resourceDomains` becomes `img-src`, `script-src`,
`style-src`, `font-src`, and `media-src`, and supports wildcard subdomains. `frameDomains`
becomes `frame-src`, defaulting to `'none'` when omitted. `baseUriDomains` becomes `base-uri`,
defaulting to `'self'`.

**Empty and omitted are the secure default.** If `ui.csp` is absent entirely the host **must**
apply this policy verbatim:

```
default-src 'none';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
media-src 'self' data:;
connect-src 'none';
```

A host **may** restrict further but **must not** allow an undeclared domain, **must** block
connections to undeclared domains, and **should** warn the user when a view asks for external
access.

`permissions` maps to Permissions Policy features on the inner iframe: `camera`, `microphone`,
`geolocation`, and `clipboardWrite`. Requesting one is not being granted one, and the
specification says apps **should not** assume otherwise. Use feature detection.

## 4. The `postMessage` dialect

Between the view and the host there is a second JSON-RPC channel that has nothing to do with the
transport in [Post 04](../04-transports/index.md). Its transport is `window.postMessage`. On it,
the view behaves as a client and the host behaves as a server, usually proxying to the real
server behind it.

Thirteen methods carry the `ui/` prefix, plus the two reserved sandbox messages from section 3.

**Lifecycle.** `ui/initialize` (view to host, a request), `ui/notifications/initialized` (view to
host), and `ui/resource-teardown` (host to view, a **request**, which the host should await).

**Requests, view to host.** `ui/open-link`, `ui/message`, `ui/request-display-mode`, and
`ui/update-model-context`. The last two are the subtle ones. `ui/request-display-mode` asks to
move between `inline`, `fullscreen`, and `pip`, and the result reports the mode that was actually
set, which may not be the one requested. `ui/update-model-context` hands content to the model for
future turns, and each call **overwrites** the previous one rather than appending.

**Notifications, host to view.** `ui/notifications/tool-input` (once, after initialize),
`ui/notifications/tool-input-partial` (zero or more times while arguments are still streaming),
`ui/notifications/tool-result`, `ui/notifications/tool-cancelled`, and
`ui/notifications/host-context-changed`.

**Notifications, view to host.** `ui/notifications/size-changed` and
`ui/notifications/initialized`.

Three distinctions in that list are worth memorizing, because they look alike and are not:
`notifications/message` logs to the host, `ui/message` adds a message to the conversation and
triggers a turn, and `ui/update-model-context` changes what the model sees next time without
saying anything.

The partial-input notifications deserve a warning. Their arguments are described as "best-effort
recovery of incomplete JSON, with unclosed structures automatically closed to produce valid
JSON". A view **may** ignore them entirely, **must not** rely on them for anything that matters,
and **should** expect fields to appear and change between notifications.

### The handshake that survived

Here is the point that confuses everyone who learns the two specifications in the wrong order.

**Revision 2026-07-28 removed `initialize`. MCP Apps still has `ui/initialize`, and that is
correct.** They are different handshakes on different channels. MCP's `initialize` was between a
client and a server over stdio or Hypertext Transfer Protocol (HTTP), and it is gone.
`ui/initialize` is between a view and a host over `postMessage`, and nothing in the core
revision touches it.

The `protocolVersion` in an `McpUiInitializeResult` is likewise the **Apps** version, not the
core one. Apps versions on its own cadence: it is at `2026-01-26` while core is at `2026-07-28`.
Conflating those two numbers will make a debugging session much longer than it needs to be.

### The Apps specification has not caught up with the stateless revision

This is the honest part, and it matters more than the mechanism.

Both `specification/2026-01-26/apps.mdx` and the in-progress draft mention **neither**
`server/discover`, **nor** `2026-07-28`, **nor** multi round-trip requests. Both still show
capability negotiation inside an `initialize` request. Their examples carry
`"protocolVersion": "2024-11-05"` in the negotiation section and `"2025-06-18"` in the transport
section, which is two different stale versions inside one document.

What follows from that, precisely:

- **The mechanism is unchanged.** An `extensions` entry keyed `io.modelcontextprotocol/ui`
  with `{"mimeTypes": ["text/html;profile=mcp-app"]}` is still exactly right.
- **Only its location moved**, to per-request `_meta`, per section 1.
- **The "register UI tool variants at connection time" pattern in the Apps text no longer maps
  to anything**, because there is no connection-scoped negotiated state. It has to become
  per-request branching.
- **`ui/initialize` is genuinely unaffected**, for the reason above. Do not "fix" it.

There is one combination neither specification addresses at all: **MCP Apps together with the
tasks extension.** Whether a UI-bound tool may return a `CreateTaskResult`, and what
`ui/notifications/tool-result` is supposed to do for a task that completes four minutes later, is
simply not written down. If you need that shape today, you are designing it, not implementing it.

## 5. The worked example, and what the software development kit actually ships

The complete project is [code/24-mcp-app/](../../code/24-mcp-app/): one tool, one `ui://`
resource, one widget, ten tests.

**The Python software development kit (SDK) ships the server half of MCP Apps.** This surprised
me, and it is worth stating precisely because [Post 09](../09-tasks/index.md) had to report the
opposite for tasks. In `mcp` 2.0.0b2:

```python
from mcp.server.apps import Apps, APP_MIME_TYPE, EXTENSION_ID, client_supports_apps
```

`Apps` is an `Extension` in the SEP-2133 sense, and passing it to `MCPServer(extensions=[apps])`
does four things: stamps `_meta.ui.resourceUri` on every tool registered through it, serves the
`ui://` resource with the right MIME type, advertises `io.modelcontextprotocol/ui` in the
server's capabilities, and refuses at construction time to publish a tool whose `resourceUri`
has no matching resource.

The whole server side is this:

```python
apps = Apps()

@apps.tool(resource_uri=APP_URI, title="World clock", annotations=READ_ONLY)
def world_clock(ctx: Context, zones: list[str] | None = None) -> ClockReadings:
    """Report the current time in several time zones."""
    log.info("MCP Apps negotiated on this request: %s", client_supports_apps(ctx))
    return _read(zones or DEFAULT_ZONES)

apps.add_html_resource(
    APP_URI,
    WIDGET_HTML,
    csp=ResourceCsp(connect_domains=[], resource_domains=[]),
    prefers_border=True,
)

mcp = MCPServer("world-clock", instructions=..., extensions=[apps])
```

`client_supports_apps(ctx)` reads **this request's** `_meta` and returns `True` only when the
client named both the extension and the MIME type. It belongs inside the handler, never at
startup: one server process serves a UI-capable client and a text-only client on interleaved
requests, and there is no connection to hang a decision on.

Notice how small the branch is in that function. The answer is identical either way, and only a
log line differs. **A server whose answer changes shape depending on that branch has made the
interface load-bearing**, which is precisely what section 1's degradation rule forbids and what
[Post 23](../23-multi-client/index.md) built an entire test suite around.

Here is the real result, captured from the running server:

```json
{
  "content": [{ "type": "text", "text": "{\n  \"captured_at\": \"2026-07-27 01:10\", ... }" }],
  "structuredContent": {
    "captured_at": "2026-07-27 01:10",
    "readings": [
      { "zone": "UTC", "local_time": "2026-07-27 01:10", "utc_offset": "+0000" },
      { "zone": "Asia/Tokyo", "local_time": "2026-07-27 10:10", "utc_offset": "+0900" }
    ]
  },
  "isError": false,
  "resultType": "complete"
}
```

An ordinary `CallToolResult`. `resultType` is `"complete"`, not something new. The extension adds
no result shape at all, which is why a host that has never heard of it works perfectly.

### What the SDK does not ship

**There is no Python view library, and there cannot be one.** The view is a browser document, so
the client half of the `postMessage` dialect is JavaScript. The
`@modelcontextprotocol/ext-apps` package provides an `App` class for it, and the specification is
explicit that the class is a convenience rather than a requirement.

So [`widget.html`](../../code/24-mcp-app/src/mcp_app_demo/widget.html) writes the dialect out by
hand. It is about forty lines:

```javascript
function post(message) {
  window.parent.postMessage({ jsonrpc: "2.0", ...message }, "*");
}

function request(method, params) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    post({ id, method, params });
  });
}

request("ui/initialize", {
  appCapabilities: { availableDisplayModes: ["inline"] },
  clientInfo: { name: "World Clock App", version: "0.1.0" },
}).then(() => notify("ui/notifications/initialized", {}));
```

Seeing it once is worth more than a library, for the same reason
[Post 03](../03-wire-protocol/index.md) showed the JSON before the decorator.

The one thing the project cannot test is the rendering, because that needs a browser. Everything
else is asserted: the `_meta.ui.resourceUri` on the tool, the MIME type on the resource, the CSP
metadata, the capability advertisement, and that a UI-declaring client and a plain one get
identical `structuredContent`.

### Which hosts support it

From the extensions client support matrix, the hosts listed as supporting MCP Apps are: Claude
on the web, Claude Desktop, VS Code with GitHub Copilot, Microsoft 365 Copilot, Goose, Postman,
MCPJam, ChatGPT, Cursor, Archestra.AI, and PostHog Code.

Two caveats on that list. The matrix is community-maintained through pull requests, so it lags.
And it tracks only MCP Apps and the two authorization extensions; tasks is absent from it
entirely, which is why [Post 09](../09-tasks/index.md) could not tell you who implements tasks.

To try it yourself, serve the project over HTTP, tunnel it, and add the tunnel URL as a custom
connector under Settings, Connectors, Add custom connector. Custom connectors require a paid
plan. The `ext-apps` repository also ships an `examples/basic-host` you can point at the same
URL locally.

## 6. What is still open

Three gaps are worth naming, because a reader deciding whether to adopt MCP deserves to know
what the protocol does not yet answer.

![Three lanes on a timeline. The left lane, shipped, holds the 2026-07-28 core and the stable extensions: MCP Apps and enterprise-managed authorization. The middle lane, moving, holds tasks and the experimental extension repositories. The right lane, open, holds three items with no accepted proposal: tool-definition pinning, portable identity, and agent-to-agent delegation. A footer notes that the Extensions Track requires a working reference implementation in an official SDK before a proposal can even be reviewed.](diagrams/03-the-road-ahead.svg)
*Shipped, moving, and open. The third column is the honest answer to "is MCP finished?"*

**No tool-definition pinning.** Nothing in 2026-07-28 lets a client record what a tool's
description and schema were yesterday and refuse to run a changed one today. A server can
therefore ship benign tools, wait to be approved, and change the descriptions afterward, which
is the attack usually called a rug pull. A host can defend itself locally, by hashing each tool
definition it has approved and re-prompting when the hash changes, but that is a host feature
rather than a protocol guarantee: it varies by host, no server can rely on it, and nothing in
`tools/list` helps. A proposal to standardize pinning exists and, at the time of writing, has
not attracted a sponsor, which under the SEP process means it is not moving. Check its status
before you plan around it.

**No identity.** MCP has authorization ([Post 20](../20-authorization/index.md)): a server can be
an OAuth 2.1 resource server and validate a token for an audience that is itself. What it does
not have is a notion of *who* is behind a request that survives across servers. There is no
portable end-user identity, no delegation chain, and no way for a second server to know that the
first one was acting for the same person. Every deployment solves this with something outside
the protocol.

**No agent-to-agent.** MCP connects a host to servers. It does not describe one agent calling
another, negotiating a sub-task, or reporting back. Tasks gave long-running work a lifecycle,
which is the nearest thing, but a task is still one server answering one client. Whether that
belongs in MCP at all is an open question, not an oversight.

To that list add the two ambiguities this post has already flagged: the Apps specification's
stale negotiation text, and the total absence of any statement about Apps combined with tasks.

## 7. How to change any of this

The process is public, and the barrier is lower than most people assume, though not in the place
they assume.

An extension goes through five steps. **Propose** a SEP in the main repository, typed
**Extensions Track**, naming the working group and the extension maintainers. **Implement** it.
**Review**, where the core maintainers have final authority. **Publish**, as a pull request
adding the extension to an extension repository. **Adopt**, which is everyone else's decision,
not yours.

Step two is the one that surprises people:

> At least one reference implementation in an official SDK is REQUIRED before the SEP can be
> reviewed.

Not before it is accepted. Before it is **reviewed**. An idea with no code does not enter the
queue. That single rule explains more about the shape of MCP's evolution than any governance
document: the extensions that exist are the ones somebody was willing to build twice, once as
prose and once as software.

Two more things follow from the framework. Post-acceptance iteration needs no core-maintainer
review, because repository maintainers own their extension and coordinate through their working
group. And **promotion into core is optional and separate**, requiring its own Standards Track
SEP. Plenty of extensions should never be promoted, and an industry-specific one probably never
will be.

Three things SEP-2133 explicitly leaves unspecified, in case you were looking for a problem to
work on: a mechanism for advertising an extension's schema, dependencies between extensions, and
profiles that group extensions together.

If you want the smaller version of contributing: the client support matrix is maintained by pull
request, and it is out of date. So is at least one page this post has had to contradict.

## 8. Where to go from here

This series opened with a copy-paste workflow and an integration count. Here is what you can now
do that you could not then.

**You can read the wire.** Given a trace, you can name every field, say which `_meta` keys are
required, tell a protocol error from a tool-execution error, and reproduce any of it with
`curl`. That is the skill everything else rests on, because it is the one that lets you debug
somebody else's implementation.

**You can build a server that other people's applications connect to.** Tools with schemas a
model gets right, resources and prompts that are additive rather than load-bearing, elicitation
through multi round-trip requests, and long work as tasks. Four projects' worth of it, tested
in memory without a subprocess.

**You can write the other side.** A client is a few hundred lines, and a host is a loop around a
model plus a permission gate. Having written both, no host behavior you meet is mysterious any
more.

**You can deploy it.** Statelessness turned MCP servers into ordinary web services, so the
interesting work moved to authorization, observability, and cost. And you can publish it, so
that the six configuration files of [Post 23](../23-multi-client/index.md) become one install
command.

**And you can tell the difference between what the protocol guarantees and what it does not.**
That was [Post 19](../19-security/index.md)'s job, and it is the most valuable thing here.
Prompt injection, line jumping, rug pulls, cross-server shadowing, the confused deputy: MCP does
not stop any of them, and a server author who knows the names can design against them.

The one thing this series has tried hardest to teach is a habit rather than a fact. **Check the
revision.** Most of what is written about MCP describes a protocol with an `initialize`
handshake, a session identifier, and a server-to-client request channel. None of those exist in
2026-07-28. The Apps extension's own specification, as section 4 showed, has examples from two
older revisions inside one file. Being able to notice that, in a document that looks
authoritative, is worth more than any single mechanism in these twenty-four posts.

[Post 01](../01-what-is-mcp/index.md) claimed that MCP turns *N* times *M* integrations into *N*
plus *M*. Post 23 tested the claim on six hosts and one server and found that the wire format
holds, and that what breaks is configuration. That is a good result for a protocol not yet two
years old. It is also, honestly, the whole of it: MCP is not intelligent, it does not make your
data safe, and it adds nothing to your model. It means the integration you write today still
works in the application you adopt next year.

Which leaves the part nobody else can do for you. The server worth building is the one that
needs domain knowledge you already have and somebody else would have to acquire. Go and write
that one.

---

## Common pitfalls

- **Assuming an extension can be negotiated once.** There is no handshake and no session. The
  client's `extensions` map arrives on every request and a server **must not** cache it against a
  connection. Read it fresh, inside the handler.
- **Making the interface load-bearing.** A tool whose `content` says "see the widget" is broken
  on every host that cannot render one, and useless to the model that called it. `content` must
  stand alone; `structuredContent` is the extra.
- **Trying to "fix" `ui/initialize` because MCP removed `initialize`.** Different channel,
  different handshake, different version number. `McpUiInitializeResult.protocolVersion` is the
  Apps version, `2026-01-26`, not the core one.
- **Copying negotiation examples out of the Apps or extensions documentation.** Both still show
  an `initialize` wrapper and stale protocol versions. The mechanism is right, the placement is
  not.
- **Embedding UI content inline instead of predeclaring a `ui://` resource.** The specification
  chose predeclaration deliberately, so hosts can prefetch, cache, and review. An inline
  document gets none of that.
- **Assuming a declared permission was granted.** `_meta.ui.permissions` is a request. Feature
  detect in the view, and have a path for the answer being no.
- **Treating an extension's fields as trusted.** SEP-2133 says both parties **should** validate
  anything an extension introduces. An extension widens your attack surface exactly as much as
  it widens your feature set.
- **Waiting for the protocol to solve pinning, identity, or agent-to-agent.** None of the three
  has an accepted proposal. If your design needs one of them, you are building it yourself.

---

## Further reading

- MCP Apps extension specification, revision 2026-01-26, `ext-apps` repository. Every normative
  rule quoted here: the tool linkage, the `ui://` resource, the double-iframe sandbox, the CSP
  defaults, and the thirteen `ui/` methods. <https://github.com/modelcontextprotocol/ext-apps>
- SEP-1865, *"MCP Apps"* (2025), Extensions Track, Status Stable (2026-01-26).
- SEP-2133, *"Extensions"* (2025), Status Final. The definition, the naming rule, the
  opt-in-by-default requirement, the graceful-degradation clause, and the five-step process in
  section 7. <https://modelcontextprotocol.io/seps/2133-extensions>
- Specification, *"Versioning"* § extension negotiation, revision 2026-07-28. The authoritative
  placement of an `extensions` declaration, against the stale `initialize` examples elsewhere.
- Extensions overview and client support matrix (2026). The official and experimental extension
  repositories, and which hosts implement Apps.
  <https://modelcontextprotocol.io/extensions/client-matrix>
- MCP Apps launch announcement (2026). The hosts supporting it at general availability, which is
  a much shorter list than the matrix carries six months later.
  <https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **Your own server.** The most useful next step is not another post. Take the smallest tool your
  team already wishes an assistant could call, build it with
  [code/05-first-server/](../../code/05-first-server/) as the skeleton, and connect it with the
  configuration file for your host from
  **[Post 23 — One server, every client](../23-multi-client/index.md)**.
- **[Post 01 — What MCP is, and the problem it solves](../01-what-is-mcp/index.md)**: worth
  rereading once now. The *N* times *M* argument reads differently after twenty-three posts, and
  it is the shortest statement of what you have actually been building.
- **The specification itself**, revision 2026-07-28. It is shorter than this series and now
  entirely readable to you, which was the point.
  <https://modelcontextprotocol.io/specification/>
