# 08 · Elicitation and MRTR: asking the user mid-call

> **TL;DR.** A Model Context Protocol (MCP) server can still stop mid-tool-call to ask the user a question, but since revision 2026-07-28 it does so by *returning* a partial result instead of *calling back* down the connection. The client collects the answer and calls the same tool again, re-sending the original arguments plus the answer and an opaque `requestState` blob the server minted. That inversion is called Multi Round-Trip Requests (MRTR), and it is what lets the retry land on a different machine than the original call and still work. This post walks the loop message by message, then builds a tool that refuses to act until a human confirms.
>
> **After reading this you will be able to:**
> - Trace an MRTR exchange message by message, and say where the state lives at each step.
> - Build a tool whose approval parameter the model can neither see nor forge.
> - Handle accept, decline, and cancel as three separate outcomes rather than one.
> - Protect `requestState` correctly on a server that runs more than one worker.

![A sequence diagram with three lifelines, user, host and client, and server. Five numbered messages run down the page: a tools/call, an input_required result carrying inputRequests and requestState, an off-the-wire exchange between the client and the user, a second tools/call carrying inputResponses and the same requestState, and a complete result.](diagrams/01-mrtr-loop.svg) *The server never sends a request. It answers one, and the client comes back with a new one.*

---

## 1. A tool that cannot finish without a human

Here is a tool you should not ship as written:

```python
@mcp.tool()
def terminate_process(pid: int) -> str:
    psutil.Process(pid).send_signal(signal.SIGTERM)
    return f"Sent SIGTERM to {pid}."
```

The model picks a process id, calls the tool, and something dies. There is no moment in that sequence where a person agreed. [Post 07](../07-resources-and-prompts/index.md) drew the line between the primitives by who pulls the trigger, and tools are the model-controlled one. Nothing in a tool call carries human intent.

The obvious repair is worse. Add a parameter:

```python
@mcp.tool()
def terminate_process(pid: int, approved: bool) -> str:
    if not approved:
        return "Not approved."
    ...
```

Now `approved` is in the published input schema, which means it is in the tool description the model reads, which means the model fills it in. You have built a field whose only purpose is to be set to `true` by the thing you were trying to gate. Annotations do not save you either: `destructiveHint` is a hint, and [Post 06](../06-tools-in-depth/index.md) is blunt about hints not being enforcement.

What the tool actually needs is to get a question in front of a human and not act until the answer comes back. MCP calls that **elicitation**, and it is the only non-deprecated client feature left in this revision. How a server performs it changed completely in 2026-07-28, and the change is worth understanding for its own sake, because it is the clearest illustration of what statelessness did to the protocol.

## 2. How this used to work, and why the back channel had to go

Until revision 2025-11-25 the mechanism was the obvious one. JSON-RPC, which is Remote Procedure Call over JavaScript Object Notation, is symmetric by design: either end may send a request. So your tool handler, partway through its work, sent one of its own back down the connection:

```jsonc
// server to client, in the old model. This is no longer legal.
{ "jsonrpc": "2.0", "id": "s-1", "method": "elicitation/create", "params": { ... } }
```

It then awaited the client's response, exactly as if it had made a network call, and carried on. Nothing in the base standard forbade this. MCP allowed it, and three features used it: `elicitation/create`, `sampling/createMessage`, and `roots/list`.

Three things had to be true for that to work. The connection had to be bidirectional and still open. The original `tools/call` had to remain in flight, unanswered, for as long as the user took to read the dialog. And the local variables of that half-finished handler had to stay in the memory of one specific process, because that process was the only one that could resume.

The last of those is the one that killed it. A request that must be finished by the same process that started it cannot be load balanced, cannot survive a deploy, and cannot be served by a stateless Hypertext Transfer Protocol (HTTP) worker. [Post 03](../03-wire-protocol/index.md) covered the sessionless model this revision adopted; the back channel was the last thing standing in its way.

So it was removed. The specification's message-patterns page now says, without qualification:

