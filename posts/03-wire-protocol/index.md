# 03 · The wire protocol: JSON-RPC, discovery, and the stateless model

> **TL;DR.** Every message in the Model Context Protocol (MCP) is a JSON-RPC 2.0 object, and since revision 2026-07-28 every request carries everything the server needs in order to answer it: the protocol revision, the client's capabilities, and who is asking. Nothing is negotiated up front and nothing is remembered afterwards, which is what turns an MCP server into an ordinary web service you can put behind a round-robin load balancer. This post reads the format field by field, from the JSON-RPC envelope through `server/discover`, `_meta`, and `resultType`, and ends on the two kinds of failure. Only one of them is a protocol error.
>
> **After reading this you will be able to:**
> - Write a valid MCP request by hand, with nothing but an HTTP client.
> - Name every reserved `_meta` key and say which side of the connection sets it.
> - Carry state across two tool calls using a server-minted handle instead of a connection.
> - Decide whether a given failure belongs in a JSON-RPC `error` object or in a result with `isError: true`.

![A tools/call request written out as JSON, with leader lines from each reserved metadata key to a short note explaining what that key tells the server.](diagrams/01-self-describing-request.svg) *Every key the server needs is in this one message, because there is no earlier message.*

> **On reading order.** This is one of the longest posts in the series, and it is reference-shaped: the wire format, field by field. It will keep. If you would rather have a server running first, go to [Post 05](../05-first-server/index.md), write one, and come back here the first time something breaks. Nothing in Part II assumes you read this post first.

---

## 1. One message has to be enough

Suppose you want to know what a server can do. Not through a host, not through a library: you want to ask it yourself, from a terminal, and read the answer with your eyes.

Until revision 2025-11-25 that was a sequence rather than a question. You sent an `initialize` request, read the reply, sent a `notifications/initialized` notification back, kept whatever `Mcp-Session-Id` header the server minted, and only then asked what you actually came to ask. A `ping` every so often kept the session from lapsing. Skip a step and the server refused, usually without telling you which step you skipped.

All of that was deleted. Revision 2026-07-28 removed the handshake (Specification Enhancement Proposal, or SEP, number 2575), removed protocol-level sessions (SEP-2567), and removed `ping` with no replacement. Nothing took over their jobs, and where those words turn up again below it is always as history. What took their place is a single rule from the specification's base protocol page:

> The Model Context Protocol (MCP) is a **stateless protocol**: all the information needed to process a request is contained in the request itself. A server processes each request independently; no state should be inferred from previous requests, even those on the same connection or stream.

The hero diagram above is that rule drawn out. One request, carrying its own revision, its own capability declaration, and its own idea of who is calling. Read the message and you know everything the server knows.

That has a pleasant consequence for you as a reader, which is that the protocol is now learnable by inspection, and a more serious one for you as an operator, which [Post 02](../02-architecture/index.md) hinted at and section 7 makes concrete: any request can be answered by any copy of your server. This post is the wire format, in the order you would meet it: the envelope, then discovery, then the metadata, then a full exchange, then the awkward parts.

## 2. JSON-RPC 2.0 in ten minutes

JSON-RPC 2.0 is a convention for calling a procedure inside another process by sending it one object encoded in JavaScript Object Notation (JSON) and getting one object back. The "RPC" is remote procedure call. It is deliberately tiny: it fixes the shape of the messages and says nothing at all about how the bytes travel. Pipes, sockets, and Hypertext Transfer Protocol (HTTP) requests are all fine, which is why the same message format works over both MCP transports in [Post 04](../04-transports/index.md).

There are exactly four message shapes. You have now met all of MCP's framing.

