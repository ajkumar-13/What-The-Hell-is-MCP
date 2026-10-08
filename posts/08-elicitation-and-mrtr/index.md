# 08 - Elicitation and MRTR: asking the user mid-call

> **TL;DR.** A Model Context Protocol (MCP) server can still stop in the middle of a tool call to ask the user a question, but since revision 2026-07-28 it does so by *returning* an `input_required` result instead of *calling back* down the connection, and the client calls the same tool again with the original arguments, the answer, and an opaque `requestState` the server minted. That inversion is called Multi Round-Trip Requests (MRTR), and it is what lets the retry land on a different worker than the original call and still succeed. This post walks the loop message by message on the wire, then builds a tool that refuses to act until a human confirms, with an approval the model can neither see nor forge. Every frame, schema, error and outcome below is printed by a script in `snippets/` against `mcp==2.3.0`.
>
> **Prerequisites:** [Post 06](../06-tools-in-depth/index.md).
> **Safe to skip?** Skip it if the reader can already trace an `input_required` result and its retry field by field, gate a destructive tool behind a parameter the software development kit (SDK) resolves by elicitation, and say what a `requestState` key policy must look like behind a load balancer.
>
> **After reading, you will be able to:**
>
> - Trace a Multi Round-Trip Request exchange message by message and say where the state lives at each step.
> - Build a tool whose approval parameter the model can neither see nor forge.
> - Handle accept, decline, and cancel as three separate outcomes rather than one.
> - Protect requestState correctly on a server that runs more than one worker.

![A sequence diagram with three lifelines, user, host and client, and server. Five numbered steps run down the page: a tools/call, an input_required result carrying inputRequests and requestState, an off-the-wire exchange between the host and the user, a second tools/call carrying inputResponses and the same requestState, and a complete result.](diagrams/01-mrtr-loop.svg)

*The server never sends a request. It answers one, and the client comes back with a new one.*

---

## 1. The question: a tool that cannot finish without a human

Here is a tool that should not ship as written. It is registered on a throwaway server in `snippets/naive_tools.py`, which prints what it publishes and never calls it:

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
    psutil.Process(pid).send_signal(signal.SIGTERM)
    return f"Sent SIGTERM to {pid}."