> Servers **MUST NOT** initiate JSON-RPC requests, and clients do not send JSON-RPC responses.

And the MRTR page states the migration:

> Servers **MUST** send server-to-client requests (such as `roots/list`, `sampling/createMessage`, or `elicitation/create`) using the MRTR pattern. The previous pattern of server-initiated requests is no longer supported. **This is a breaking change.**

![The old model on the left, where a server sends an elicitation/create request back down the connection and blocks waiting for a response, marked as removed; the new model on the right, where the server answers with an input_required result and the client returns with a second, independent tool call.](diagrams/02-back-channel-vs-return.svg) *Left, the server asks and waits. Right, the server answers and stops. Only the right-hand column exists in 2026-07-28.*

Sampling and roots did not survive this transition as features worth teaching: both are deprecated as of 2026-07-28, and [Post 18](../18-server-side-models/index.md) covers what replaced sampling. Elicitation did survive, in a new shape. If you are curious what happens when older Python code tries the old move, the software development kit (SDK) has an exception for it. `NoBackChannelError` is raised when a server tries to push a request over a transport with no channel for it, and the modern Streamable HTTP path hardcodes its `can_send_request` flag to `False`. There is no path back.

## 3. MRTR: return `input_required`, get retried

The replacement is four steps. Quoting the MRTR page:

1. Client sends an initial request to the server with the parameters needed to perform the operation.
2. Server determines that additional information is required to fulfill the request and responds requesting more information.
3. Client gathers the requested information from the user or other sources, then retries the original request including the additional requested information.
4. Server determines it has sufficient information to complete the operation, and responds with the final result.

Step 2 is the whole idea. The server does not ask; it *answers*, and its answer happens to be "not yet, and here is what I need." The original request is then **finished**. It got a JSON-RPC result, it is closed, and the server holds nothing.

The tag that marks such an answer is `resultType`, which [Post 03](../03-wire-protocol/index.md) introduced. Every result in this revision carries one, and it takes the value `"complete"` or `"input_required"`:

```typescript
export interface InputRequiredResult extends Result {
  inputRequests?: InputRequests;   // resultType is "input_required"
  requestState?: string;
}
```

`inputRequests` is a **map**, not a list. Its keys are identifiers the server invents and which must be unique within the request; its values are bare request objects, with `method` and `params` but no `jsonrpc` and no `id`. Only three methods may appear as values, and only one of them is not deprecated:

| `method` | Client capability required |
|---|---|
| `"elicitation/create"` | `elicitation`, and the specific mode |
| `"sampling/createMessage"` | `sampling`, deprecated |
| `"roots/list"` | `roots`, deprecated |

`requestState` is "an opaque string meaningful only to the server. Clients **MUST NOT** inspect, parse, modify, or make any assumptions about its contents." Section 5 is about what belongs in it.

The retry side is the mirror image. `CallToolRequestParams`, `ReadResourceRequestParams`, and `GetPromptRequestParams` all extend a common base:

```typescript
export interface InputResponseRequestParams extends RequestParams {
  inputResponses?: InputResponses;
  requestState?: string;
}
```

That inheritance is the reason `tools/call`, `resources/read`, and `prompts/get` are the only three methods that participate in MRTR. Servers **must not** send an `InputRequiredResult` on anything else. Note also where the two fields sit: inside `params`, as siblings of `name` and `arguments`. They are **not** in `_meta`.

**What a server may and must do.** The MRTR page lists eight requirements, and they are short enough to read in full.

1. A server **may** answer any supported client request with an `InputRequiredResult`.
2. It **may** include `inputRequests`. Keys are server-assigned and **must** be unique within the scope of the request; values **must** be an `ElicitRequest`, a `CreateMessageRequest`, or a `ListRootsRequest`.
3. It **may** include `requestState`, encoded however it likes: base64 JSON, an encrypted token, packed binary. The protocol never looks inside.
4. It **must** treat `requestState` as attacker-controlled input. If that state influences authorization, resource access, or business logic, the server **must** protect its integrity and **must** reject state that fails verification. The protection may be omitted only when tampering can cause nothing worse than the request failing.
5. To prevent replay it **should** bind three things inside the protected payload and check each on receipt: the authenticated principal, a short expiry, and an identifier for the originating request such as the method name plus a digest of its salient parameters.
6. Every `InputRequiredResult` **must** carry at least one of `inputRequests` or `requestState`.
7. A server **must not** send an `inputRequests` entry for a capability the client did not declare on that request.
8. A server **must not** assume the client will fulfill the requests or retry at all, and it **may** return `input_required` on repeated attempts at the same request.