**A request** asks for something and expects an answer.

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": { "name": "get_weather" }
}
```

`jsonrpc` is the literal string `"2.0"` on every message. `id` is a string or an integer, and MCP tightens the base standard twice: it **must not** be `null`, and it **must not** match the id of any other request the sender has issued and not yet had answered. `method` names the operation. `params` is an optional object.

In this revision, requests travel one way only. The specification's message-patterns page is blunt about it: servers **must not** initiate JSON-RPC requests, and clients do not send JSON-RPC responses. If you have seen a diagram where the server calls the client, that diagram is a revision out of date.

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

**An error response** is an unsuccessful answer.

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

It also repeats the `id`, except in the one case where the request was so malformed that the id could not be read. `code` is an integer, `message` should be a concise single sentence, and an optional `data` member may carry anything. A response has a `result` or an `error`, never both and never neither.

**A notification** is a message that expects no answer.

```json
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": { "progressToken": "wx-7", "progress": 1, "total": 3 }
}
```

The only structural difference from a request is the missing `id`, and the receiver **must not** send a response. Notifications are how a server reports progress and how it announces that a list has changed.

Three things about ids that catch people out. They are local to one client and one connection, so client A's request `5` has nothing whatsoever to do with client B's request `5`. They are correlation tokens and not sequence numbers, so any unique value works and `"d-1"` is as legal as `1`. And there is no batching to worry about: on the Streamable HTTP transport the body of a POST **must** be a single JSON-RPC request or notification.

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

Everything else you will ever see on an MCP connection is a notification, a result, or an error. [Post 06](../06-tools-in-depth/index.md), [Post 07](../07-resources-and-prompts/index.md), and [Post 09](../09-tasks/index.md) fill in what each method carries.

## 3. `server/discover`, and what replaced negotiation

Capability negotiation used to be a conversation. It is now two independent one-way statements, and neither side agrees to anything.

The client states its capabilities on every single request, in `_meta`. That is section 4. The server states its capabilities in the result of one method, `server/discover`. **Servers must implement `server/discover`. Clients may call it and are not required to.** A client that already knows what it is talking to can go straight to `tools/list`.

The request has no parameters at all. `params` contains `_meta` and nothing else:

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

The result is where a server introduces itself:

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

Five fields are required and two are not. `resultType`, `supportedVersions`, `capabilities`, `ttlMs`, and `cacheScope` must all be present; `instructions` and `_meta` are optional, although a server **should** put its `serverInfo` in `_meta`. Note that `supportedVersions` is a plural array, not a single negotiated revision, and that `server/discover` is a cacheable method, which is why the freshness hint `ttlMs` and the sharing hint `cacheScope` are mandatory on it.

`capabilities` is a `ServerCapabilities` object, and its entire field set is small:

| Field | Meaning |
|---|---|
| `tools` | The server has tools. `listChanged` says whether it announces changes. |
| `resources` | The server has resources. `subscribe` and `listChanged` are its sub-flags. |
| `prompts` | The server has prompts, with its own `listChanged`. |
| `completions` | The server answers `completion/complete`. |
| `experimental` | Non-standard capabilities, keyed freely. |
| `extensions` | Declared extensions, keyed by reverse Domain Name System (DNS) identifier. |
| `logging` | Deprecated in 2026-07-28. You will meet it in older servers. |

A missing key means the capability is absent, and calling into an absent capability gets you `-32601`. Note what `capabilities` is not: it is not a catalog. It tells you the server has tools; it does not tell you which. You still call `tools/list`.

**Version handling has no handshake either.** Each request declares its revision, and if the server does not implement that revision it answers with `-32022` and tells you what it does implement:

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

That caching matters because probing is awkward. A modern client talking to a legacy server does not get a clean rejection: the server may reject, may stay silent, or may quietly process an era-ambiguous method under legacy rules. The recommended probe on the standard input and output transport is to send `server/discover` first, then read the outcome three ways. A `DiscoverResult` means modern. A recognizable modern JSON-RPC error also means modern, so retry with a listed revision and do **not** fall back. Anything else, including a timeout, means legacy. The specification adds one warning that is easy to get wrong: the fallback **must not** be keyed to one specific error code.

## 4. The `_meta` contract, key by key

`_meta` is where the per-request state lives now. It is a plain object that hangs off `params` on a request and off `result` on a response, and it has a naming grammar so that your keys and the specification's keys never collide.

A key has an optional **prefix** and a **name**. The prefix is dot-separated labels followed by a slash, written in reverse DNS notation, so `com.example/`. Any prefix whose second label is `modelcontextprotocol` or `mcp` is reserved for the protocol, which makes `io.modelcontextprotocol/`, `dev.mcp/`, and `com.mcp.tools/` off limits, while `com.example.mcp/` is yours because its second label is `example`. The name must begin and end with an alphanumeric character and may contain hyphens, underscores, and dots between.

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

Six notes, one per group.

**The two required keys are required in the schema, not just in the prose.** A request missing either one is malformed, and the server **must** reject it with `-32602`, which on HTTP means status `400`. An empty `"io.modelcontextprotocol/clientCapabilities": {}` is valid and means "I support no optional capabilities". Leaving the key out entirely is not.

**Capabilities are per-request, so the server must not remember them.** If handling a request needs a capability the client did not declare on that request, the server **must** return `-32021`, with `data.requiredCapabilities` naming what was missing, again with HTTP status `400`. A server that infers capabilities from an earlier request is not conformant.

**`clientInfo` and `serverInfo` are for humans.** They are self-reported, the protocol does not verify them, and implementations **should not** change behavior or make security decisions based on them. Send them anyway; they are what makes a log readable.

**`logLevel` is opt-in and per-request.** If the key is absent, the server **must not** emit any `notifications/message` for that request. This replaced a `logging/setLevel` method that no longer exists. Logging as a whole is deprecated as of 2026-07-28, with standard error output and OpenTelemetry named as the migration path, so treat this key as something to recognize rather than to build on.

**`progressToken` opts a request into progress notifications** bearing that token, on that request's response stream only.

**`traceparent`, `tracestate`, and `baggage` are the one exception to the prefix rule.** They are unprefixed on purpose, so that MCP trace context matches World Wide Web Consortium (W3C) Trace Context and existing OpenTelemetry conventions. [Post 21](../21-deploying/index.md) wires them into a real trace.

Here is a `tools/call` with every request-side key present at once. The specification never prints them together, so this message is composed from the individual field definitions, but every name, every casing, and every value shape is exact.

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

Read it as a server would. The two required keys are present, so the request is well formed. `elicitation` is declared with both modes, so this server is allowed to come back asking the user a question; had the client declared neither mode, the server's only lawful move would be `-32021`. `logLevel: "info"` means log notifications at `info` or above are welcome, on this request and no other. `progressToken` means progress notifications are welcome too. The three trace keys mean a distributed trace is already in flight and your server should join it rather than start one.

One warning about reading the specification's own examples. Most feature pages print requests with `_meta` omitted, and several carry an explicit note saying so for brevity. That is abbreviation, not permission. On the wire, both required keys go on every request.

## 5. `resultType`, and why every result carries one

Every result in this revision is a tagged union, and `resultType` is the tag.

```typescript
export type ResultType = "complete" | "input_required" | string;

