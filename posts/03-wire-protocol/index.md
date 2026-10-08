# 03 - The wire protocol: JSON-RPC, discovery, and the stateless model

> **TL;DR.** Every message in the Model Context Protocol (MCP) is a JSON-RPC 2.0 object (JavaScript Object Notation, remote procedure call), and since revision 2026-07-28 every request carries in its `_meta` everything the server needs to answer it: the revision, the client's capabilities, and who is asking. Nothing is negotiated up front and nothing is remembered afterwards, which is what lets an MCP server sit behind a plain round-robin load balancer like any other web service. This post reads the format field by field, from the envelope through `server/discover`, `_meta`, and `resultType`, and runs a client built from nothing but `httpx`, a Hypertext Transfer Protocol (HTTP) client, against a real server. It ends on the two kinds of failure, of which only one is a JSON-RPC error.
>
> **Prerequisites:** [Post 02](../02-architecture/index.md).
> **Safe to skip?** Skip it if the reader can already write a `tools/call` request with both required `_meta` keys by hand, read a `server/discover` result, and say which failures travel as a JSON-RPC `error` and which as a result with `isError: true`.
>
> **After reading, you will be able to:**
>
> - Write a valid MCP request by hand with nothing but an HTTP client.
> - Name every reserved `_meta` key and say which side of the connection sets it.
> - Carry state across two tool calls with a server-minted handle instead of a connection.
> - Decide whether a failure belongs in a JSON-RPC `error` object or in a result with `isError: true`.

![A tools/call request for get_system_info written out as JSON, its metadata object holding six keys with protocolVersion and clientCapabilities marked required, and a leader line from each key to a card saying what that key tells the server.](diagrams/01-self-describing-request.svg)

*Every key the server needs is in this one message, because there is no earlier message to remember.*

---

## 1. The question: what does one request have to carry?

This post asks what one self-describing MCP request carries, and why carrying all of it turns a server into an ordinary web service. The quickest way in is a reader who wants to know what a server can do: not through a host, not through a library, but asked directly from a terminal, with the answer read by eye.

Until revision 2025-11-25 that was a sequence rather than a question. The client sent an `initialize` request, read the reply, sent a `notifications/initialized` notification back, kept whatever `Mcp-Session-Id` header the server minted, and only then asked what it came to ask. A `ping` every so often checked that the other side was still responding, and a request that skipped a step was refused.

Revision 2026-07-28 deleted all of it. It removed the handshake (Specification Enhancement Proposal 2575, or SEP-2575), removed protocol-level sessions (SEP-2567), and removed `ping` with no replacement. A reader meets these mechanisms only in older code, and where they turn up below it is as history. What took their place is a single rule from the specification's base protocol page:

> The Model Context Protocol (MCP) is a **stateless protocol**: all the information needed to process a request is contained in the request itself. A server processes each request independently; no state should be inferred from previous requests, even those on the same connection or stream.

The hero diagram is that rule drawn out: one request carrying its own revision, its own capability declaration, and its own idea of who is calling. Reading the message is enough to know everything the server knows.

That has a pleasant consequence for a reader, which is that the protocol is now learnable by inspection, and a more serious one for an operator, which [post 02](../02-architecture/index.md) hinted at and section 7 makes concrete: any request can be answered by any copy of the server. The sections follow the order in which the wire format is met: the envelope, then discovery, then the metadata, then a full exchange, then the awkward parts, and finally a client written by hand.

## 2. JSON-RPC 2.0 in ten minutes

JSON-RPC 2.0 is a convention for calling a procedure inside another process by sending it one object encoded in JavaScript Object Notation (JSON) and getting one object back. RPC stands for remote procedure call. It is deliberately tiny: it fixes the shape of the messages and says nothing at all about how the bytes travel. Pipes, sockets, and HTTP requests are all fine, which is why the same message format works over both MCP transports in [post 04](../04-transports/index.md).

There are exactly four message shapes, and together they are all of MCP's framing.

**A request** asks for something and expects an answer.

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": { "name": "get_weather" }
}
```

`_meta` is left out of this one to show the envelope alone; section 4 puts it back. `jsonrpc` is the literal string `"2.0"` on every message. `id` is a string or an integer, and MCP tightens the base standard twice: it **must not** be `null`, and it **must not** match the id of any other request the sender has issued and not yet had answered. `method` names the operation. `params` is an optional object.

In this revision, requests travel one way only. The specification's patterns overview is blunt about it: "Servers **MUST NOT** initiate JSON-RPC requests, and clients do not send JSON-RPC responses." A diagram in which the server calls the client is a revision out of date.

**A result response** is a successful answer.

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "resultType": "complete",
    "content": [{ "type": "text", "text": "72F and partly cloudy." }]
  }
}
```