**What a client must do.** Four requirements, and the third is the one that surprises people.

1. If the result carries `inputRequests`, the client **must** construct those inputs before retrying. If it carries only `requestState`, the client **may** retry immediately.
2. If the result carries `requestState`, the client **must** echo the exact value back and **must not** inspect, parse, or modify it. If there was no `requestState`, the client **must not** invent one.
3. **The JSON-RPC `id` on the retry must differ from the original**, because they are two independent requests.
4. Both fields affect only the retry of that one request. They **must not** be attached to anything else the client has in flight.

## 4. The complete message loop

Here is the whole thing on the wire. A desktop client is talking to a remote server at `https://example.com/mcp`, behind a plain round-robin load balancer with three identical backends, `A`, `B`, and `C`. The tool publishes a weather report as a GitHub gist and needs an account name the server does not have. The trace is assembled from the specification's own examples rather than captured from a live server, and the HTTP headers are trimmed to the ones this post needs.

**Message 1, client to server.** The model picked the tool. This POST lands on backend `B`.

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: create_weather_gist
```

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "create_weather_gist",
    "arguments": { "location": "Seattle, WA" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {}, "url": {} } }
    }
  }
}
```

The client declared `elicitation` with both modes. That declaration is what makes everything below legal. Had it declared neither mode, the server's only lawful move would be the error code `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`).

**Message 2, server to client.** HTTP `200`. Backend `B` has the weather but not the account name, so it answers with a question.

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "resultType": "input_required",
    "inputRequests": {
      "gh_user": {
        "method": "elicitation/create",
        "params": {
          "mode": "form",
          "message": "Which GitHub account should own this gist?",
          "requestedSchema": {
            "type": "object",
            "properties": {
              "username": { "type": "string", "title": "GitHub username", "minLength": 1, "maxLength": 39 },
              "public": { "type": "boolean", "title": "Make the gist public", "default": true }
            },
            "required": ["username"]
          }
        }
      }
    },
    "requestState": "v1.aead.b0RkS3l...",
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "ExampleServer", "version": "2.4.0" }
    }
  }
}
```

Read that carefully, because four things in it are the post. `resultType` is `"input_required"`, so this is a result and not a request. Request `3` is now complete and closed, and backend `B` retains nothing about it. `gh_user` is a key the server made up. And the `elicitation/create` object has a `method` and `params` but no `jsonrpc` and no `id`, because it is a bare request object living inside a map, not a message.

There is no `ttlMs` and no `cacheScope` either. Interim results are never cacheable.

**Off the wire.** The client shows which server is asking, renders two fields with `public` pre-checked from its `default`, and lets the user review, edit, decline, or dismiss. The user types `octocat` and submits. No MCP message is involved in any of this.

**Message 3, client to server.** The retry. This POST lands on backend `A`, a completely different machine from the one that asked the question.

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "create_weather_gist",
    "arguments": { "location": "Seattle, WA" },
    "inputResponses": {
      "gh_user": {
        "action": "accept",
        "content": { "username": "octocat", "public": true }
      }
    },
    "requestState": "v1.aead.b0RkS3l...",
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {}, "url": {} } }
    }
  }
}
```

The `id` is `4`, not `3`. The method, the tool name, and all the original arguments are re-sent verbatim. `inputResponses` is keyed by `gh_user`, matching the key backend `B` assigned, and its value is a bare `ElicitResult`. `requestState` comes back byte for byte; the client did not parse it and was forbidden to. Both required `_meta` keys are present again, because capabilities are per-request and a server is not allowed to remember them.