export interface Result {
  _meta?: ResultMetaObject;
  resultType: ResultType;        // not optional
  [key: string]: unknown;
}
```

Two values are defined by the core protocol.

`"complete"` means the request finished and the result holds the final content. This is almost every result you will ever see: a `ListToolsResult`, a `DiscoverResult`, a `CallToolResult` that ran to the end.

`"input_required"` means the server cannot finish without something only the client can supply, so it is answering with a partial result instead of a final one. The body is an `InputRequiredResult`, and it is the foundation of Multi Round-Trip Requests (MRTR), which is how a server asks the user a question now that it cannot call the client. [Post 08](../08-elicitation-and-mrtr/index.md) is entirely about that loop. For this post, one property matters: the original request is **over**. The server answered it. Anything the client does next is a brand new request with a new id.

Only three methods may ever answer `input_required`: `tools/call`, `resources/read`, and `prompts/get`. Servers **must not** send an `InputRequiredResult` on any other request, and the schema enforces it by typing only those three response wrappers as a union.

Four rules govern the field, and the last one is the useful one.

- Extensions **may** add values. The tasks extension adds `"task"`, which [Post 09](../09-tasks/index.md) covers.
- The set of values a client accepts is the core set plus the values of extensions it has advertised in its capabilities.
- A `resultType` the client does not recognize **must** be treated as invalid.
- An **absent** `resultType` **must** be treated as `"complete"`, for compatibility with servers implementing earlier revisions.

If you are writing a client, that last rule is the difference between working against real servers and only working against new ones.

## 6. A complete annotated trace

Here is an entire useful exchange. A client talks to a weather server at `https://example.com/mcp`, behind a plain round-robin load balancer with three identical backends. Each message is annotated with the backend it happened to land on, to make the point that none of them matter. The trace is assembled from the specification's own examples rather than captured from a live server, so read it as the shape of a real exchange.