It repeats the request's `id`, and it carries a `result`. The `result` may follow any JSON object structure, with one MCP-specific requirement: it **must** include a `resultType`. Section 5 is about that field.

**An error response** is an unsuccessful answer. This one is in the form the specification's Tools page prints for a tool name the server does not have:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32602,
    "message": "Unknown tool: get_wether"
  }
}
```

It also repeats the `id`, except in the one case where the request was so malformed that the id could not be read. `code` is an integer, here `-32602` (`Invalid params`), `message` should be a concise single sentence, and an optional `data` member may carry anything. A response has a `result` or an `error`, never both and never neither.

**A notification** is a message that expects no answer.

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": { "progressToken": "wx-7", "progress": 1, "total": 3 }
}
```

The only structural difference from a request is the missing `id`, and the receiver **must not** send a response. Notifications are how a server reports progress on a request and how it announces that a list has changed.

Three things about ids catch people out. They are local to one client and one connection, so client A's request `5` has nothing whatsoever to do with client B's request `5`. They are correlation tokens and not sequence numbers, so any unique value works and `"d-1"` is as legal as `1`. And there is no batching: on the Streamable HTTP transport the body of a POST **must** be a single JSON-RPC request or notification, and on standard input and output (stdio) each line is a single message.

That is the whole envelope. The core protocol defines ten client requests inside it, and this is the complete list:

| Method | What it asks for |
|---|---|
| `server/discover` | Which revisions and capabilities this server has |
| `tools/list` | The tools, one page at a time |
| `tools/call` | Run a tool |
| `resources/list` | The concrete resources |
| `resources/templates/list` | The parameterized resource patterns |
| `resources/read` | The contents of one resource |
| `prompts/list` | The prompts |
| `prompts/get` | One prompt, rendered with arguments |
| `completion/complete` | Argument autocompletion |
| `subscriptions/listen` | A stream of change notifications |

Everything else on an MCP connection is a notification, a result, or an error. [Post 06](../06-tools-in-depth/index.md), [post 07](../07-resources-and-prompts/index.md), and [post 09](../09-tasks/index.md) fill in what each method carries.

## 3. `server/discover`, and what replaced negotiation

Capability negotiation used to be a conversation. It is now two independent one-way statements, and neither side agrees to anything.

The client states its capabilities on every single request, in `_meta`; that is section 4. The server states its capabilities in the result of one method, `server/discover`. **Servers must implement `server/discover`. Clients may call it and are not required to.** A client that already knows what it is talking to can go straight to `tools/list`.

The request has no parameters at all: `params` contains `_meta` and nothing else. As the specification's `server/discover` page prints it:

```json
{
  "jsonrpc": "2.0",
  "id": "discover-1",
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "ExampleClient",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

The result is where a server introduces itself, again in the page's own example:

```json
{
  "jsonrpc": "2.0",
  "id": "discover-1",
  "result": {
    "resultType": "complete",
    "supportedVersions": ["2026-07-28"],
    "capabilities": {
      "tools": {},
      "resources": {}
    },
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "ExampleServer",
        "version": "1.0.0"
      }
    },
    "instructions": "This server provides weather and resource utilities.",
    "ttlMs": 3600000,
    "cacheScope": "public"
  }
}
```

Five fields are required and two are not. `resultType`, `supportedVersions`, `capabilities`, `ttlMs`, and `cacheScope` must all be present; `instructions` and `_meta` are optional, although a server **should** put its `serverInfo` in `_meta`. `supportedVersions` is a plural array, not a single negotiated revision, and `server/discover` is one of the six cacheable methods, which is why the freshness hint `ttlMs` and the sharing hint `cacheScope` are mandatory on it.

`capabilities` is a `ServerCapabilities` object, and its entire field set is small:

| Field | Meaning |
|---|---|
| `tools` | The server has tools. `listChanged` says whether it announces changes. |
| `resources` | The server has resources. `subscribe` and `listChanged` are its sub-flags. |
| `prompts` | The server has prompts, with its own `listChanged`. |
| `completions` | The server answers `completion/complete`. |
| `experimental` | Non-standard capabilities, keyed freely. |
| `extensions` | Declared extensions, keyed by reverse Domain Name System (DNS) identifier. |
| `logging` | Deprecated in 2026-07-28; it appears in older servers. |

A missing key means the capability is absent, and calling into an absent capability earns `-32601` (`Method not found`). `capabilities` is not a catalog: it says the server has tools, not which ones. The tools come from `tools/list`.

**Revision handling has no handshake either.** Each request declares its revision, and if the server does not implement that revision it answers with `-32022` (`UnsupportedProtocolVersion`) and says what it does implement. The specification's versioning page prints this example:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32022,
    "message": "Unsupported protocol version",
    "data": {
      "supported": ["2026-07-28", "2025-11-25"],
      "requested": "1900-01-01"
    }
  }
}
```