**Message 4, server to client.** Backend `A` verifies the seal on `requestState`, checks the principal, the expiry, and the embedded request digest, merges in `octocat`, calls GitHub, and finishes.

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resultType": "complete",
    "content": [{ "type": "text", "text": "Created gist https://gist.github.com/octocat/a1b2c3 as octocat." }],
    "structuredContent": { "url": "https://gist.github.com/octocat/a1b2c3", "owner": "octocat" },
    "isError": false,
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "ExampleServer", "version": "2.4.0" }
    }
  }
}
```

Two HTTP request and response pairs. Two independent JSON-RPC exchanges. Two different backends. Zero server-initiated requests, and zero session state.

## 5. Carrying state across round trips without a session

That trace contains the argument. Backend `B` asked the question and backend `A` answered it, and no message told `A` that `B` existed. If your server had kept the half-finished tool call in a dictionary keyed by request id, message 3 would have arrived at a process with an empty dictionary and the call would have failed. **State cannot live in memory.** Not in a module-level dict, not in an `asyncio` future, not in a `contextvar`.

Exactly two things survive a round trip.

**The client re-sends the original parameters.** The retry is a complete request: same method, same tool name, same arguments. For many tools that is enough on its own, and SEP-2322, the Specification Enhancement Proposal that introduced MRTR, says so in its own worked example: "No `requestState` is needed yet, since the original tool call arguments will be re-sent on retry."

**`requestState` carries whatever the arguments do not.** Anything the server computed, looked up, or already gathered in an earlier round goes here. When a call takes three rounds, the state typically grows each time to accumulate what has been answered so far.

Because it travels through the client, `requestState` is attacker-controlled input by definition. The specification is direct about the threat:

> Because `requestState` passes through the client, malicious or compromised clients could attempt to modify it to alter server behavior, bypass authorization checks, or corrupt server logic.

Hence server requirement 4. The example value the MRTR page prints is literally the string `"AEAD-protected blob"`, which is the specification signalling the expected construction rather than a format. AEAD is authenticated encryption with associated data: the payload is both encrypted and tamper-evident, so a modified blob fails verification instead of decrypting into something useful.

**What the Python SDK does, and where its default is wrong.** `MCPServer` seals `requestState` for you. Your code reads and writes plaintext, and the sealing happens at the boundary. By default it seals under a **process-local ephemeral key**, generated at startup.

That default is correct for `stdio` and for a single-process development server, and it is wrong for anything else. Two workers behind a load balancer have two different keys, so a retry that lands on the wrong worker fails verification. A restart invalidates every outstanding round trip. For a real deployment, supply the key yourself:

```python
from mcp.server.mcpserver import MCPServer, RequestStateSecurity

mcp = MCPServer("fleet", request_state_security=RequestStateSecurity(keys=[key]))
```

Keys are at least 32 bytes each, and the list exists so you can rotate: put the new key first, keep the old one until outstanding states expire. Passing a policy to an **unnamed** server raises `ValueError` at construction, which is deliberate: the server name is sealed into the state as an audience claim, so that state minted by another service sharing the same keys is rejected. When verification fails the server returns a frozen `-32602` (`Invalid params`): `{"code": -32602, "message": "Invalid or expired requestState"}`.

Two further notes. Replay defenses bound the window; they do not make state single-use. If a given `requestState` must be redeemable at most once, a one-time payment for instance, you have to enforce that yourself, server-side. And results produced by an MRTR retry **must not** be cached at all, because they depend on inputs that are not part of any cache key.

Nothing here forbids a stateful server. Section 9 describes a flow that requires one. `requestState` then degenerates into a lookup key, and the specification suggests exactly that: "Servers needing to correlate an elicitation across retries encode their own identifier in `requestState`."

## 6. Writing it: `Resolve` and `Elicit`

None of the above appears in your tool body. The high-level SDK expresses an elicitation as a **parameter the model cannot see**, filled in by a resolver function. The complete file is [code/05-first-server/src/system_info/interactive.py](../../code/05-first-server/src/system_info/interactive.py).

First, the shape of the question, as an ordinary Pydantic model:

```python
class TerminateConfirmation(BaseModel):
    confirm: bool = Field(description="Confirm that this process should be terminated.")
    reason: str = Field(default="", description="Optional note recorded in the server log.")