```

Now `approved` is in the published input schema, which is the description the model reads before it fills in the arguments. The same script prints it:

```
properties: {"pid": {"title": "Pid", "type": "integer"}, "approved": {"title": "Approved", "type": "boolean"}}
required:   ["pid", "approved"]
```

That is a required field whose only purpose is to be set to `true` by the very thing it was meant to gate. Annotations do not help either: `destructiveHint` is a hint, the Tools page says a client must treat annotations as untrusted unless they come from a trusted server, and [post 06](../06-tools-in-depth/index.md) is blunt about hints not being enforcement.

What the tool actually needs is to get a question in front of a human and not act until the answer comes back. MCP calls that **elicitation**, and it is the only client feature in this revision that is not deprecated. How a server performs it changed completely in 2026-07-28, and the change is worth understanding for its own sake, because it is the clearest illustration of what statelessness did to the protocol. So the question for this post is: **how does a server ask the user a question in the middle of a tool call when there is no channel left to call back on?**

## 2. How this used to work, and why the back channel had to go

Through revision 2025-11-25 the mechanism was the obvious one. JavaScript Object Notation Remote Procedure Call (JSON-RPC) 2.0 is symmetric by design: either end may send a request. So a tool handler, partway through its work, sent one of its own back down the connection, a request with its own `id` and the method `elicitation/create`, traveling from server to client.

It then awaited the client's response, exactly as if it had made a network call, and carried on. Nothing in JSON-RPC forbids this. MCP allowed it, and three client features used it, through the methods `elicitation/create`, `sampling/createMessage`, and `roots/list`.

Three things had to be true for that to work. The connection had to be bidirectional and still open. The original `tools/call` had to remain in flight, unanswered, for as long as the user took to read the dialog. And the local variables of that half-finished handler had to stay in the memory of one specific process, because that process was the only one that could resume.

The last of those is the one that killed it. A request that must be finished by the same process that started it cannot be load balanced, cannot survive a deploy, and cannot be served by a stateless Hypertext Transfer Protocol (HTTP) worker. [Post 03](../03-wire-protocol/index.md) covered the sessionless model this revision adopted; the back channel was the last thing standing in its way.

So it was removed. The specification's Patterns overview page now says, without qualification:

> Servers **MUST NOT** initiate JSON-RPC requests, and clients do not send JSON-RPC responses.

And the MRTR page states the migration:

> Servers **MUST** send server-to-client requests (such as `roots/list`, `sampling/createMessage`, or `elicitation/create`) using the MRTR pattern. The previous pattern of server-initiated requests is no longer supported. **This is a breaking change.**

![The old model on the left, where a server sends an elicitation/create request back down the connection and blocks waiting for a response, marked as removed; the new model on the right, where the server answers with an input_required result and the client returns with a second, independent tool call.](diagrams/02-back-channel-vs-return.svg)

*Left, the server asks and waits. Right, the server answers and stops. Only the right-hand column exists in 2026-07-28.*

Sampling and roots did not survive this transition as features worth teaching: both are deprecated as of 2026-07-28, and [post 18](../18-server-side-models/index.md) covers what replaced sampling. Elicitation did survive, in a new shape. Older Python code that tries the old move meets an exception from the SDK. In `snippets/resolve_rules.py` a tool calls `ctx.elicit()` directly in its body, on a request at revision 2026-07-28, and the SDK refuses before anything reaches the wire:

```
ctx.elicit() called in the tool body: NoBackChannelError -32600: Cannot send 'elicitation/create': this transport context has no back-channel for server-initiated requests.
```

`NoBackChannelError` lives in `mcp.shared.exceptions`, and the client receives it as a JSON-RPC error with code `-32600` (`Invalid Request`). There is no path back.

## 3. MRTR: return `input_required`, get retried

The replacement is four steps. Quoting the MRTR page:

1. Client sends an initial request to the server with the parameters needed to perform the operation.
2. Server determines that additional information is required to fulfill the request and responds requesting more information.
3. Client gathers the requested information from the user or other sources, then retries the original request including the additional requested information.
4. Server determines it has sufficient information to complete the operation, and responds with the final result.

Step 2 is the whole idea. The server does not ask; it *answers*, and its answer happens to be "not yet, and here is what I need." The original request is then **finished**. It got a JSON-RPC result, it is closed, and the server holds nothing.

The tag that marks such an answer is `resultType`, which [post 03](../03-wire-protocol/index.md) introduced. Every result in this revision carries one, and the core protocol defines two values, `"complete"` and `"input_required"`. The schema gives the interim result two optional fields:

```typescript
export interface InputRequiredResult extends Result {
  inputRequests?: InputRequests;
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
3. It **may** include `requestState`, encoded however it likes: base64-encoded JSON, an encrypted JSON Web Token (JWT), serialized binary. The protocol never looks inside.
4. It **must** treat `requestState` as attacker-controlled input. If that state influences authorization, resource access, or business logic, the server **must** protect its integrity and **must** reject state that fails verification. The protection may be omitted only when tampering can cause nothing worse than the request failing.
5. To prevent replay it **should** bind three things inside the protected payload and check each on receipt: the authenticated principal, a short expiry, and an identifier for the originating request such as the method name plus a digest of its salient parameters.
6. Every `InputRequiredResult` **must** carry at least one of `inputRequests` or `requestState`.
7. A server **must not** send an `inputRequests` entry for a capability the client did not declare.
8. A server **must not** assume the client will fulfill the requests or retry at all, and it **may** return `input_required` on repeated attempts at the same request.

**What a client must do.** Four requirements, and the third is the one that surprises people.

1. If the result carries `inputRequests`, the client **must** construct those inputs before retrying. If it carries only `requestState`, the client **may** retry immediately.
2. If the result carries `requestState`, the client **must** echo the exact value back and **must not** inspect, parse, or modify it. If there was no `requestState`, the client **must not** invent one.
3. **The JSON-RPC `id` on the retry must differ from the original**, because they are two independent requests.
4. Both fields affect only the retry of that one request. They **must not** be attached to anything else the client has in flight.

## 4. The complete message loop

Here is the whole thing on the wire, against the project's own server rather than an illustration. `snippets/mrtr_wire.py` starts [code/05-first-server/](../../code/05-first-server/) over Streamable HTTP on a free loopback port and talks to it with nothing but `httpx`, composing every message by hand. The process id is `999999`, which does not exist, so nothing is ever terminated.

**Message 1, client to server.** The model picked the tool and supplied the one argument it can see.

```http
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: terminate_process
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "terminate_process",
    "arguments": {
      "pid": 999999
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mrtr-by-hand",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {
        "elicitation": {
          "form": {}
        }
      }
    }
  }
}
```

The client declared `elicitation` with form mode. That declaration is what makes everything below legal, because server requirement 7 forbids an `inputRequests` entry for a capability the client did not declare. Section 12 shows what the server answers when the declaration is missing.

**Message 2, server to client.** HTTP `200`. The server has the process id but not the user's consent, so it answers with a question. The script shortens `requestState` for printing; the real value is a sealed token of about 400 characters that starts with `v1.` and is different on every run.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "inputRequests": {
      "system_info.interactive:_confirm_terminate": {
        "method": "elicitation/create",
        "params": {
          "message": "Terminate process 999999 (unknown)? This cannot be undone.",
          "mode": "form",
          "requestedSchema": {
            "properties": {
              "confirm": {
                "description": "Confirm that this process should be terminated.",
                "title": "Confirm",
                "type": "boolean"
              },
              "reason": {
                "default": "",
                "description": "Optional note recorded in the server log.",
                "title": "Reason",
                "type": "string"
              }
            },
            "required": [
              "confirm"
            ],
            "type": "object"
          }
        }
      }
    },
    "requestState": "v1.<sealed, shortened here>",
    "resultType": "input_required",
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "system-info",
        "version": ""
      }
    }
  }
}
```

Read that carefully, because four things in it are the post. `resultType` is `"input_required"`, so this is a result and not a request. Request `1` is now complete and closed, and the server retains nothing about it. `system_info.interactive:_confirm_terminate` is a key the server made up; section 6 shows where the SDK gets it. And the `elicitation/create` object has a `method` and `params` but no `jsonrpc` and no `id`, because it is a bare request object living inside a map, not a message. The SDK also writes `"mode": "form"` explicitly, which the specification allows a form-mode request to omit, and `serverInfo` rides in the result's `_meta` with an empty `version` because the project never sets one.

There is no `ttlMs` and no `cacheScope` either (the script checks for both and prints `absent`). The Caching page says interim results are not cacheable and carry no caching hints.

**Off the wire.** The host shows which server is asking, renders a checkbox and a text field from `requestedSchema`, and lets the user review, edit, decline, or dismiss. The user checks the box and submits. No MCP message is involved in any of this.

**Message 3, client to server.** The retry.

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "terminate_process",
    "arguments": {
      "pid": 999999
    },
    "inputResponses": {
      "system_info.interactive:_confirm_terminate": {
        "action": "accept",
        "content": {
          "confirm": true
        }
      }
    },
    "requestState": "v1.<sealed, shortened here>",
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {
        "name": "mrtr-by-hand",
        "version": "1.0.0"
      },
      "io.modelcontextprotocol/clientCapabilities": {
        "elicitation": {
          "form": {}
        }
      }
    }
  }
}
```