The client **should** pick a mutually supported revision from `data.supported` and retry.

The specification also names three eras, and the vocabulary is worth having. A **modern** implementation carries revision, identity, and capabilities as per-request metadata, which means 2026-07-28 and later. A **legacy** implementation establishes a session with a handshake, which means 2025-11-25 and earlier. A **dual-era** implementation supports both. Era is a property of the server, not of a request, and clients **should** cache the answer for the lifetime of the server process or origin.

That caching matters because probing is awkward. A modern client talking to a legacy server does not get a clean rejection: the server may reject, may stay silent, or may quietly process an era-ambiguous method under legacy rules. The recommended probe on the stdio transport is to send `server/discover` first, then read the outcome three ways. A `DiscoverResult` means modern. A recognizable modern JSON-RPC error also means modern, so the client retries with a listed revision and does **not** fall back. Anything else, including a timeout, means legacy. The specification adds one warning that is easy to get wrong: the fallback **must not** be keyed to one specific error code. [Post 04](../04-transports/index.md) returns to probing for the era.

## 4. The `_meta` contract, key by key

`_meta` is where the per-request state lives now. It is a plain object that hangs off `params` on a request and off `result` on a response, and it has a naming grammar so that an implementation's own keys and the specification's keys never collide.

A key has an optional **prefix** and a **name**. The prefix is dot-separated labels followed by a slash, written in reverse DNS notation, so `com.example/`. Any prefix whose second label is `modelcontextprotocol` or `mcp` is reserved for the protocol, which makes `io.modelcontextprotocol/`, `dev.mcp/`, and `com.mcp.tools/` off limits, while `com.example.mcp/` is free because its second label is `example`. The name, unless empty, must begin and end with an alphanumeric character and may contain hyphens, underscores, and dots between.

These are the reserved keys, all of them:

| Key | Type | Appears on | Required |
|---|---|---|---|
| `io.modelcontextprotocol/protocolVersion` | `string` | every client request | **Yes** |
| `io.modelcontextprotocol/clientCapabilities` | `ClientCapabilities` | every client request | **Yes** |
| `io.modelcontextprotocol/clientInfo` | `Implementation` | client requests | No, but **should** |
| `io.modelcontextprotocol/logLevel` | `LoggingLevel` | client requests | No |
| `io.modelcontextprotocol/serverInfo` | `Implementation` | results | No, but **should** |
| `io.modelcontextprotocol/subscriptionId` | `string` or `number` | messages on a `subscriptions/listen` stream | **Yes**, on those |
| `progressToken` | `string` or `number` | requests | No |
| `traceparent`, `tracestate`, `baggage` | `string` | any message | No |

The client sets every key in the table that lives on a request; the server sets `serverInfo` and `subscriptionId`; the three trace keys may ride on any message from either side. Six notes, one per group.

**The two required keys are required in the schema, not just in the prose.** A request missing either one is malformed, and the server **must** reject it with `-32602`, which on HTTP means status `400`. An empty `"io.modelcontextprotocol/clientCapabilities": {}` is valid and means "I support no optional capabilities". Leaving the key out entirely is not.

**Capabilities are per-request, so the server must not remember them.** If handling a request needs a capability the client did not declare on that request, the server **must** return `-32021` (`MissingRequiredClientCapability`), with `data.requiredCapabilities` naming what was missing, again with HTTP status `400`. A server that infers capabilities from an earlier request is not conformant.

**`clientInfo` and `serverInfo` are for humans.** They are self-reported, the protocol does not verify them, and implementations **should not** change behavior or make security decisions based on them. They are still worth sending, because they are what makes a log readable.

**`logLevel` is opt-in and per-request.** If the key is absent, the server **must not** emit any `notifications/message` for that request. This replaced a `logging/setLevel` method that no longer exists. Logging as a whole is deprecated as of 2026-07-28, with standard error output and OpenTelemetry named as the migration path, so the key is something to recognize rather than to build on.

**`progressToken` opts a request into progress notifications** bearing that token, on that request's response stream only.

**`traceparent`, `tracestate`, and `baggage` are the one exception to the prefix rule.** They are unprefixed on purpose, so that MCP trace context matches World Wide Web Consortium (W3C) Trace Context and existing OpenTelemetry conventions. [Post 21](../21-deploying/index.md) wires them into a real trace.