```

Then a resolver, which returns an `Elicit(...)` rather than performing one:

```python
def _confirm_terminate(pid: int) -> Elicit[TerminateConfirmation]:
    try:
        name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        name = "unknown"
    return Elicit(
        f"Terminate process {pid} ({name})? This cannot be undone.",
        TerminateConfirmation,
    )
```

The resolver takes `pid`, and the SDK supplies it by matching the parameter name against the tool's own arguments. Then the tool:

```python
@mcp.tool(title="Terminate a process", annotations=ToolAnnotations(destructive_hint=True, ...))
def terminate_process(
    pid: int,
    approval: Annotated[ElicitationResult[TerminateConfirmation], Resolve(_confirm_terminate)],
) -> str:
    if isinstance(approval, DeclinedElicitation):
        return f"Not terminated. The user declined to confirm ending process {pid}."
    if isinstance(approval, CancelledElicitation):
        return f"Not terminated. The confirmation was dismissed for process {pid}."
    assert isinstance(approval, AcceptedElicitation)
    if not approval.data.confirm:
        return f"Not terminated. The user answered no for process {pid}."
    ...
```

**The security property is measured, not asserted.** The `approval` parameter is stripped from the published input schema. From [verify/RESULTS.md](../../verify/RESULTS.md), the required properties of `terminate_process` are:

```json
["pid"]
```

`approval` is absent. A model reading `tools/list` sees a tool that takes a process id and nothing else, so it has no field in which to fabricate its own approval. That is the difference between this and the `approved: bool` parameter from section 1, and it is the reason to reach for `Resolve` rather than rolling your own.

**Transport selection is automatic and version-gated.** The SDK reads the revision the client declared in `_meta` and branches on it:

```python
_INPUT_REQUIRED_VERSION = "2026-07-28"
```

At `2026-07-28` or later the questions are batched into an `InputRequiredResult`. At `2025-11-25` or earlier the same source file uses the old back channel. You write the tool once and it serves both eras.

**The registration rules**, all enforced when the decorator runs, all worth knowing before you fight one:

- `Resolve` is honored **on tools only**. It is not read on prompts or resources.
- Only a top-level `Annotated[T, Resolve(f)]` counts. `Annotated[T, Resolve(f)] | None` raises `InvalidSignature`.
- A resolver's parameters must each be a `Context`, another `Resolve(...)`, or a tool argument matched **by name**. Anything else raises `InvalidSignature` at registration.
- Resolver cycles raise `InvalidSignature`.
- A resolver's return annotation may carry **at most one** `Elicit`, `Sample`, or `ListRoots` arm.
- **`Resolve(...)` combined with a declared `InputRequiredResult` return type on the same tool is rejected at registration.** There is one `requestState` channel per call, and you may drive it by hand or let `Resolve` drive it, not both. An undeclared one fails the call with `ToolError` instead.
- Each resolver is memoized once per call, keyed by resolver identity, so two parameters backed by the same function ask one question.
- Synchronous resolvers run on a worker thread.
- The key that appears in `inputRequests` on the wire is derived from the resolver's `module:qualname`, which is stable across workers. That is what makes the whole thing work on stateless HTTP.

If the client did not declare the capability the resolver needs, the call fails with `-32021` and a `requiredCapabilities` payload naming what was missing. Driving a one-file throwaway server with a resolver named `ask_confirm` and no elicitation support wired up at all, on `mcp==2.0.0b2`, the message read `Client did not declare the form elicitation capability required by resolver '__main__:ask_confirm'`. The resolver's `module:qualname` is in the text, which is the fastest way to find the one that asked.

## 7. Schema rules for what you can ask

Form mode schemas are deliberately poor, because every client has to render them:

> To simplify client user experience, form mode elicitation schemas are limited to **flat objects with primitive properties only**.

Four property types are allowed, and that is the whole list.

| Type | Constraints you may set |
|---|---|
| `string` | `minLength`, `maxLength`, `format`, `default` |
| `number` or `integer` | `minimum`, `maximum`, `default` |
| `boolean` | `default` |
| enum | single-select or multi-select, four shapes |

The permitted string formats are exactly `email`, `uri`, `date`, and `date-time`. Enums come as a bare `enum` array, as `oneOf` with `const` and `title` when you want display labels, and in two array-typed multi-select forms with `minItems` and `maxItems`. An older `enumNames` shape still exists in the schema and is documented for removal, so do not write new code against it.

Everything else is out: no nested objects, no arrays of objects beyond enum multi-selects, no `$ref`, no conditionals. `ElicitResult.content` values are typed `string | number | boolean | string[]`, which is the same restriction seen from the other side. Every primitive supports `default`, and clients that support defaults **should** pre-populate the field.

One rule is a hard prohibition rather than a limitation. Servers **must not** use form mode to request passwords, application programming interface (API) keys, access tokens, or payment credentials, and **must** use URL mode, which sends the user to a Uniform Resource Locator (URL) of the server's choosing, for anything involving them. The specification narrows what it means by sensitive: secrets and credentials that grant access or authorize transactions. A name, an email address, or a username is not categorically prohibited.

## 8. Accept, decline, cancel

The client's answer is an `ElicitResult`, and it has three actions:

```typescript
export interface ElicitResult {
  action: "accept" | "decline" | "cancel";
  content?: { [key: string]: string | number | boolean | string[] };
}
```

**Accept** means the user explicitly approved and submitted. In form mode `content` holds the submitted data; in URL mode it is omitted. **Decline** means the user explicitly said no: they clicked Reject, or Decline, or No. **Cancel** means the user dismissed without choosing: closed the dialog, clicked outside it, pressed Escape, or the browser failed to load.

Decline and cancel are not errors and there is no separate channel for them. They travel back to the server inside `inputResponses` on the retry, exactly like an accept, and your tool sees them as a value to branch on.

![Three columns, one per action, answering the same four questions down the page: what the user did, which SDK class arrives, whether it carries data, and what the terminate_process tool does. Only accept carries data, and even it acts only when the confirm field is true. A footer notes that none of the three is an error, that all three ride back inside inputResponses on the retry, and that an accept in URL mode means consent rather than completion.](diagrams/03-three-outcomes.svg) *Three outcomes, three code paths. Collapsing decline and cancel into "no" throws away the difference between a refusal and a closed window.*

The four outcomes below are real. They come from [verify/RESULTS.md](../../verify/RESULTS.md), produced against `mcp` 2.0.0b2 on Python 3.13.5 by driving the tool with a scripted elicitation callback:

| What the user did | What the tool returned |
|---|---|
| accept, `{"confirm": true}` | `No process with pid 999999 is running.` |
| accept, `{"confirm": false}` | `Not terminated. The user answered no for process 999999.` |
| decline | `Not terminated. The user declined to confirm ending process 999999.` |
| cancel | `Not terminated. The confirmation was dismissed for process 999999.` |

The second row is the one worth pausing on. `action: "accept"` means the user submitted the form, not that the user said yes. A confirmation checkbox left unchecked arrives as an accept with `confirm: false`, and a tool that treats "accepted" as "approved" will terminate a process whose owner explicitly declined to confirm it.

How you receive the outcome is your choice. Annotating the parameter as `Annotated[ElicitationResult[T], Resolve(f)]` gives you the full three-way union to branch on, as above. Annotating it as the bare model, `Annotated[TerminateConfirmation, Resolve(f)]`, unwraps to the data on accept and raises `ToolError` on decline or cancel, which is fine when there is nothing useful to say about a refusal.

**One honest hedge.** The specification says what a server should *do* with each action: process the data, handle the decline by offering alternatives, handle the cancel by perhaps asking again later. It does not say what the server must *return*. A `"complete"` result with `isError: true`, a `"complete"` result describing the abandonment without `isError`, and another `InputRequiredResult` re-asking are all permitted by the text, since server requirement 8 explicitly allows repeated prompting. Pick one and be consistent. The tool above returns a plain `"complete"` result with a sentence the model can relay, on the grounds that a user declining is not a failure of the tool.

## 9. URL mode, for authorization flows and payments

Form mode routes the answer through the client, which is precisely what you do not want for a credential. URL mode exists for that case:

> URL mode elicitation enables servers to direct users to external URLs for out-of-band interactions that must not pass through the MCP client. This is essential for auth flows, payment processing, and other sensitive or secure operations.

The request carries three fields, and unlike form mode the `mode` is required with no default:

```json
{
  "method": "elicitation/create",
  "params": {
    "mode": "url",
    "url": "https://mcp.example.com/ui/set_api_key",
    "message": "Please provide your API key to continue."
  }
}
```

The client's answer is `{ "action": "accept" }` and nothing more. Here is the semantic that catches everyone:

> The response with `action: "accept"` indicates that the user has **consented to the interaction**. It does **not** mean that the interaction is complete.

The user agreed to open the URL. Whether they finished whatever was on the other side happens out of band, and the client is never told. So on the retry your server determines from its own records whether the out-of-band work completed, and either returns the final result or answers `input_required` **again**, which is why server requirement 8 exists. Clients **should** give the user manual controls to retry or cancel the original request in the meantime.

This is the flow that forces a stateful server. The specification says so directly: the MCP server is responsible for storing and managing the third-party tokens obtained through URL mode elicitation, in other words the server must be stateful. That is not a contradiction of section 5; the protocol still carries no state, and your database is your own business.

Four constraints are worth carrying away.

**The cross-user phishing attack is real and named.** URL mode hands out a URL, and a URL can be forwarded. Alice triggers an elicitation, the server mints an authorization URL, Alice tricks Bob into opening it, Bob authorizes, and the server binds Bob's third-party tokens to Alice's account. The server **must** ensure the user who started the elicitation is the user who completes the flow. The recommended pattern is to point the elicitation at a route on your own domain, check that the browser session's subject matches the subject the MCP authorization layer gave you, and only then redirect onward.

**Never mint a pre-authenticated URL**, and never put credentials or personal data in the URL. A malicious client could use it to impersonate the user.

**Third-party credentials must not transit the client**, you must not reuse the client's credentials against the third-party service, and you must not use URL mode elicitation to authorize users for *your own* server. That is what OAuth, the Open Authorization framework, is for, and [Post 20](../20-authorization/index.md) covers it.

**Three things you may remember from 2025-11-25 are gone.** The `elicitationId` field and the `notifications/elicitation/complete` notification were both removed, because under MRTR the client learns the outcome by retrying rather than by being told. So is the error code `-32042` (`URL_ELICITATION_REQUIRED`), which existed in 2025-11-25 only and meant "use URL mode for this". It is retired: the code stays reserved and will never be reused, a client may still meet it coming from a server implementing an older revision, and an implementation of 2026-07-28 **must not** emit it. There is nothing to emit it *for* any more, because the elicitation request itself now names its own mode.

## 10. Designing questions a user can actually answer

The protocol will let you ask anything. Most of the craft is in not doing that.

**Derive the question from the arguments, deterministically.** This is the pitfall that costs the most debugging time, so it gets its own paragraph. The SDK asks each question once per call, and it recognizes an already-answered question by matching the recorded answer against a **SHA-256 digest of the exact rendered question text**, where SHA-256 is the 256-bit Secure Hash Algorithm. Reword the message between rounds, or change the schema, and the digest changes, the recorded answer no longer matches, and the server asks again. Put a timestamp, a live memory reading, or a "3 processes match" count in the message and every recorded answer looks stale forever. **The call never converges.** It is not an exception and not a loop you can see; the tool simply keeps returning `input_required` until something upstream gives up.

The resolver in section 6 is written the safe way, and the behavior is measured. From [verify/RESULTS.md](../../verify/RESULTS.md):

```
- asked 1 time(s)
- text: `Terminate process 4242 (unknown)? This cannot be undone.`
```

The process id and the process name both come from the tool's own arguments, so the string is identical on every round. Note also that resolver *bodies* may run again on each round; the recorded answer is consulted only at the point the body asks. Keep resolvers cheap and free of side effects.

**Ask everything at once.** `inputRequests` is a map for a reason. Two questions in one result cost one round trip; two rounds of one question cost two, and give the user two dialogs.

**Say what will happen, in the message.** `"Terminate process 4242 (unknown)? This cannot be undone."` names the target and the consequence. "Are you sure?" names neither. The message is the only text the user reads.

**Do not ask for what you can look up.** Every elicitation is a round trip and a dialog. If the answer is in the arguments, in the token, or in your database, take it from there.

**Assume no answer ever comes.** Server requirement 8 says you must not assume a retry. Because the original request already terminated with a result, there is nothing hanging on the wire and nothing to clean up; if you stored something, let a time to live (TTL) expire it. And this is worth saying plainly: a user who is asked to confirm something they did not initiate will click through it. Elicitation is a consent mechanism only when the question is rare, specific, and expected.

---

## Common pitfalls

- **Letting the question text vary between rounds.** A timestamp, a live reading, or a changing count in the elicitation message changes its SHA-256 digest, so the recorded answer never matches and the tool call never converges. Build the message from the tool's arguments and nothing else.
- **Shipping the SDK's default `requestState` key to production.** It is a process-local ephemeral key. It works on `stdio` and on one worker, and it silently breaks the moment you add a second worker or restart during an outstanding round trip. Pass `RequestStateSecurity(keys=[...])` with at least 32 bytes per key.
- **Keeping the half-finished call in memory.** The original request is answered and closed the instant you return `input_required`. There is no future to await, no dictionary entry worth keeping, and no guarantee the retry reaches the same process.
- **Treating `action: "accept"` as approval.** Accept means the form came back. In form mode the answer inside it may still be no; in URL mode it means only that the user consented to open the URL, not that the out-of-band work finished.
- **Reusing the JSON-RPC `id` on the retry.** It **must** differ. The two requests are independent, and a client that reuses the id is not conformant.
- **Putting `inputResponses` or `requestState` in `_meta`.** They are siblings of `name` and `arguments` inside `params`. This one is easy to get wrong by analogy, since almost everything else new in this revision lives in `_meta`.
- **Asking for a secret in form mode.** Passwords, API keys, tokens, and payment credentials are a **must not**, because the answer would pass through the client. Use URL mode.

---

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The four steps, the three types, and the eight server and four client requirements quoted in section 3. <https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr>
- Specification, *"Elicitation"*, revision 2026-07-28. Capability declaration, the schema restrictions in section 7, the three actions, URL mode, and the phishing and statefulness requirements in section 9. <https://modelcontextprotocol.io/specification/draft/client/elicitation>
- SEP-2322, *"Multi Round-Trip Requests"* (2026), status Final. The design rationale and the worked example section 5 quotes on re-sent arguments. <https://modelcontextprotocol.io/seps/2322-MRTR>
- Specification, *"Base protocol"* § Responses, revision 2026-07-28. `resultType`, and the rule that an absent one means `"complete"`.
- Specification, *"Caching"*, revision 2026-07-28. Why interim results carry no caching hints and MRTR retries are never cacheable.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 09 — Tasks: work that outlives a single request](../09-tasks/index.md)**: the other thing a server can return instead of a finished result, and the distinction between waiting for a person and waiting for a long job.
- **[Post 14 — Project 1 · Writes, transactions, and an audit trail](../14-database-writes/index.md)**: this loop doing real work, gating every database mutation behind a preview a human has to approve.