The `id` is `2`, not `1`. The method, the tool name, and all the original arguments are re-sent verbatim. `inputResponses` is keyed by the same string the server assigned, and its value is a bare `ElicitResult`. `requestState` goes back byte for byte; the client did not parse it and was forbidden to. The `_meta` keys are present again, because capabilities are declared per request and the specification says servers must not infer them from prior requests.

**Message 4, server to client.** HTTP `200`. The server verified the seal on `requestState`, checked what it binds (section 5), restored the answer, ran the tool body, and the body reached the process lookup.

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "text": "No process with pid 999999 is running.",
        "type": "text"
      }
    ],
    "isError": false,
    "resultType": "complete",
    "structuredContent": {
      "result": "No process with pid 999999 is running."
    },
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "system-info",
        "version": ""
      }
    }
  }
}
```

Two HTTP request and response pairs. Two independent JSON-RPC exchanges. Zero server-initiated requests, and zero session state. Nothing in message 3 depends on which process answered message 1, which is the property section 5 puts to the test with more than one worker.

## 5. Carrying state across round trips without a session

Message 3 had to stand on its own. If the server had kept the half-finished tool call in a dictionary keyed by request id, a retry arriving at a second worker would have found an empty dictionary and the call would have failed. **State cannot live in memory.** Not in a module-level dict, not in an `asyncio` future, not in a `contextvar`.

Exactly two things survive a round trip.

**The client re-sends the original parameters.** The retry is a complete request: same method, same tool name, same arguments. For many tools that is enough on its own, and SEP-2322, the Specification Enhancement Proposal that introduced MRTR, says so in its own worked example: "No `requestState` is needed yet, since the original tool call arguments will be re-sent on retry."

**`requestState` carries whatever the arguments do not.** Anything the server computed, looked up, or already gathered in an earlier round goes here. When a call takes three rounds, the state typically grows each time to accumulate what has been answered so far. The SDK uses it exactly that way: it records each answered question in the state so that no question is asked twice.

Because it travels through the client, `requestState` is attacker-controlled input by definition. The specification is direct about the threat:

> Because `requestState` passes through the client, malicious or compromised clients could attempt to modify it to alter server behavior, bypass authorization checks, or corrupt server logic.

Hence server requirement 4. The example value the MRTR page prints is literally the string `"AEAD-protected blob"`, which is the specification signaling the expected construction rather than a format. AEAD is authenticated encryption with associated data: the payload is both encrypted and tamper-evident, so a modified blob fails verification instead of decrypting into something useful.

**What the Python SDK does, and where its default stops working.** `MCPServer` seals `requestState` itself. Tool code never touches the token; the SDK's own state is sealed and unsealed at the boundary, with Advanced Encryption Standard encryption with a 256-bit key in Galois/Counter Mode (AES-256-GCM), with an expiry, the method, the tool name, a digest of the arguments, and the server's name sealed inside, plus the authenticated principal when the transport has one. By default the key is a **process-local ephemeral key**, generated at startup.

That default is correct for `stdio` and for a single-process server, and it is wrong for anything else. Two workers behind a load balancer have two different keys, and a restarted worker has a new one. For a real deployment, every worker is given the same key. `snippets/two_workers.py` builds each worker of a small `fleet` service with this function:

```python
    security = None if key == "ephemeral" else RequestStateSecurity(keys=[bytes.fromhex(key)])
    mcp = MCPServer("fleet", request_state_security=security)