Here is a `tools/call` with every request-side key present at once. The specification never prints them together, so this message is composed from the individual field definitions, but every name, every casing, and every value shape follows the schema.

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": {
      "location": "Seattle, WA"
    },
    "_meta": {
      "progressToken": "wx-7",
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {
        "elicitation": { "form": {}, "url": {} },
        "extensions": { "io.modelcontextprotocol/tasks": {} }
      },
      "io.modelcontextprotocol/clientInfo": {
        "name": "ExampleClient",
        "version": "1.0.0",
        "title": "Example Desktop Client"
      },
      "io.modelcontextprotocol/logLevel": "info",
      "traceparent": "00-0af7651916cd43dd8448eb211c80319c-00f067aa0ba902b7-01",
      "tracestate": "congo=t61rcWkgMzE",
      "baggage": "userId=alice,serverNode=DF%2028"
    }
  }
}
```

A server reads it like this. The two required keys are present, so the request is well formed. `elicitation` is declared with both modes, so this server is allowed to come back asking the user a question; had the client declared neither mode, the server's only lawful move would be `-32021`. `logLevel: "info"` means log notifications at `info` or above are welcome, on this request and no other. `progressToken` means progress notifications are welcome too. The three trace keys mean a distributed trace is already in flight, and the server should join it rather than start one.

One warning about the specification's own examples. Feature pages print requests with `_meta` omitted, several with an explicit note saying so for brevity. That is abbreviation, not permission. On the wire, both required keys go on every request, and section 10 shows what a server answers when they are missing.

## 5. `resultType`, and why every result carries one

Every result in this revision is a tagged union, and `resultType` is the tag. The schema declares it like this:

```typescript
export type ResultType = "complete" | "input_required" | string;

export interface Result {
  _meta?: ResultMetaObject;
  resultType: ResultType;        // not optional
  [key: string]: unknown;
}
```

Two values are defined by the core protocol.

`"complete"` means the request finished and the result holds the final content. This is almost every result there is: a `ListToolsResult`, a `DiscoverResult`, a `CallToolResult` that ran to the end.

`"input_required"` means the server cannot finish without something only the client can supply, so it answers with a partial result instead of a final one. The body is an `InputRequiredResult`, and it is the foundation of Multi Round-Trip Requests (MRTR), which is how a server asks the user a question now that it cannot call the client. [Post 08](../08-elicitation-and-mrtr/index.md) is entirely about that loop. For this post, one property matters: the original request is **over**. The server answered it. Anything the client does next is a brand new request with a new id.

Only three methods may ever answer `input_required`: `tools/call`, `resources/read`, and `prompts/get`. Servers **must not** send an `InputRequiredResult` on any other request, and the schema enforces it by typing only those three response wrappers as a union.

Four rules govern the field, and the last one is the useful one.

- Extensions **may** add values. The tasks extension adds `"task"`, which [post 09](../09-tasks/index.md) covers.
- The set of values a client accepts is the core set plus the values of extensions it has advertised in its capabilities.
- A `resultType` the client does not recognize **must** be treated as invalid.
- An **absent** `resultType` **must** be treated as `"complete"`, for compatibility with servers implementing earlier revisions.

For a client author, that last rule is the difference between working against real servers and only working against new ones.

## 6. A complete annotated trace

Here is an entire useful exchange. A client talks to a weather server at `https://example.com/mcp`, behind a plain round-robin load balancer with three identical backends. Each message is annotated with the backend it happened to land on, to make the point that none of them matter. The trace is assembled from the specification's own examples rather than captured from a live server, so it shows the shape of a real exchange; section 9 runs a real one.

**Step 1, client to server, `server/discover`.** Lands on backend A. The transport headers get their own post; for now, the one rule to note is that the revision in the header must match the revision in the body.

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: server/discover
```

```json
{
  "jsonrpc": "2.0",
  "id": "d-1",
  "method": "server/discover",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } }
    }
  }
}
```

**Step 2, server to client, the `DiscoverResult`.** HTTP `200`, one JSON object.

```json
{
  "jsonrpc": "2.0",
  "id": "d-1",
  "result": {
    "resultType": "complete",
    "supportedVersions": ["2026-07-28", "2025-11-25"],
    "capabilities": { "tools": { "listChanged": true } },
    "instructions": "Weather lookup for United States locations.",
    "ttlMs": 3600000,
    "cacheScope": "public",
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "ExampleServer", "version": "2.4.0" }
    }
  }
}
```

This server is dual-era: it lists 2025-11-25 as well. There is no `resources` key and no `prompts` key, so `prompts/list` would answer `-32601`. The client may reuse this result for an hour, since 3,600,000 milliseconds is 60 minutes, and because `cacheScope` is `"public"` a shared gateway may serve it to somebody else.

**Step 3, client to server, `tools/list`.** Lands on backend C, which has never seen this client. Nothing from step 1 is needed, and the client re-sends both required `_meta` keys because the server is forbidden from remembering them.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } }
    }
  }
}
```