**① Client to server, `server/discover`.** Lands on backend A. The transport headers get their own post; for now, note only that the revision in the header must match the revision in the body.

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

**② Server to client, the `DiscoverResult`.** HTTP `200`, one JSON object.

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

This server is dual-era: it lists 2025-11-25 as well. There is no `resources` key and no `prompts` key, so `prompts/list` would answer `-32601`. The client may reuse this result for an hour, and because `cacheScope` is `"public"` a shared gateway may serve it to somebody else.

**③ Client to server, `tools/list`.** Lands on backend C, which has never seen this client. Nothing from ① is needed, and the client re-sends both required `_meta` keys because the server is forbidden from remembering them.

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

**④ Server to client, the `ListToolsResult`.**

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

**⑤ Client to server, `tools/call`.** Lands on backend B. The tool name is mirrored into an `Mcp-Name` header so that a load balancer can route on it without parsing the body.

```http
POST /mcp HTTP/1.1
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

**⑥ Server to client, the `CallToolResult`.**

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

Six messages, three backends, and not one of them depended on which machine answered the last one. Had the server needed a value it did not have, ⑥ would have carried `resultType: "input_required"` instead, request `2` would still have been finished and closed, and the client would have come back with a new request bearing a new id. That is [Post 08](../08-elicitation-and-mrtr/index.md).

## 7. What statelessness costs, and the handle that pays it back

Nothing is free. Three costs, in the order you will feel them.

**Bytes.** Both required `_meta` keys go on every request, forever. On a chatty connection that is real overhead, and it is the same on both transports by design, because SEP-2575 insisted the transports not diverge.

**A design you may have been planning.** List results **must not** vary per connection or as a side effect of other requests on that connection. The pattern where a `connect` tool makes a `query` tool appear in later `tools/list` results is no longer conformant. There is one important carve-out: the list **may** vary by the authorization presented on the request, so returning only the tools a caller's granted scopes permit is entirely fine. Filtering by credential is allowed; filtering by history is not.

**Any cross-call state you actually need.** Some work genuinely spans two calls. You open something, you add to it, you commit it.

The answer is a **handle**: an opaque identifier your server mints, returns in a result, and accepts back as an ordinary tool argument. The specification is explicit that this is not a protocol feature at all. From the wire's perspective a handle is a string in a result and a string in a later argument object, and nothing in the schema knows it is special.

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

The model is what carries `basket_id` forward, which is worth sitting with for a moment: the continuity in your server is now maintained by something that also has to decide what to say next. Design accordingly. The specification names four considerations, and all four are practical.

1. **Authorization.** A handle is a name, not a capability. Validate on every call that this caller may touch this basket. On a server with no authentication at all, the handle effectively is the credential, so it needs the entropy of a version 4 universally unique identifier and a bounded lifetime.
2. **Opacity.** Clients and models **must** be able to treat it as meaningless. Do not encode a primary key, a path, or a user id in plaintext.
3. **Lifetime.** State it in the creating tool's *description*, in words, for example "baskets expire after 24 hours of inactivity". The description is the only place the model can read it.
4. **Expiry.** When a handle has gone, return a tool execution error rather than a protocol error, so the model can recover by creating a new one. Section 8 explains the difference.

There is a second cross-call carrier, `requestState`, which a server mints when it answers `input_required` and the client echoes back verbatim on the retry. It is opaque to the client in exactly the same way and it travels through untrusted hands, so the specification requires servers to treat it as attacker-controlled input and to integrity-protect it with authenticated encryption when it influences authorization or business logic. [Post 08](../08-elicitation-and-mrtr/index.md) has the details.

What you get in return is the diagram below. Three identical replicas, no affinity, no shared store to keep them in sync, and a failed replica costs you one retry instead of a whole conversation.

![The old model on the left, where a load balancer must pin a client to one replica using a session id, beside the new model on the right, where three identical replicas each answer any request in turn.](diagrams/02-stateful-vs-stateless.svg) *The session identifier was the thing that forced sticky routing. Removing it removed the requirement.*

## 8. Two kinds of failure, and only one is an error

This is the distinction that most tutorials get wrong, including an earlier edition of this series, which claimed MCP defines extra error codes for tool failures. It does not, and the correction is worth stating plainly: **MCP has never defined an error code for a tool that ran and failed, because a tool that ran and failed did not fail at the protocol layer.**

![Two columns: a protocol error carried in a JSON-RPC error object with no result member, which is not always an HTTP 200, and a tool execution error carried in a successful HTTP 200 result with isError set to true and fed back to the model.](diagrams/03-error-taxonomy.svg) *Left, the request was wrong. Right, the request was fine and the work did not succeed. Only the right-hand one reaches the model.*

**Protocol errors** are problems with the request itself, the kind a model is unlikely to be able to fix: an unknown method, an unknown tool name, malformed parameters, a server that broke. They are returned as a JSON-RPC `error` object.

| Condition | Code | HTTP |
|---|---|---|
| Unparseable JSON | `-32700` | |
| Not a valid JSON-RPC request object | `-32600` | |
| Unknown method, or a method behind an undeclared server capability | `-32601` | `404` |
| Missing required `_meta` field | `-32602` | `400` |
| Unknown tool name, or arguments that violate the schema | `-32602` | |
| Unknown prompt name, bad pagination cursor, resource not found | `-32602` | |
| Internal failure | `-32603` | |
| Header value does not match the body | `-32020` | `400` |
| Server needs a client capability the request did not declare | `-32021` | `400` |
| Server does not implement the requested revision | `-32022` | `400` |

Three of those are MCP's own, and they are new in this revision. The specification now partitions the implementation-defined range: `-32000` to `-32019` is legacy, nothing new may be allocated there, and receivers **must not** assume any meaning for those codes; `-32020` to `-32099` belongs to the specification alone, and an implementation **must not** emit a code in that range that the specification has not defined. Two older codes are retired and **must not** be emitted: `-32002`, which used to mean resource not found and is now `-32602`, and `-32042`. A client **should** still accept `-32002` from an older server.

**Tool execution errors** are the opposite case. The request was valid, the tool ran, and the work did not succeed: an upstream application programming interface (API) returned a failure, a date was in the past, a business rule refused. These are returned as a **successful** JSON-RPC response.

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resultType": "complete",
    "content": [
      {
        "type": "text",
        "text": "Invalid departure date: must be in the future. Today is 2026-08-08."
      }
    ],
    "isError": true
  }
}
```