```

It starts three workers on loopback ports, A and B with one shared 32-byte key and C with the default, asks A, and sends the retry elsewhere:

```
tools/call on A          -> HTTP 200, input_required, key '__main__:ask'
retry on B (shared key)  -> HTTP 200, complete: 'Restart of billing approved.'
retry on C (its own key) -> HTTP 400, error -32602 'Invalid or expired requestState'
same retry replayed on A -> HTTP 200, complete: 'Restart of billing approved.'
```

The retry that lands on B succeeds although B never saw the question. The one that lands on C fails, and C is exactly what every worker looks like with the default key, or after a restart. The error is deliberately uninformative: `-32602` (`Invalid params`) with the message `Invalid or expired requestState` and `data` of `{"reason": "invalid_request_state"}`, whatever the cause. The real reason goes only to the server's log.

Two constructors check the policy, `RequestStateSecurity` the key and `MCPServer` the name, and the same script prints both after the default lifetime:

```
default lifetime of sealed state: 600.0 seconds
a 16-byte key: ValueError: request-state keys must be at least 32 bytes of secret randomness; keys[0] is 16 bytes. Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
a policy on a server with no name: ValueError: request_state_security is configured but this server has no name. Sealed requestState carries the server name as an audience claim, so state minted by another service that shares the same keys is rejected; unnamed servers would all stamp the same placeholder and the check would mean nothing. Name the server (MCPServer("my-service", ...)) or set RequestStateSecurity(audience=...).
```

Keys are at least 32 bytes each, and `keys` is a list so that keys can rotate: the first key seals and every key in the list unseals. A server given a key policy must have a name, or an explicit `audience`, because the name is sealed into the state as an audience claim. Sealed state lives for ten minutes unless `ttl` says otherwise.

Two further notes. Replay defenses bound the window; they do not make state single-use. The last line of the run above replays the same retry on A and it succeeds again. If a given `requestState` must be redeemable at most once, a one-time payment for instance, the server has to enforce that itself, server-side, as the MRTR page requires. And the Caching page says results produced by an MRTR retry **must not** be cached at all, because they depend on inputs that are not part of any cache key.

Nothing here forbids a stateful server. Section 9 describes a flow that requires one. `requestState` then degenerates into a lookup key, and the specification suggests exactly that: "Servers needing to correlate an elicitation across retries encode their own identifier in `requestState`."

## 6. Writing it: `Resolve` and `Elicit`

None of the above appears in the tool body. The high-level SDK expresses an elicitation as a **parameter the model cannot see**, filled in by a resolver function. The complete file is [code/05-first-server/src/system_info/interactive.py](../../code/05-first-server/src/system_info/interactive.py), and the blocks below are excerpts of it.

First, the shape of the question, as an ordinary Pydantic model:

```python
class TerminateConfirmation(BaseModel):
    """What we ask the human before ending a process."""

    confirm: bool = Field(
        description="Confirm that this process should be terminated.",
    )
    reason: str = Field(
        default="",
        description="Optional note recorded in the server log.",
    )