**Step 4, server to client, the `ListToolsResult`.**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "complete",
    "tools": [
      {
        "name": "get_weather",
        "title": "Current weather",
        "description": "Current conditions for a United States city or zip code.",
        "inputSchema": {
          "type": "object",
          "properties": { "location": { "type": "string" } },
          "required": ["location"]
        }
      }
    ],
    "ttlMs": 300000,
    "cacheScope": "public",
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "ExampleServer", "version": "2.4.0" }
    }
  }
}
```

No `nextCursor`, so this is the end of the list. This result **must not** differ because the request happened to reach backend C rather than backend A. That requirement is what makes `ttlMs` trustworthy.

**Step 5, client to server, `tools/call`.** Lands on backend B. The tool name is mirrored into an `Mcp-Name` header so that a load balancer can route on it without parsing the body.

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_weather
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": { "location": "Seattle, WA" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } }
    }
  }
}
```

**Step 6, server to client, the `CallToolResult`.**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "resultType": "complete",
    "content": [
      { "type": "text", "text": "{\"temperatureF\": 54.1, \"conditions\": \"Rain\"}" }
    ],
    "structuredContent": { "temperatureF": 54.1, "conditions": "Rain" },
    "isError": false,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "ExampleServer", "version": "2.4.0" }
    }
  }
}
```

`tools/call` is not cacheable, so there is no `ttlMs` and no `cacheScope` here. The text block duplicates the structured content as serialized JSON, which the specification recommends for compatibility with clients that do not read `structuredContent`.

Six messages, three backends, and not one of them depended on which machine answered the last one. Had the server needed a value it did not have, step 6 would have carried `resultType: "input_required"` instead, request `2` would still have been finished and closed, and the client would have come back with a new request bearing a new id. That is [post 08](../08-elicitation-and-mrtr/index.md).

## 7. What statelessness costs, and the handle that pays it back

Nothing is free. There are three costs, in the order a server author meets them.

**Bytes.** Both required `_meta` keys go on every request, forever, and `clientInfo`, which the specification recommends, rides along with them. The `_meta` object that the client of section 9 sends is 183 bytes of compact JSON on every request, before any parameter. It is the same on both transports by design, because SEP-2575 insisted the transports not diverge.

**A design that no longer works.** List results **must not** vary per connection or as a side effect of other requests on that connection. The pattern where a `connect` tool makes a `query` tool appear in later `tools/list` results is no longer conformant. There is one important carve-out: the list **may** vary by the authorization presented on the request, so returning only the tools a caller's granted scopes permit is entirely fine. Filtering by credential is allowed; filtering by history is not.

**Any cross-call state that is genuinely needed.** Some work spans two calls: open something, add to it, commit it.

The answer is a **handle**: an opaque identifier the server mints, returns in a result, and accepts back as an ordinary tool argument. The specification is explicit that this is not a protocol feature at all. From the wire's perspective a handle is a string in a result and a string in a later argument object, and nothing in the schema knows it is special. This example is adapted from the one the Tools page prints in its non-normative section on stateful tools:

```jsonc
// tools/call
{ "name": "create_basket", "arguments": {} }

// result
{
  "content": [{ "type": "text", "text": "Created basket bsk_a1b2c3" }],
  "structuredContent": { "basket_id": "bsk_a1b2c3" }
}

