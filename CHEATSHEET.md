# MCP cheatsheet

One page, protocol revision **2026-07-28**. Print it, pin it.

Everything here is the current revision. If a tutorial you are reading mentions `initialize`,
a session id, or sampling, it predates this.

---

## The three roles

| Role | Owns | Count |
|---|---|---|
| **Host** | the model, the conversation, every consent decision | one application |
| **Client** | transport, request correlation, capability cache | one per connected server |
| **Server** | tools, resources, prompts | your code |

The host is the security boundary. A server cannot enforce anything about how it is used.

## The primitives, and who pulls the trigger

| Primitive | Controlled by | Method |
|---|---|---|
| **Tool** | the model | `tools/call` |
| **Resource** | the application | `resources/read` |
| **Prompt** | the user | `prompts/get` |

Using a tool where a resource belongs is the most common design mistake in MCP servers.

---

## Every method

| Method | Direction | Notes |
|---|---|---|
| `server/discover` | client to server | servers **must** implement, clients **may** call |
| `tools/list` · `tools/call` | client to server | paginated list |
| `resources/list` · `resources/read` | client to server | |
| `resources/templates/list` | client to server | RFC 6570 templates |
| `prompts/list` · `prompts/get` | client to server | |
| `completion/complete` | client to server | for prompt args and template vars |
| `subscriptions/listen` | client to server | one stream, opt into each change type |
| `notifications/progress` | server to client | needs a `progressToken` on the request |
| `notifications/cancelled` | either | |
| `notifications/message` | server to client | only if `logLevel` was set; deprecated |

**Gone:** `initialize`, `notifications/initialized`, `ping`, `logging/setLevel`,
`resources/subscribe`, `resources/unsubscribe`, `notifications/roots/list_changed`.

---

## The `_meta` keys

Prefix is `io.modelcontextprotocol/` unless shown otherwise.

| Key | On | Required |
|---|---|---|
| `protocolVersion` | every client request | **yes** |
| `clientCapabilities` | every client request | **yes**, `{}` is valid |
| `clientInfo` | client requests | should |
| `logLevel` | client requests | no |
| `serverInfo` | results | should |
| `subscriptionId` | messages on a listen stream | yes, on those |
| `progressToken` | requests | no, **unprefixed** |
| `traceparent` · `tracestate` · `baggage` | any message | no, **unprefixed** |

A prefix whose second label is `modelcontextprotocol` or `mcp` is reserved. `com.example.mcp/`
is yours; `com.mcp.tools/` is not.

---

## `resultType`

Every result carries one. Absent **must** be read as `complete`.

- `complete` — done.
- `input_required` — the server needs something from the user. See MRTR below.
- `task` — only with the tasks extension.

## The MRTR loop

There is no server-to-client channel. A server that needs input **returns** and gets retried.

```
client  tools/call  { name, arguments, _meta }
server  result      { resultType: "input_required",
                      inputRequests: {...}, requestState: "<sealed>" }
        host asks the user
client  tools/call  { name, arguments, _meta,
                      inputResponses: {...}, requestState: "<sealed>" }
server  result      { resultType: "complete", content: [...] }
```

The retry may land on a different replica, so state travels in the message, never in memory.

**The trap:** answers are matched against a hash of the exact rendered question. Build the
question from the tool's arguments only. A timestamp or a live reading in the text means the
recorded answer never matches and the call never converges.

---

## Two kinds of failure

| | Protocol error | Tool execution error |
|---|---|---|
| Shape | JSON-RPC `error` object | successful `result`, `isError: true` |
| Cause | unknown method, bad params, missing `_meta` | the work failed |
| Who sees it | the client | **the model**, so it can retry |

Codes: `-32700` parse, `-32600` invalid request, `-32601` method not found, `-32602` invalid
params, `-32603` internal, `-32020`, `-32021` missing client capability, `-32022` unsupported
version. `-32002` and `-32042` are retired; resource-not-found is now `-32602`.

---

## Python SDK 2.x

```python
from mcp.server.mcpserver import MCPServer, Context, Resolve, Elicit
from mcp_types import ToolAnnotations

mcp = MCPServer("demo")

@mcp.tool()                     # parentheses REQUIRED
def add(a: int, b: int) -> int: # return type drives outputSchema
    """Docstring becomes the description."""
    return a + b

mcp.run()                       # stdio; run("streamable-http", host=, port=) for HTTP
```

| v1 | v2 |
|---|---|
| `mcp.server.fastmcp` / `FastMCP` | `mcp.server.mcpserver` / `MCPServer` |
| `mcp.types` | `mcp_types` |
| `McpError` | `MCPError` |
| camelCase attributes | snake_case (`read_only_hint`) |

**Python is snake_case, the wire is camelCase.** `read_only_hint` becomes `readOnlyHint`.

**Silent failure to know:** a return class with no class-body annotations yields
`outputSchema: null` with no warning. Use a dataclass or `structured_output=True`.

**Testing:** `async with Client(mcp) as c:` inside each test body. Never a yield fixture; the
task group must be exited by the task that entered it.

---

## Host configuration

| Client | File | Top-level key |
|---|---|---|
| Claude Desktop | `claude_desktop_config.json` | `mcpServers` |
| Claude Code | `.mcp.json` | `mcpServers` |
| Cursor | `.cursor/mcp.json` | `mcpServers` |
| VS Code | `.vscode/mcp.json` | **`servers`**, `type` required |
| Gemini CLI | `~/.gemini/settings.json` | `mcpServers`, **no `type`** |
| Zed | settings | **`context_servers`** |

Gemini CLI infers the transport from the key: `command` is stdio, `url` is SSE,
`httpUrl` is Streamable HTTP.

---

## Security, in six lines

1. Tool descriptions are untrusted input to the model. Injection lands at `tools/list`, before
   any approval prompt fires.
2. Annotations are hints. They enforce nothing.
3. Never accept a token that was not issued for you, and never forward one upstream.
4. Validate `Origin`; bind local servers to `127.0.0.1`.
5. Resource template variables are untrusted. Use an allowlist.
6. Private data, untrusted content, and an outbound channel together are the lethal trifecta.
   Remove one.

---

Full detail: [README.md](README.md) · [GLOSSARY.md](GLOSSARY.md) · [REFERENCES.md](REFERENCES.md)