```

Then a resolver, which returns an `Elicit(...)` rather than performing one:

```python
def _confirm_terminate(pid: int) -> Elicit[TerminateConfirmation]:
    """Build the confirmation question for a specific pid.

    The question is derived from the tool's arguments and a lookup keyed by
    them. That is not a style preference. The SDK matches a recorded answer
    against a digest of the exact rendered question, so if this string changed
    between rounds, for example because it embedded a timestamp or a live
    memory reading, the recorded answer would never match and the call would
    never converge (the SDK client gives up after ten rounds).
    """
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
@mcp.tool(
    title="Terminate a process",
    annotations=ToolAnnotations(
        title="Terminate a process",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def terminate_process(
    pid: int,
    approval: Annotated[
        ElicitationResult[TerminateConfirmation],
        Resolve(_confirm_terminate),
    ],
) -> str:
    """Terminate a running process, after the user confirms.

    Args:
        pid: The process id to terminate.
    """
    # `approval` is invisible to the model. The published input schema for this
    # tool contains `pid` and nothing else, so the model cannot fabricate an
    # approval by passing one as an argument. That is the point.
    #
    # Annotating as ElicitationResult[...] rather than the bare model means we
    # get the full three-way outcome to branch on. Annotating it as the bare
    # TerminateConfirmation would instead raise a ToolError on decline, which is
    # fine when there is nothing useful to say about a refusal.
    if isinstance(approval, DeclinedElicitation):
        return f"Not terminated. The user declined to confirm ending process {pid}."
    if isinstance(approval, CancelledElicitation):
        return f"Not terminated. The confirmation was dismissed for process {pid}."

    assert isinstance(approval, AcceptedElicitation)
    if not approval.data.confirm:
        return f"Not terminated. The user answered no for process {pid}."
```

**The security property is measured, not asserted.** The `approval` parameter is stripped from the published input schema. `snippets/published_schema.py` imports the project, connects an in-memory client, and prints the function's own parameters next to what `tools/list` publishes:

```
Python parameters of terminate_process: ['pid', 'approval']
published properties:                   ['pid']
published required:                     ['pid']
```

`approval` is absent. A model reading `tools/list` sees a tool that takes a process id and nothing else, so it has no field in which to fabricate its own approval. That is the difference between this and the `approved: bool` parameter from section 1, and it is the reason to reach for `Resolve` rather than rolling a flag by hand. The project's own suite asserts the same thing in `test_resolved_parameter_is_hidden_from_the_model`.

**Transport selection is automatic and revision-gated.** The SDK reads the revision the client declared in `_meta` and branches on it; its source pins the switch as `_INPUT_REQUIRED_VERSION = "2026-07-28"`. At 2026-07-28 or later the questions are batched into an `InputRequiredResult`, as section 4 showed. For a client at 2025-11-25 or earlier the same tool sends the old server-to-client request instead. The tool is written once and serves both eras; this post exercises only the first.

**The registration rules**, all enforced when the decorator runs, all worth knowing before fighting one. `snippets/resolve_rules.py` registers a deliberately wrong tool for each refusal and calls a correct one for each behavior.

- `Resolve` is honored **on tools only**. On a prompt the parameter is published as an ordinary prompt argument; section 12 shows what that costs.
- Only a top-level `Annotated[T, Resolve(f)]` counts. `Annotated[T, Resolve(f)] | None` raises `InvalidSignature`.
- A resolver's parameters must each be a `Context`, another `Resolve(...)`, or a tool argument matched **by name**. Anything else raises `InvalidSignature` at registration.
- Resolver cycles raise `InvalidSignature`.
- A resolver's return annotation may carry **at most one** `Elicit`, `Sample`, or `ListRoots` arm.
- **`Resolve(...)` combined with a declared `InputRequiredResult` return type on the same tool is rejected at registration.** There is one `requestState` channel per call, and the multi-round flow is driven either by hand or by `Resolve`, not both.
- Within one round, each resolver runs once, keyed by resolver identity, so two parameters backed by the same function ask one question.
- Synchronous resolvers run on a worker thread.
- The key that appears in `inputRequests` on the wire is derived from the resolver's `module:qualname`, which is stable across workers. That is what makes the whole thing work on stateless HTTP.

Two of the printed refusals show the style of all five, each naming the rule it enforces:

```
with_input_required: InvalidSignature: Tool 'with_input_required' combines Resolve(...) parameters with an InputRequiredResult return; a call has one input_required channel, so the multi-round flow is driven either by resolvers or by the tool body, not both
unknown_parameter: InvalidSignature: Resolver 'needs_user' parameter 'user' cannot be resolved: expected a Context, an Annotated[_, Resolve(...)], or a tool argument by name
```

The two call-time behaviors print as:

```
one resolver behind two parameters: a=accept b=accept; questions shown: 1
thread the plain resolver ran on: 'AnyIO worker thread' (the call itself: 'MainThread')
```

## 7. Schema rules for what can be asked

Form mode schemas are deliberately poor, because every client has to render them:

> To simplify client user experience, form mode elicitation schemas are limited to **flat objects with primitive properties only**.

Four property types are allowed, and that is the whole list.

| Type | Constraints a server may set |
|---|---|
| `string` | `minLength`, `maxLength`, `format`, `default` |
| `number` or `integer` | `minimum`, `maximum`, `default` |
| `boolean` | `default` |
| enum | single-select or multi-select, four shapes |

The permitted string formats are exactly `email`, `uri`, `date`, and `date-time`. Enums come as a bare `enum` array, as `oneOf` with `const` and `title` when display labels are wanted, and in two array-typed multi-select forms with `minItems` and `maxItems`. An older `enumNames` shape still exists in the schema and is documented for removal, so new code should not be written against it.

Everything else is out: no nested objects, no arrays of objects beyond enum multi-selects, no `$ref`, no conditionals. `ElicitResult.content` values are typed `string | number | boolean | string[]`, which is the same restriction seen from the other side. Every primitive supports `default`, and clients that support defaults **should** pre-populate the field. The `requestedSchema` in section 4 is what the SDK renders from `TerminateConfirmation`: one boolean, one string with a `default` of `""`, both flat.

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

Decline and cancel are not errors and there is no separate channel for them. They travel back to the server inside `inputResponses` on the retry, exactly like an accept, and the tool sees them as a value to branch on.

![Three columns, one per action, answering the same four questions down the page: what the user did, which SDK class arrives, whether it carries data, and what the terminate_process tool does. Only accept carries data, and even it acts only when the confirm field is true. A footer notes that none of the three is an error, that all three ride back inside inputResponses on the retry, and that an accept in URL mode means consent rather than completion.](diagrams/03-three-outcomes.svg)

*Three outcomes, three code paths. Collapsing decline and cancel into "no" throws away the difference between a refusal and a closed window.*

`snippets/four_outcomes.py` drives the project's tool through an in-memory client whose elicitation callback plays the user, once per answer; the client performs the MRTR retry itself:

```
accept {"confirm": true}     isError=false  No process with pid 999999 is running.
accept {"confirm": false}    isError=false  Not terminated. The user answered no for process 999999.
decline                      isError=false  Not terminated. The user declined to confirm ending process 999999.
cancel                       isError=false  Not terminated. The confirmation was dismissed for process 999999.
```

The second row is the one worth pausing on. `action: "accept"` means the user submitted the form, not that the user said yes. A confirmation checkbox left unchecked arrives as an accept with `confirm: false`, and a tool that treats "accepted" as "approved" will terminate a process whose owner explicitly declined to confirm it.

How the outcome arrives is the tool author's choice. Annotating the parameter as `Annotated[ElicitationResult[T], Resolve(f)]` gives the full three-way union to branch on, as above. Annotating it as the bare model, `Annotated[TerminateConfirmation, Resolve(f)]`, unwraps to the data on accept and raises `ToolError` on decline or cancel, which is fine when there is nothing useful to say about a refusal. `snippets/resolve_rules.py` shows what the model then receives:

```
bare-model annotation, user declines: isError=True: Error executing tool bare: Resolver for parameter 'approval' could not resolve: elicitation was decline
```

**One honest hedge.** The specification says what a server should *do* with each action: process the data, handle the decline by offering alternatives, handle the cancel by perhaps asking again later. It does not say what the server must *return*. A `"complete"` result with `isError: true`, a `"complete"` result describing the abandonment without `isError`, and another `InputRequiredResult` re-asking are all permitted by the text, since server requirement 8 explicitly allows repeated prompting. Pick one and be consistent. The project's tool returns a plain `"complete"` result with a sentence the model can relay, on the grounds that a user declining is not a failure of the tool.

## 9. URL mode, for authorization flows and payments

Form mode routes the answer through the client, which is precisely what is not wanted for a credential. URL mode exists for that case:

> URL mode elicitation enables servers to direct users to external URLs for out-of-band interactions that must not pass through the MCP client. This is essential for auth flows, payment processing, and other sensitive or secure operations.

The request carries three fields, and unlike form mode the `mode` is required with no default. As the Elicitation page prints it:

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

The user agreed to open the URL. Whether they finished whatever was on the other side happens out of band, and the client is never told. So on the retry the server determines from the echoed `requestState` or its own records whether the out-of-band work completed, and either returns the final result or answers `input_required` **again**, which is why server requirement 8 exists. Clients **should** give the user manual controls to retry or cancel the original request in the meantime.

This is the flow that forces a stateful server. The specification says so directly: the MCP server is responsible for storing and managing the third-party tokens obtained through URL mode elicitation, in other words the server must be stateful. That is not a contradiction of section 5; the protocol still carries no state, and the server's database is its own business.

Four constraints are worth carrying away.

**The cross-user phishing attack is real and named.** URL mode hands out a URL, and a URL can be forwarded. Alice triggers an elicitation, the server mints an authorization URL, Alice tricks Bob into opening it, Bob authorizes, and the server binds Bob's third-party tokens to Alice's account. The server **must** ensure the user who started the elicitation is the user who completes the flow. The recommended pattern is to point the elicitation at a route on the server's own domain, check that the browser session's subject matches the subject the MCP authorization layer supplied, and only then redirect onward.

**Never mint a pre-authenticated URL**, and never put credentials or personal data in the URL. A malicious client could use it to impersonate the user.

**Third-party credentials must not transit the client**, the server must not reuse the client's credentials against the third-party service, and the server must not use URL mode elicitation to authorize users for *itself*. That is what OAuth, the Open Authorization framework, is for, and [post 20](../20-authorization/index.md) covers it.

**Three things remembered from 2025-11-25 are gone.** The `elicitationId` field and the `notifications/elicitation/complete` notification were both removed, because under MRTR the client learns the outcome by retrying rather than by being told. So is the error code `-32042` (`URL_ELICITATION_REQUIRED`), which existed in 2025-11-25 only and meant "use URL mode for this". It is retired: the code stays reserved and will never be reused, a client may still meet it coming from a server implementing an older revision, and an implementation of 2026-07-28 **must not** emit it. There is nothing to emit it *for* any more, because the elicitation request itself now names its own mode.

**A note on the SDK.** `mcp==2.3.0` still ships the old mechanism: `UrlElicitationRequiredError` in `mcp.shared.exceptions`, and a tool that raises it answers a 2026-07-28 request with the retired code. `snippets/resolve_rules.py` prints:

```
a tool raising UrlElicitationRequiredError: UrlElicitationRequiredError -32042: URL elicitation required
```

That is the SDK lagging the specification, not a second way to do URL mode. A 2026-07-28 server asks for URL mode by returning an `elicitation/create` entry with `"mode": "url"` in `inputRequests`, as above.

## 10. Designing questions a user can answer

The protocol will let a server ask anything. Most of the craft is in not doing that.

**Derive the question from the arguments, deterministically.** This is the pitfall that costs the most debugging time, so it gets its own paragraph. The SDK asks each question once per call, and it recognizes an already-answered question by comparing a **SHA-256 digest of the rendered question**, its message and its schema, against the digest recorded when it was asked; SHA-256 is the 256-bit Secure Hash Algorithm. Reword the message between rounds, or change the schema, and the digest changes, the recorded answer no longer matches, and the server asks again. Put a timestamp, a live memory reading, or a "3 processes match" count in the message and every answer looks stale. **The call never converges.** `snippets/drifting_question.py` gives two otherwise identical tools to a client whose user always answers yes; one message is built from the arguments, the other carries a counter that moves on every round:

```
== steady_tool ==
dialogs shown: 1; first two: ['Terminate process 4242? This cannot be undone.']
answers the server discarded: 0
ended: finished: accept

== drifting_tool ==
dialogs shown: 10; first two: ['Terminate process 4242? (reading 1)', 'Terminate process 4242? (reading 2)']
answers the server discarded: 10
server log line: Discarding the answer for resolver '__main__:drifting': the question changed since it was asked
ended: InputRequiredRoundsExceededError: Server returned InputRequiredResult for more than 10 rounds; raise input_required_max_rounds on the Client, or use client.session.<method>(..., allow_input_required=True) to drive the loop manually.
```

The server never fails: it keeps returning `input_required` and logs one line at `INFO` per discarded answer. The user sees ten dialogs. The call ends only because the SDK's client stops after ten rounds, its default `input_required_max_rounds`; a client without such a cap would ask forever.

The project's resolver in section 6 is written the safe way. `snippets/four_outcomes.py` records every question its user is shown for one call:

```
asked 1 time(s)
text: Terminate process 999999 (unknown)? This cannot be undone.
```

The process id comes from the tool's arguments and the name is looked up from it, so the string is identical on every round as long as the process does not change. Note also that resolver *bodies* may run again on each round; the counter in the drifting tool moved because its resolver ran every time, and the recorded answer is consulted only at the point the body asks. Keep resolvers cheap and free of side effects.

**Ask everything at once.** `inputRequests` is a map for a reason. Two questions in one result cost one round trip; two rounds of one question cost two, and give the user two dialogs.

**Say what will happen, in the message.** `"Terminate process 999999 (unknown)? This cannot be undone."` names the target and the consequence. "Are you sure?" names neither. The message is the only text the user reads.

**Do not ask for what can be looked up.** Every elicitation is a round trip and a dialog. If the answer is in the arguments, in the token, or in the server's database, take it from there.

**Assume no answer ever comes.** Server requirement 8 says a server must not assume a retry. Because the original request already terminated with a result, there is nothing hanging on the wire and nothing to clean up; if the server stored something, a time to live (TTL) should expire it. And this is worth saying plainly: a user who is asked to confirm something they did not initiate will click through it. Elicitation is a consent mechanism only when the question is rare, specific, and expected.

## 11. Make it run: the project and the scripts behind this post

The project is the post's code. From inside `code/05-first-server/`:

```bash
uv sync --extra dev                         # once: the environment, with the test tools
uv run python -m system_info                # stdio, what a desktop host spawns; Ctrl+C stops it
uv run python -m system_info --http         # Streamable HTTP on 127.0.0.1:8000
uv run --extra dev pytest                   # 19 passed, seven of them on terminate_process
```

Seven scripts under `snippets/` produce every schema, frame, error and outcome quoted above. Each runs from the series root with no arguments, in a few seconds, through the root environment that pins `mcp[cli]==2.3.0`, `psutil` and `httpx<1`. Two import the project under `code/05-first-server/` in memory, one starts it as a subprocess, and every one stops whatever it started before it exits; none needs a host or any network beyond 127.0.0.1, and none terminates a real process.

```bash
uv run python posts/08-elicitation-and-mrtr/snippets/naive_tools.py        # section 1: what an approval flag publishes
uv run python posts/08-elicitation-and-mrtr/snippets/mrtr_wire.py          # sections 4 and 12: the loop on the wire, three refused requests
uv run python posts/08-elicitation-and-mrtr/snippets/two_workers.py        # section 5: shared and ephemeral keys, three workers
uv run python posts/08-elicitation-and-mrtr/snippets/published_schema.py   # section 6: approval is not in the schema
uv run python posts/08-elicitation-and-mrtr/snippets/resolve_rules.py      # sections 2, 6, 9 and 12: what Resolve refuses and allows
uv run python posts/08-elicitation-and-mrtr/snippets/four_outcomes.py      # sections 8 and 10: four answers, one question
uv run python posts/08-elicitation-and-mrtr/snippets/drifting_question.py  # section 10: a question that never converges
```

`two_workers.py` is both halves of its experiment: with no argument it is the client, and it starts itself three times with `--serve --port N --key K` as the workers. That flag also runs one worker by hand:

```bash
uv run python posts/08-elicitation-and-mrtr/snippets/two_workers.py --serve --port 8000 --key ephemeral
```

To drive accept, decline, and cancel against a real host instead, connect the project to a desktop host as [post 05](../05-first-server/index.md) does and ask it to terminate a process id that does not exist; the host renders the confirmation and the tool's answer comes back as one of the four rows in section 8.

## 12. What can go wrong?

Three mistakes reach the wire, and one never gets that far. `snippets/mrtr_wire.py` and `snippets/resolve_rules.py` print each of them.

**A retry that is not the same request.** A client, or a hand-written test, that changes the arguments on the retry, or touches a single character of `requestState`, gets the frozen refusal, HTTP `400`:

```
--- arguments changed to pid 4242: HTTP 400
{"code": -32602, "message": "Invalid or expired requestState", "data": {"reason": "invalid_request_state"}}
--- one character of requestState changed: HTTP 400
{"code": -32602, "message": "Invalid or expired requestState", "data": {"reason": "invalid_request_state"}}
```

The two responses are identical on purpose; only the server's log tells them apart:

```
  mcp.server.request_state: requestState rejected on tools/call: request binding
  mcp.server.request_state: requestState rejected on tools/call: seal
```

The first reason, `request binding`, is the SDK's implementation of server requirement 5: the state is bound to the tool name and a digest of the arguments, so it cannot be spent on a different process id. The same refusal is what section 5's worker C returned, and the log is the place to look for which check failed.

**A client that never declared elicitation.** The resolver refuses to ask, because requirement 7 forbids it, and the call fails with `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`), HTTP `400`, before the tool body runs:

```
--- first call with no elicitation capability: HTTP 400
{"code": -32021, "message": "Client did not declare the form elicitation capability required by resolver 'system_info.interactive:_confirm_terminate'", "data": {"requiredCapabilities": {"elicitation": {"form": {}}}}}
```

The `requiredCapabilities` payload names what was missing, and the resolver's `module:qualname` is in the message, which is the fastest way to find the one that asked. A test client built with `Client(mcp)` and no `elicitation_callback` hits the same error.

**`Resolve` on a prompt.** The SDK honors `Resolve` only on tools, and it does not refuse it elsewhere. On a prompt the parameter is published as an argument the user is expected to fill in, and getting the prompt fails as an internal error:

```
Resolve on a prompt: published arguments ['pid', 'approval']
prompts/get confirm_prompt: MCPError -32603: Internal server error
```

**A wrong signature.** The other mistakes in section 6's list never reach the wire: the decorator raises `InvalidSignature` at import, the server never starts, and a host reports only that the process died. Running the server once in a terminal shows the message.

## Common pitfalls

1. **Letting the question text vary between rounds.** A timestamp, a live reading, or a changing count in the message changes its SHA-256 digest, so the recorded answer never matches and the call never converges; build the message from the tool's arguments and nothing else.
2. **Shipping the SDK's default `requestState` key to more than one worker.** It is a process-local ephemeral key, so a retry that reaches another worker, or a restarted one, gets `-32602`; pass `RequestStateSecurity(keys=[...])` with the same key of at least 32 bytes to every worker.
3. **Treating `action: "accept"` as approval.** Accept means the form came back; in form mode the answer inside it may still be no, and in URL mode it means only that the user consented to open the URL.
4. **Reusing the JSON-RPC `id` on the retry.** It **must** differ, because the two requests are independent.
5. **Putting `inputResponses` or `requestState` in `_meta`.** They are siblings of `name` and `arguments` inside `params`, which is easy to get wrong by analogy with the revision and capability keys that every request now carries in `_meta`.
6. **Asking for a secret in form mode.** Passwords, API keys, tokens, and payment credentials are a **must not**, because the answer would pass through the client; use URL mode.

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The four steps, the three types, and the eight server and four client requirements quoted in section 3. <https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr>
- Specification, *"Patterns overview"*, revision 2026-07-28. The rule that servers must not initiate JSON-RPC requests, quoted in section 2. <https://modelcontextprotocol.io/specification/draft/basic/patterns>
- Specification, *"Elicitation"*, revision 2026-07-28. Capability declaration, the schema restrictions in section 7, the three actions, URL mode, and the phishing and statefulness requirements in section 9. <https://modelcontextprotocol.io/specification/draft/client/elicitation>
- Specification, *"Base protocol"*, revision 2026-07-28. `resultType`, the per-request `_meta` keys, and the error codes, including the retired `-32042`. <https://modelcontextprotocol.io/specification/draft/basic>
- Specification, *"Caching"*, revision 2026-07-28. Why interim results carry no caching hints and MRTR retries are never cacheable. <https://modelcontextprotocol.io/specification/draft/server/utilities/caching>
- SEP-2322, *"Multi Round-Trip Requests"* (2026), status Final. The design rationale and the worked example section 5 quotes on re-sent arguments. <https://modelcontextprotocol.io/seps/2322-MRTR>
- SEP-1036 (URL-mode elicitation) and SEP-1034 (elicitation defaults). The proposals behind section 9's mode and section 7's `default` values.
- MCP Python SDK, release 2.3.0. `MCPServer`, `Resolve`, `Elicit`, `ElicitationResult`, `RequestStateSecurity`, and `Client` with an `elicitation_callback`. <https://github.com/modelcontextprotocol/python-sdk>
- [code/05-first-server/](../../code/05-first-server/). The complete project, with `interactive.py` and the elicitation tests.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 09 - Tasks: work that outlives a single request](../09-tasks/index.md):** the other thing a server can return instead of a finished result, and the difference between waiting for a person and waiting for a long job.
- **[Post 14 - Project 1: Writes, transactions, and an audit trail](../14-database-writes/index.md):** this loop doing real work, gating every database mutation behind a preview a human has to approve.