// tools/call, some time later, quite possibly on another backend
{ "name": "add_item", "arguments": { "basket_id": "bsk_a1b2c3", "sku": "AX-9" } }
```

The model is what carries `basket_id` forward, which is worth sitting with for a moment: the continuity in the server is now maintained by something that also has to decide what to say next. The specification names four design considerations, and all four are practical.

1. **Authorization.** A handle is a name, not a capability. The server checks on every call that this caller may touch this basket. On a server with no authentication at all, the handle effectively is the credential, so it needs the entropy of a version 4 universally unique identifier and a bounded lifetime.
2. **Opacity.** Clients and the model should be able to treat it as meaningless. A handle that encodes a primary key, a path, or a user id in plaintext invites both guessing and parsing.
3. **Lifetime.** The lifetime belongs in the creating tool's *description*, in words, for example "baskets expire after 24 hours of inactivity". The description is the only place the model can read it.
4. **Expiry.** When a handle has gone, the server returns a tool execution error rather than a protocol error, so the model can recover by creating a new one. Section 8 explains the difference.

There is a second cross-call carrier, `requestState`, which a server mints when it answers `input_required` and the client echoes back verbatim on the retry. It is opaque to the client in exactly the same way, and it travels through untrusted hands, so the specification requires servers to treat it as attacker-controlled input and, when it influences authorization, resource access, or business logic, to protect its integrity, for example with a hash-based message authentication code (HMAC) or authenticated encryption, and to reject state that fails verification. [Post 08](../08-elicitation-and-mrtr/index.md) has the details.

What comes back in return is the diagram below. Three identical replicas, no affinity, no shared session store to keep in sync, and a failed replica costs one retry instead of a whole conversation.

![Two panels. On the left, until revision 2025-11-25, a balancer pinned by a struck-through Mcp-Session-Id header to the one replica holding the session, the other two marked unusable. On the right, from 2026-07-28, a client sending its version and capabilities in each request and a balancer spreading requests round robin across three identical replicas.](diagrams/02-stateful-vs-stateless.svg)

*The session identifier was the thing that forced sticky routing. Removing it removed the requirement.*

## 8. Two kinds of failure, and only one is an error

This distinction is easy to get wrong, and an earlier edition of this series got it wrong by claiming that MCP defines extra error codes for tool failures. It does not: **MCP defines no error code for a tool that ran and failed, because a tool that ran and failed did not fail at the protocol layer.**

![Two columns. On the left, a protocol error, caused by an unknown method or a missing metadata key, carried in a JSON-RPC error object with a numeric code and no result member, not always with HTTP status 200, and handled by the client. On the right, a tool execution error, caused by a missing row, a timeout, or a departure date in the past, carried in a successful HTTP 200 result with isError true and fed back to the model.](diagrams/03-error-taxonomy.svg)

*Left, the request was wrong. Right, the request was fine and the work did not succeed. Only the right-hand one is written for the model to read.*

**Protocol errors** are problems with the request itself, the kind a model is unlikely to be able to fix: an unknown method, an unknown tool name, a request that does not satisfy the `CallToolRequest` schema, a server that broke. They are returned as a JSON-RPC `error` object. A blank HTTP cell below means the specification names no status.

| Condition | Code | HTTP |
|---|---|---|
| Unparseable JSON | `-32700` (`Parse error`) | |
| Not a valid JSON-RPC request object | `-32600` (`Invalid Request`) | |
| Unknown method, or a method behind an undeclared server capability | `-32601` | `404` |
| Missing required `_meta` field | `-32602` | `400` |
| Unknown tool name, or a `tools/call` that breaks the `CallToolRequest` shape | `-32602` | |
| Unknown prompt name, bad pagination cursor, resource not found | `-32602` | |
| Internal failure | `-32603` (`Internal error`) | |
| Header value does not match the body, or a required header is missing | `-32020` (`HeaderMismatch`) | `400` |
| Server needs a client capability the request did not declare | `-32021` | `400` |
| Server does not implement the requested revision | `-32022` | `400` |

Three of those are MCP's own, and they are new in this revision. The specification now partitions the implementation-defined range: `-32000` to `-32019` is legacy, nothing new may be allocated there, and apart from `-32002` receivers **must not** assume any meaning for those codes; `-32020` to `-32099` belongs to the specification alone, and an implementation **must not** emit a code in that range that the specification has not defined. Two older codes are retired and **must not** be emitted: `-32002`, which used to mean resource not found and is now `-32602`, and `-32042`, which meant that a uniform resource locator (URL) mode elicitation was required. A client **should** still accept `-32002` from an older server.

**Tool execution errors** are the opposite case. The request was valid, the tool ran, and the work did not succeed: an upstream application programming interface (API) returned a failure, an input value was in the wrong format or out of range, a business rule refused. These are returned as a **successful** JSON-RPC response, as the Tools page prints it:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resultType": "complete",
    "content": [
      {
        "type": "text",
        "text": "Invalid departure date: must be in the future. Current date is 08/08/2025."
      }
    ],
    "isError": true
  }
}
```

HTTP `200`. `resultType: "complete"`, because the request completed. `isError: true`, which defaults to `false` when absent. And a `content` block written for a reader who will try again, which is the whole point. The schema states the reason directly: errors originating from the tool should be reported inside the result with `isError` set, not as a protocol-level error, because otherwise the model cannot see that an error occurred and self-correct. Clients **should** pass tool execution errors to the model; they **may** pass protocol errors, though recovery is less likely.

The boundary between the two is narrower than it looks, because "invalid input" sits on both sides. The rule that resolves it: a request that violates the *shape* of `CallToolRequest` is a protocol error, and a request that is well formed but whose values are wrong for this tool is a tool execution error. `"arguments": "python"`, a string where `CallToolRequest` requires an object, is `-32602`. `{"date": "1999-01-01"}` for a flight next week is `isError: true`, and so is `{"date": 42}` against a tool whose schema wants a string: the Tools page lists input validation errors among tool execution errors, a split that SEP-1303 formalized.

> **A note on the text.** The schema's comment on `-32602` still lists "invalid tool arguments" beside "unknown tool name", which reads as if a schema-violating argument were a protocol error. The Tools page and SEP-1303 say the opposite for argument values, and the Python software development kit (SDK) follows them, so this post does too. The same SDK also departs from the Tools page in one place: `mcp==2.3.0` answers an unknown tool name with `isError: true` and the text `Unknown tool: get_wether`, where the page prints `-32602`. Section 10 shows both answers as they arrive.

## 9. Make it run: doing it by hand with `httpx`