HTTP `200`. `resultType: "complete"`, because the request completed. `isError: true`, which defaults to `false` when absent. And a `content` block written for a reader who will try again, which is the whole point. The schema states the reason directly: errors originating from the tool should be reported inside the result with `isError` set, not as a protocol-level error, because otherwise the model cannot see that an error occurred and self-correct. Clients **should** pass tool execution errors to the model; they **may** pass protocol errors, though recovery is less likely.

The boundary between the two is narrower than it looks, because "invalid input" sits on both sides. The rule that resolves it: a request that violates the *shape* of `CallToolRequest` is a protocol error, and a request that is perfectly well formed but semantically wrong for this tool is a tool execution error. `{"date": 42}` against a string schema is `-32602`. `{"date": "1999-01-01"}` for a flight next week is `isError: true`.

## 9. Doing it by hand

None of the above needs a software development kit (SDK). Here is a script that speaks the protocol with an HTTP client and nothing else, in [snippets/raw_discover.py](snippets/raw_discover.py):

```python
PROTOCOL_VERSION = "2026-07-28"

def build(request_id, method, params=None):
    """A JSON-RPC 2.0 request with the two mandatory _meta keys."""
    body = dict(params or {})
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "raw-discover", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}
```

The full file adds the four headers, a reader that copes with either a single JSON reply or a Server-Sent Events (SSE) stream, and a `main` that sends `server/discover` and then `tools/list` and prints both results. Run it against any modern server:

```bash
uv run --with 'httpx<1' python raw_discover.py http://127.0.0.1:8000/mcp
```

The upper bound on `httpx` is deliberate, and the same habit the rest of the series applies to the SDK. httpx 1.0 removes `timeout` from the `Client` constructor, so an unpinned `--with httpx` will start failing the day that release lands.