None of the above needs an SDK. The script [snippets/raw_discover.py](snippets/raw_discover.py) speaks the protocol with `httpx`, a plain HTTP client, and nothing else. Its core is the function that builds every request:

```python
def build(request_id: int, method: str, params: dict | None = None) -> dict:
    """A JSON-RPC 2.0 request with the two mandatory _meta keys."""
    body = dict(params or {})
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "raw-discover", "version": "1.0.0"},
        # Required even when empty. Leaving the key out entirely is malformed,
        # and a conformant server will reject the request with -32602.
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}
```

The full file adds the four headers every POST carries (`Content-Type`, `Accept`, `MCP-Protocol-Version`, `Mcp-Method`, plus `Mcp-Name` on a `tools/call`), a reader that copes with either a single JSON reply or a Server-Sent Events (SSE) stream, and a `main` that sends `server/discover` and then `tools/list` and prints both results. Both scripts run from the series root, whose `pyproject.toml` pins `mcp[cli]==2.3.0` and `httpx<1`:

```text
uv run python posts/03-wire-protocol/snippets/raw_discover.py
uv run python posts/03-wire-protocol/snippets/raw_discover.py http://127.0.0.1:8000/mcp
uv run python posts/03-wire-protocol/snippets/what_can_go_wrong.py
```

With no argument, `raw_discover.py` starts the server built in [post 05](../05-first-server/index.md), from [code/05-first-server/](../../code/05-first-server/), over Streamable HTTP on 127.0.0.1 and a free port, queries it, and stops it again; the run takes a few seconds. Given a URL, it queries a server that is already running instead, such as the same project started with `uv run python -m system_info --http` inside its directory. The third command sends the broken requests of section 10. The upper bound on `httpx` is deliberate: the script is written against the 0.x interface, and the pin keeps a future major release from changing it underneath.

Against the post 05 server, `raw_discover.py` prints this:

```text
server        system-info, version ""
serverInfo    in result._meta of server/discover and of tools/list
versions      2026-07-28
capabilities  completions, prompts, resources, tools
cacheable     0 ms, scope private
_meta         183 bytes on every request

4 tools, cacheable 0 ms, scope private
  get_system_info          required: nothing
  find_process             required: name
  terminate_process        required: pid
  watch_cpu                required: nothing
```

Five things in that output are worth pausing on. The revision on the `versions` line is the one this series targets, and it was read rather than negotiated. The capability list came from `server/discover` rather than from a handshake. The two `cacheable` lines are the `ttlMs` and `cacheScope` pair that `server/discover` and `tools/list`, two of the six cacheable methods, must carry; a `ttlMs` of `0` means the result is stale at once, so this server declines to be cached. The `_meta` line is the per-request cost of section 7. And `terminate_process` requires only `pid`, even though the tool cannot run without a human approving it, because the approval is not something the caller supplies; [post 08](../08-elicitation-and-mrtr/index.md) explains why.

The first two lines are about identity. The `serverInfo` line says where the script found the server's self-description: in each result's `_meta` under `io.modelcontextprotocol/serverInfo`, on `tools/list` as well as on `server/discover`, which is where the specification puts it. The script's `server_info` function also looks for a top-level `serverInfo` field and names that place if the field turns up there, so a server that misplaces it is reported as such rather than as having none. The empty version is the project's doing: `app.py` constructs `MCPServer("system-info")` with no version argument, so the server reports an empty string, and the `0.2.0` in the project's `pyproject.toml` never reaches the wire. A server that wants `serverInfo` to mean anything in a log passes a version explicitly.

The client half of the file, `build`, `headers`, `read_result`, `call`, and `server_info`, is a complete MCP client for two methods. That is the honest measure of how much protocol there is here. When something goes wrong in post 05 and later, this script is the fastest way to find out whether the problem is the server or the thing calling it.

## 10. What can go wrong?

Every case below is triggered on purpose by `snippets/what_can_go_wrong.py` against the same post 05 server, and each request differs from a working one by one change. The script prints the HTTP status, then either the JSON-RPC error or, for a result, the first line of the tool's text or the number of tools listed.

**The `_meta` keys are left out**, for example because a request was copied from a feature page that abbreviated them. The server refuses before it looks at the method, and its message names the two keys it requires:

```text
_meta left out, as the feature pages print requests
  HTTP 400  error -32602  params._meta must be an object carrying the required 'io.modelcontextprotocol/protocolVersion' and 'io.modelcontextprotocol/clientCapabilities' envelope keys  id=1
clientInfo left out
  HTTP 200  result  4 tools
```

The second case is not a failure. `io.modelcontextprotocol/clientInfo` is a **should** in the specification, and the server answers a request without it in full. `build` still sends it, because it is what makes the server's log readable.

**The revision or a routing header disagrees with the server.** A revision the server does not implement earns `-32022` with the list it does implement; a header that names a different method from the body, or a `tools/call` without its `Mcp-Name` header, earns `-32020`:

```text
unsupported revision 1900-01-01
  HTTP 400  error -32022  Unsupported protocol version  data={"supported":["2026-07-28"],"requested":"1900-01-01"}  id=3
Mcp-Method header disagrees with the body
  HTTP 400  error -32020  mcp-method header does not match the request body's method  id=4
Mcp-Name left out of a tools/call
  HTTP 400  error -32020  mcp-name header does not match the request body's 'name' parameter  id=5
```

**A method or a tool name is misspelled.** The two typos look alike and travel differently. A misspelled method is a protocol error with status `404` and a JSON-RPC body, and the body is what the specification says distinguishes it from the bare `404` of a legacy server that has no modern MCP endpoint. A misspelled tool name, under this SDK, comes back as a successful result with `isError: true`, so a client that only checks for an `error` member will report success:

```text
unknown method
  HTTP 404  error -32601  Method not found  data="tools/lsit"  id=6
unknown tool name
  HTTP 200  result  isError=true  Unknown tool: get_wether
```

**The arguments are wrong.** A value of the wrong type for the tool is a tool execution error the model can read and fix; an `arguments` member that is not an object at all breaks `CallToolRequest` and is refused as `-32602`:

```text
argument of the wrong type
  HTTP 200  result  isError=true  Error executing tool find_process: 1 validation error for find_processArguments
arguments that are not an object
  HTTP 400  error -32602  Invalid request parameters  data=""  id=9
```

**A tool needs a capability the client did not declare.** `terminate_process` asks the user to confirm through elicitation, and `build` declares no capabilities, so the server cannot ask and says what was missing:

```text
elicitation needed but not declared
  HTTP 400  error -32021  Client did not declare the form elicitation capability required by resolver 'system_info.interactive:_confirm_terminate'  data={"requiredCapabilities":{"elicitation":{"form":{}}}}  id=10
```

**The body is not JSON.** The server cannot read the id, so the error carries `"id": null`, the one case section 2 allowed:

```text
unparseable JSON
  HTTP 400  error -32700  Parse error  id=null
```

The script's last case sends the same `tools/list` with nothing missing, and its output ends with `HTTP 200` and the four tools, which confirms that each failure above came from its one change.

## Common pitfalls

1. **Returning a JSON-RPC error when a tool fails.** The model is not meant to see a protocol error; return `resultType: "complete"` with `isError: true` and a message that says what to change, and keep the `error` object for unknown methods, unknown tools, and requests that break the method's shape.
2. **Caching a client's capabilities after the first request.** They are per-request, and a server that remembers them breaks the first time a host reconnects with a different configuration or a load balancer sends the next request elsewhere.
3. **Reusing a JSON-RPC id that is still outstanding.** The response then matches the wrong request, which surfaces as an inexplicable result rather than an error.
4. **Reading `requestState` or a handle.** Opaque means opaque in both directions: a client echoes `requestState` byte for byte, and a model or client that parses a handle breaks on the next format the server chooses.
5. **Treating `capabilities` from `server/discover` as the tool list.** It says a server has tools; only `tools/list` says which.
6. **Expecting `-32002` for a missing resource.** It is `-32602` now, and emitting `-32002` from a 2026-07-28 server is non-conformant; accept it as a client, never send it as a server.

## Further reading

- Model Context Protocol specification, *Base protocol*, revision 2026-07-28. The JSON-RPC message shapes, the error-code allocation policy, the statelessness rules, `resultType`, and the `_meta` grammar and reserved-key table that sections 2, 4, 5, and 8 rely on.
- Model Context Protocol specification, *server/discover* and *Versioning and compatibility*, revision 2026-07-28. The request and result shapes, `ServerCapabilities`, `-32022`, and the modern, legacy, and dual-era model of section 3.
- Model Context Protocol specification, *Tools*, revision 2026-07-28, the error-handling section and the non-normative section on stateful tools. The split between protocol errors and tool execution errors, and the basket example of section 7.
- SEP-2575, *Stateless MCP*, and SEP-2567, *Sessionless MCP*. The reasoning behind removing the handshake and the session, including the load-balancing argument.
- SEP-1303, *Input validation errors as tool execution errors*. Why a wrong argument value is reported in the result, as section 8 says.
- JSON-RPC 2.0 specification. The base standard MCP profiles.
- MCP Python SDK, release 2.3.0. The server behind every transcript in sections 9 and 10, driving [code/05-first-server/](../../code/05-first-server/).

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 04 - Transports: stdio and Streamable HTTP](../04-transports/index.md):** the same messages and the two ways they physically travel, including every header this post deferred.
- **[Post 08 - Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md):** what `resultType: "input_required"` actually does, and how a server asks a question when it cannot call anybody.