Pointed at the server built in [Post 05](../05-first-server/index.md), it prints this:

```
server        system-info 2.0.0b2
versions      2026-07-28
capabilities  completions, prompts, resources, tools
cacheable     0 ms, scope private

4 tools
  get_system_info          required: nothing
  find_process             required: name
  terminate_process        required: pid
  watch_cpu                required: nothing
```

Four things in that output are worth pausing on. The revision on the `versions` line is the one this series targets, and it was read rather than negotiated. The capability list came from `server/discover` rather than from a handshake. The `cacheable` line is the `ttlMs` and `cacheScope` pair every list result now carries, here declining to be cached. And `terminate_process` requires only `pid`, even though the tool cannot run without a human approving it, because the approval is not something the caller supplies. [Post 08](../08-elicitation-and-mrtr/index.md) explains why.

One thing in that output is not what it looks like. The `2.0.0b2` beside the server name is the SDK's version, not this project's. `app.py` constructs `MCPServer("system-info")` with no version argument, so the SDK fills its own in, and the `0.2.0` in that project's `pyproject.toml` never reaches the wire. Pass a version explicitly if you want `serverInfo` to mean anything to whoever reads your logs.

It is about seventy lines once the comments come out, and it is a complete, conformant MCP client for two methods. That is the honest measure of how much protocol there is here. When something goes wrong in [Post 05](../05-first-server/index.md) and later, this script is the fastest way to find out whether the problem is your server or the thing calling it.

---

## Common pitfalls

- **Omitting `_meta` because the specification's examples omit it.** Most feature pages print `tools/call` without it and carry a small note saying they abbreviated. A real request without `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` earns `-32602` and HTTP `400`.
- **Returning a JSON-RPC error when a tool fails.** The model never sees a protocol error as something to fix. Return `resultType: "complete"` with `isError: true` and a message that says what to change, and reserve the `error` object for unknown methods, unknown tool names, and malformed input.
- **Caching a client's capabilities after the first request.** They are per-request. A server that remembers them is reading state it has been told not to read, and it will break the first time a host reconnects with a different configuration or a load balancer sends the next request elsewhere.
- **Reusing a JSON-RPC id that is still outstanding.** The id must not match any request the sender has issued and not yet had answered. Reuse produces a response the client matches to the wrong request, which surfaces as an inexplicable result rather than an error.
- **Reading `requestState` or a handle you were given.** Opaque means opaque, in both directions. Clients must echo `requestState` byte for byte, and a model or client that parses your handle will be broken by the next format you choose.
- **Treating `capabilities` from `server/discover` as the tool list.** It says a server has tools. Only `tools/list` says which.
- **Expecting `-32002` for a missing resource.** It is `-32602` now, and emitting `-32002` from a 2026-07-28 server is non-conformant. Accept it when you are the client, never send it when you are the server.

---

## Further reading

- Specification, *"Base protocol"*, revision 2026-07-28. The JSON-RPC message shapes, the error-code allocation policy, the statelessness rules, and the `_meta` grammar and reserved-key table quoted in sections 2, 4, and 8. <https://modelcontextprotocol.io/specification/draft/basic>
- Specification, *"server/discover"* and *"Versioning and compatibility"*, revision 2026-07-28. The request and result shapes, `ServerCapabilities`, and the modern, legacy, and dual-era model.
- Specification, *"Tools"* § Error handling, revision 2026-07-28. The split between protocol errors and tool execution errors, and the non-normative section on stateful tools that section 7 draws the basket example from.
- SEP-2575, *"Stateless MCP"*, and SEP-2567, *"Sessionless MCP"* (2026). The reasoning behind removing the handshake and the session, including the load-balancing argument.
- JSON-RPC 2.0 specification. The base standard MCP profiles. <https://www.jsonrpc.org/specification>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 04 — Transports: stdio and Streamable HTTP](../04-transports/index.md)**: the same messages, and the two ways they physically travel, including every header this post deferred.
- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**: what `resultType: "input_required"` actually does, and how a server asks a question when it cannot call anybody.
- **[Post 21 — Deploying to production](../21-deploying/index.md)**: the operational payoff, from round-robin load balancing to trace context in `_meta`.
