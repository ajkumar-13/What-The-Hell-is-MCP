# 09 · Tasks: work that outlives a single request

> **TL;DR.** Some tool calls take minutes, and the Model Context Protocol (MCP) tasks extension replaces "hold the connection open and hope" with an object that has an identifier, a lifecycle, and a cancel method. Task creation is **server-directed**: the client declares `io.modelcontextprotocol/tasks` in its per-request capabilities and the server decides, on each individual request, whether to hand back a task instead of a result. The per-tool `execution.taskSupport` field that most tutorials still teach was the 2025-11-25 mechanism and does not exist in 2026-07-28. This post covers the extensions framework, the whole lifecycle on the wire, and the honest state of tooling today, and ends by building a working task on the seams the Python SDK already ships.
>
> **After reading this you will be able to:**
> - Declare an extension in per-request capabilities, and read a server's from `server/discover`.
> - Drive a task from creation through polling to a terminal state, by hand.
> - Tell a task apart from a multi round-trip request, and pick the right one.
> - Report progress from a slow tool today, and stand up the extension yourself on the seams the software development kit already ships.

![A state machine. A tools/call returns a result tagged resultType task, creating the task in a working state. Working and input_required sit inside a dashed non-terminal box, with the server moving one way and tasks/update moving the other. A rail down the right edge of that box carries three arrows out to completed, failed, and cancelled.](diagrams/01-task-state-machine.svg) *Five states. Once the task exists the client drives only two of these arrows, and one of those two is a request rather than a command.*

---

## 1. Extensions, and why tasks is one

Suppose you have a tool that renders a video, and you want the protocol to stop pretending it will finish inside one request. You search, you find a snippet, you paste it into your tool definition:

```json
"execution": { "taskSupport": "required" }
```

Nothing happens. Not because you typed it wrong. **That field does not exist in revision 2026-07-28.** There are zero occurrences of it in the canonical schema. It belonged to a feature that was removed from the core specification and rebuilt somewhere else, and the somewhere else is the first thing worth understanding, because it is the shape the whole protocol will grow in from now on.

An **extension** is an optional addition to the specification. Specification Enhancement Proposal (SEP) 2133, which created the mechanism, defines it:

> An MCP extension is an optional addition to the specification that defines capabilities beyond the core protocol. Extensions enable functionality that may be modular (e.g., distinct features like authentication), specialized (e.g., industry-specific logic), or experimental (e.g., features being incubated for potential core inclusion).

Three tiers exist. **Official** extensions live in the MCP GitHub organization under an `ext-` prefix, use the `io.modelcontextprotocol` vendor prefix, and must be Apache-2.0 licensed. **Experimental** ones use an `experimental-ext-` prefix, must be tied to a working group or interest group, must advertise their non-official status in the README, and may be archived by the core maintainers at any time. **Unofficial** ones sit outside MCP governance entirely, which is a perfectly legitimate place to be.

### The naming rule

An extension identifier is `{vendor-prefix}/{extension-name}`, and the prefix is reverse Domain Name System (DNS) notation: `io.modelcontextprotocol/tasks`, `com.example/my-extension`. The rules are the same as for `_meta` keys, with one difference: here the prefix is **mandatory**.

The reservation rule catches people. **Any prefix whose second label is `modelcontextprotocol` or `mcp` is reserved for MCP.** So `io.modelcontextprotocol/`, `dev.mcp/`, `org.modelcontextprotocol.api/`, and `com.mcp.tools/` are all off limits, while `com.example.mcp/` is fine, because its second label is `example`.

Breaking changes require a **new identifier**, for example `io.modelcontextprotocol/oauth-client-credentials-v2`. Removing a field, renaming one, changing a type, adding a required field, or altering the semantics of existing behavior all count as breaking. The recommended alternative is to version inside the settings object rather than mint a new name.

### Two rules that govern every extension

Extensions are **always disabled by default** and require explicit opt-in. And when the two sides disagree, the specification is prescriptive:

> If one party supports an extension but the other does not, the supporting party MUST either revert to core protocol behavior or reject the request with an appropriate error if the extension is mandatory.

There is also a security default worth internalizing before you write a line: both sides **should** treat any new field an extension introduces as untrusted and validate it comprehensively. An extension is not a trusted channel.

### Where the declaration lives, and where the docs are wrong

Extensions are advertised in an `extensions` field on capabilities, a map from extension identifier to a per-extension settings object. An empty object `{}` means "supported, no settings". [Post 03](../03-wire-protocol/index.md) covered why there is no `initialize` handshake in this revision, and the consequence lands here: the client's declaration goes in `_meta` on **every request**, and the server's goes in the `server/discover` result.

Be careful reading the official pages. As of writing, `/extensions/overview`, SEP-2133 itself, and the client support matrix all still document negotiation as something that happens during an `initialize` handshake, with a `2025-06-18` protocol version in the examples. Those pages have not been updated for the stateless revision. The authoritative text is the versioning page's extension-negotiation section, which shows bare `capabilities` objects with no `initialize` wrapper. The tasks extension is, at the moment, the only extension document that has been rewritten for per-request capabilities.

### Is tasks official? Nobody can tell you cleanly

This post would be dishonest if it glossed over the status of the thing it is teaching. Four primary sources disagree.

| Source | What it says |
|---|---|
| `/extensions/overview` | Lists **MCP Tasks** under *Official Extension Repositories* |
| SEP-2663 | Status **Final**, Type **Extensions Track**, which means an accepted official extension |
| The `ext-tasks` repository | Uses the official `ext-` prefix, but its README says "It is **not** an official extension and may change significantly or be discontinued" |
| `/extensions/tasks/overview` | Points at an `experimental-ext-tasks` repository that does not exist |

The specification file itself lives at `specification/draft/tasks.md`. There is no stable or date-versioned tasks specification directory. The most defensible reading is that tasks is an Extensions Track extension whose SEP is Final and which ships alongside 2026-07-28 as the replacement for core tasks, but whose text is still at draft and whose own repository describes itself as experimental. Treat the wire format as likely to shift. This post is not going to pick a side that the sources have not picked.

## 2. What a task is for, and the thing that is not a task

Here is a tool that cannot be made fast, because being slow is the point of it. The complete file is [code/05-first-server/src/system_info/progress.py](../../code/05-first-server/src/system_info/progress.py), and it samples central processing unit (CPU) usage once a second for up to thirty seconds:

```python
@mcp.tool(title="Watch CPU over time", annotations=ToolAnnotations(read_only_hint=True, ...))
async def watch_cpu(seconds: int = 5, ctx: Context = None) -> CpuWatch:
    """Sample CPU usage once a second and report the series."""
    seconds = max(1, min(seconds, 30))
    samples: list[CpuSample] = []
    for i in range(seconds):
        percent = psutil.cpu_percent(interval=None)
        await asyncio.sleep(1)
        percent = psutil.cpu_percent(interval=None)
        samples.append(CpuSample(second=i + 1, percent=round(percent, 1)))
        if ctx is not None:
            await ctx.report_progress(
                progress=i + 1, total=seconds,
                message=f"sampled {i + 1} of {seconds} seconds",
            )
    ...
```

Thirty seconds is survivable. Now imagine the same shape around a video render, a full repository index, or a batch job against a warehouse. The request is open the whole time, one specific process is committed to it, and every layer between the client and that process has an opinion about how long a Hypertext Transfer Protocol (HTTP) request may live.

![Two timelines of equal length. The upper one is a single wide bar, one request held open for the whole job. The lower one is a short create exchange, a run of short polls, and a short final exchange returning the completed status.](diagrams/02-sync-vs-task.svg) *Neither row is faster. The lower one is the one that survives a deploy.*

**Progress reporting and tasks are different mechanisms, and only one of them works everywhere today.** The `ctx.report_progress` call above is core protocol. When the client puts a `progressToken` in the request's `_meta`, the server may emit `notifications/progress` on that request's response stream, and `watch_cpu` does. That is a real method on `Context` in `mcp` 2.0.0b2, it needs no extension, and the tool above is registered and published like any other: a capability listing shows `watch_cpu` with one input property and an output schema, alongside the rest of the server's tools.

What progress reporting does **not** do is change the shape of the exchange. The request is still open. The connection is still held. You are still one timeout away from losing the work, and you are still telling the client "keep waiting" rather than "come back later". Progress is a courtesy inside a synchronous call; a task is a different call.

There is a sharp corner here. `notifications/progress` and `notifications/message` **must not** be sent on a task's notification stream, and progress is **not supported on tasks at all** in this extension. In 2025-11-25 core tasks, the original `progressToken` persisted into the task and progress notifications kept flowing. That is gone. Turning a tool into a task means giving up progress reporting and replacing it with `statusMessage` plus polling.

## 3. Declaring the extension, and why creation is server-directed

The client declares support in its per-request capabilities. This is the whole handshake:

```jsonc
// client to server, in the _meta of every request
{
  "params": {
    "_meta": {
      "io.modelcontextprotocol/clientCapabilities": {
        "extensions": {
          "io.modelcontextprotocol/tasks": {}
        }
      }
    }
  }
}
```

The server advertises the same identifier in its `server/discover` result:

```jsonc
// server to client, in response to server/discover
{
  "result": {
    "capabilities": {
      "extensions": {
        "io.modelcontextprotocol/tasks": {}
      }
    }
  }
}
```

No extension-specific settings are defined. `{}` means supported.

![A client sending the same capability declaration on two requests, and a server answering one with resultType complete and the other with resultType task. Below, a struck-through tools/list entry carrying an execution taskSupport field, marked as removed.](diagrams/03-extension-negotiation.svg) *The declaration is identical on both requests. The decision is not.*

Now the part that most published material still gets wrong. In 2025-11-25, tasks were part of core and the handshake had three moving pieces: a server capability under `tasks.requests.tools.call`, a per-tool `execution.taskSupport` field in `tools/list` with the values `required`, `optional`, or `forbidden`, and a `task` object the client added to the request parameters to opt in. SEP-2663 deleted all three. Its motivation section is worth quoting, because it explains the design and not just the change:

> **The handshake is fragile.** A client that wants to opt into tasks must therefore prime its state with a `tools/list` call before issuing any task-augmented request, and cannot blindly attach a `task` parameter to every request to handle tools isomorphically. This is confusing, implicit, and easy to get wrong.

The replacement is one sentence in the extension specification:

> Task creation is **server-directed**: the client signals support by including the extension in its per-request capabilities, and the server decides on a per-request basis whether to materialize a task.

So: one declaration point, zero per-tool metadata, zero per-request opt-in flags. Read the consequences carefully, because each one contradicts an intuition somebody has.

- **A client declaring the extension is not requesting a task.** It is saying "I can cope with one if you send it".
- **The server may return a task in response to any supported request, at its own discretion.** The specification calls it "the sole decider".
- **A server cannot promise in advance.** There is nowhere to put the promise. The same tool may return a normal result at ten in the morning and a task at noon, because the queue got long.
- **Supported methods are `tools/call` only.** Not `resources/read`, not `prompts/get`. The specification notes this is extensible in future revisions.

And the rule that is the cleanest one-line illustration of statelessness in the entire specification:

> A server **MUST NOT** return `CreateTaskResult` to a client that did not include the extension capability on its request, **regardless of prior declarations**.

Read that last clause twice. It is not enough that the client declared the extension on its last request, or on the request that created the task, or during some notional connection setup. There is no connection setup. Each request stands alone, and a capability the client did not assert on *this* request does not exist as far as *this* request is concerned. If you are the kind of server author who caches the client's capabilities in a dictionary keyed by connection, this is the sentence that tells you not to.

## 4. Either shape, on any tool call

Because the server decides, a client that declares the extension **must** be prepared to handle either result shape on any `tools/call`. The discriminator is `resultType`, which [Post 03](../03-wire-protocol/index.md) introduced and which every result in this revision carries. The extension adds one value to the core set:

```typescript
// "task" is introduced by this extension.
type ResultType = "complete" | "input_required" | "task" | string;
```

Servers **must** set `resultType: "task"` on a `CreateTaskResult` and **must not** set it on anything else. Here is the exchange, from the extension specification's own examples. The JavaScript Object Notation Remote Procedure Call (JSON-RPC) request is completely ordinary:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": { "name": "get_weather", "arguments": { "city": "New York" } }
}
```

The response is not:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "task",
    "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840",
    "status": "working",
    "statusMessage": "The operation is now in progress.",
    "createdAt": "2025-11-25T10:30:00Z",
    "lastUpdatedAt": "2025-11-25T10:40:00Z",
    "ttlMs": 60000,
    "pollIntervalMs": 5000
  }
}
```

The task fields are **inlined** into the result, not nested under a `task` key. The timestamps in the specification's samples are leftover 2025-11-25 dates and carry no meaning.

Note the field names: `ttlMs` and `pollIntervalMs`, with the `Ms` suffix. In 2025-11-25 they were `ttl` and `pollInterval`. A port that misses this fails silently, because both are optional-ish in practice and a missing `pollIntervalMs` just means the client picks its own interval.

Two creation-time rules matter more than they look.

**Durability.** A server **must not** return a `CreateTaskResult` until the task is durably created, meaning until a `tasks/get` for that `taskId` would resolve. In an eventually consistent store the server waits for consistency before answering. This is what lets a client poll immediately without a speculative retry loop, and it is a real constraint on your storage design rather than a formality.

**Multi Round-Trip Requests (MRTR) come first.** If a server needs to ask the user something *before* deciding whether to create a task, for example a confirmation, it **should** resolve all of those exchanges synchronously first, and only then answer with a `CreateTaskResult`. Section 8 is about why those two things are not the same mechanism.

## 5. The lifecycle

Five statuses, and the hero diagram at the top of this post is the whole machine.

| Status | Meaning |
|---|---|
| `working` | The request is being processed. |
| `input_required` | The server needs client input. `tasks/get` carries `inputRequests`; the client answers with `tasks/update`. |
| `completed` | Finished successfully. `result` holds the final output. |
| `failed` | A JSON-RPC error occurred during execution. `error` holds it. |
| `cancelled` | Canceled before completion. |

`working` and `input_required` alternate freely. `completed`, `failed`, and `cancelled` are terminal and immutable. The `Task` object itself is small:

```typescript
interface Task {
  taskId: string;
  status: "working" | "input_required" | "completed" | "cancelled" | "failed";
  statusMessage?: string;    // MAY be shown to the end user or the model
  createdAt: string;         // ISO 8601
  lastUpdatedAt: string;     // ISO 8601
  ttlMs: number | null;      // null means unlimited; MAY change over the task's life
  pollIntervalMs?: number;   // clients SHOULD honor it; MAY change over the task's life
}
```

**One semantic flip will silently break a port from 2025-11-25.** A tool call that returns `isError: true` used to move the task to `failed`. In the extension it moves the task to **`completed`**, with the error sitting inside `result`. The specification is emphatic about why:

> This maintains a strong separation between protocol-level faults (which use the `failed` status) and other faults.

That is the same split [Post 06](../06-tools-in-depth/index.md) drew between a protocol error and a tool-execution error, applied to task status. `failed` means the machinery broke. `completed` with `isError: true` means the machinery worked and the tool has bad news. A client that branches on `status == "failed"` to decide whether to show an error will show nothing at all for the most common failure mode there is.

## 6. Polling with `tasks/get`, and being told instead

The request is as small as a request gets:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tasks/get",
  "params": { "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840" }
}
```

The response carries the whole task, with whatever extra field the status implies: nothing extra for `working` and `cancelled`, `inputRequests` for `input_required`, `result` for `completed`, `error` for `failed`.

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "resultType": "complete",
    "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840",
    "status": "working",
    "createdAt": "2025-11-25T10:30:00Z",
    "lastUpdatedAt": "2025-11-25T10:50:00Z",
    "ttlMs": 3600000,
    "pollIntervalMs": 5000
  }
}
```

**`resultType` here is `"complete"`, not `"task"`.** This trips people because the payload is so obviously about a task. `"task"` is only ever the discriminator on a `CreateTaskResult`; everywhere else the task is just the content of an ordinary complete result. Worth flagging: the extension specification's own error-handling examples show `"resultType": "task"` on what they describe as `tasks/get` responses, which contradicts the rule stated a few sections earlier in the same document. Treat those two examples as errors, not as a subtlety you have missed.

**Polling discipline.** Clients **should** respect `pollIntervalMs`, and servers **may** rate limit clients that ignore it. Clients **should** keep polling until the task reaches a terminal status or they cancel it. And clients **should** persist task identifiers durably, so a crash or a restart does not orphan work the server is still doing. That last one is the practical difference between a task and a promise: a promise dies with your process.

This is also the answer to the debt [Post 04](../04-transports/index.md) left open. Streams are not resumable in this revision, so a broken response stream loses the in-flight request and the client has to re-issue it under a brand new identifier, starting the work again. A task changes what breaking costs. The exchange that dies is a `tasks/get` lasting milliseconds, the work carries on at the server, and the retry is the same poll with the same `taskId`. Resumability came back, one layer up, as an identifier the client stores instead of an event identifier the transport replays.

**Time to live (TTL).** If `ttlMs` is not null, a client **may** treat `createdAt + ttlMs` as a backstop and give up after it. Servers **may** mark a task `failed` any time after the TTL elapses and delete it any time after that, and `ttlMs` itself **may** change over the task's lifetime. A purged task legitimately answers `-32602`:

```json
{ "jsonrpc": "2.0", "id": 70,
  "error": { "code": -32602, "message": "Failed to retrieve task: Task not found" } }
```

**Being told instead of asking.** Polling is not the only option. A client can subscribe with `subscriptions/listen`, naming the task identifiers it cares about, and receive `notifications/tasks` instead:

```typescript
export interface SubscriptionsListenRequest extends Request {
  method: "subscriptions/listen";
  params: { notifications: { taskIds?: string[] } };
}
```

The server acknowledges with the subset it agreed to, in a `notifications/subscriptions/acknowledged` message. Each `notifications/tasks` payload carries a complete task, identical to what `tasks/get` would have returned at that moment, so there is no follow-up round trip. Clients **may** keep polling as well and need not. Keeping a cheap poll as a backstop is still wise, because that stream is not resumable either: if it drops, the notifications you would have received are simply not delivered, and the `taskId` is the only thing that survives. The notification was called `notifications/tasks/status` in 2025-11-25.

## 7. `tasks/update` and `tasks/cancel`

A task in `input_required` surfaces its questions on every `tasks/get`, in an `inputRequests` map whose shape is the same one [Post 08](../08-elicitation-and-mrtr/index.md) used for MRTR: keys are server-assigned identifiers, values are bare request objects with a `method` and `params` and no envelope.

```json
"inputRequests": {
  "name": {
    "method": "elicitation/create",
    "params": {
      "mode": "form",
      "message": "Please enter your name.",
      "requestedSchema": {
        "type": "object",
        "properties": { "name": { "type": "string" } },
        "required": ["name"]
      }
    }
  }
}
```

The client answers with `tasks/update`:

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "method": "tasks/update",
  "params": {
    "taskId": "786512e2-9e0d-44bd-8f29-789f320fe840",
    "inputResponses": {
      "name": { "action": "accept", "content": { "name": "octocat" } }
    }
  }
}
```

The server acknowledges with an empty result and `resultType: "complete"`. The rules around this are worth reading once carefully, because several of them are counterintuitive.

- **The acknowledgment is eventually consistent.** The task's observable status may not reflect the input yet. Do not assert on it in the next poll.
- **`inputRequests` is a point-in-time snapshot, not a queue.** Every poll re-sends every outstanding request. Clients **should** deduplicate by key, or the user gets the same dialog every five seconds.
- **Keys are unique for the whole life of the task.** A server **must not** reuse a key after a response for it was delivered, and **must not** use one key for two distinct questions. This is stronger than the MRTR rule, where keys need only be unique within one request.
- **Partial answers are allowed.** A server **may** accept a subset; the task stays `input_required` until the rest arrive. It **should** ignore responses for keys that were never issued, already answered, or superseded.
- **A task is not a higher-trust channel.** Clients **must** treat each entry exactly as they would the equivalent standalone request: same consent, same rendering, same suspicion. That phrase is the specification's own.

One caution on the example above. The extension specification's worked flow answers an elicitation whose schema declares a `name` property with `"content": {"input": "Luca"}`, keyed by `input` rather than by the schema property. The core MRTR page keys `content` by the schema property, and that is almost certainly the correct form. The example above uses the MRTR form deliberately.

**Cancellation is a request, not a command.** `tasks/cancel` takes a `taskId`, returns an empty acknowledgment, and that is all it promises:

- `notifications/cancelled`, the core cancellation notification, **must not** be used for tasks.
- Cancellation is **cooperative**. The server is obliged to acknowledge, not to stop. Reaching `cancelled` is explicitly not guaranteed, and the task may land in a different terminal status.
- It is eventually consistent, so the status may remain `working` after the acknowledgment.
- Clients **may** discard all task state the moment they send the cancel, and need not poll to confirm.

If you need cancellation that actually stops work, you have to build it: check a flag between units of work, and accept that anything already committed stays committed.

## 8. Tasks against MRTR, and the distinction that trips people up

Both mechanisms produce a result that means "not yet", and both use the same `inputRequests`/`inputResponses` value shapes, so people conflate them constantly. The specification anticipated this and says so directly:

> While task `inputRequests` share structural similarities with multi round-trip requests, they are a distinct mechanism: task `inputRequests` are surfaced via `tasks/get` and fulfilled via `tasks/update`, not via retries of the original method. A server that needs client input *before* returning a `CreateTaskResult` uses the multi round-trip request flow on the original request; a server that needs client input *during* task execution uses the `inputRequests`/`inputResponses` mechanism described here.

|  | MRTR, core | Task `inputRequests`, extension |
|---|---|---|
| When | Before the task exists | During task execution |
| Surfaced on | The original request's result, `resultType: "input_required"` | A `tasks/get` result, `status: "input_required"` |
| Fulfilled by | **Retrying the original request** with a new JSON-RPC id, `inputResponses`, and the echoed `requestState` | **`tasks/update`** with `taskId` and `inputResponses` |
| Server state | Stateless; carried in the opaque `requestState` | Stateful; keyed by `taskId` |
| Key uniqueness | Within one request | Over the whole life of the task |
| Repeats | The server may return `input_required` again on the retry | The server re-sends outstanding requests on every poll |

The practical test is a question about time. If you cannot even start without the answer, that is MRTR: return `input_required`, get retried, and the specification says to finish those exchanges before creating the task. If you are already running and hit something you cannot decide alone, that is a task input request, and the original `tools/call` finished long ago.

## 9. `taskId` is the state handle

Tasks is the worked example of the design SEP-2567 prescribed when it deleted sessions: cross-call state uses explicit, server-minted handles passed as ordinary parameters. The `taskId` **is** that handle. Everything hangs off it, and it is re-sent on every request that touches the task. Four consequences follow, and they are the most operationally important part of this post.

**`tasks/result` and `tasks/list` were both removed.** `tasks/result` existed to hold open a stream so the server could push unsolicited elicitation requests down it, which is illegal now that servers cannot initiate requests at all. `tasks/list` died for a subtler reason, stated plainly: "While it was possible for tasks to instead be bound to a session, SEP-2567 removes sessions from the protocol. There is no other natural scope a server can define unilaterally." The security considerations then turn the loss into a gain: "Because there is no `tasks/list`, a server cannot inadvertently leak the existence of one caller's tasks to another."

**Task identifier entropy is security-critical.** With no session and possibly no authorization context, the identifier is the only boundary around the task. The specification is direct: a server **may** use task identifiers as bearer tokens for its stored state, and servers **must** generate them with sufficient entropy that a third party cannot enumerate or guess them. A sequential integer is a vulnerability, not an inconvenience.

**`Mcp-Name: <taskId>` replaces sticky sessions.** Over Streamable HTTP, a client **must** set the `Mcp-Name` header to `params.taskId` on `tasks/get`, `tasks/update`, and `tasks/cancel`, so intermediaries can route the request to the instance holding the state, "which is typically required for correctness". `Mcp-Method` carries the method name, per the header conventions [Post 04](../04-transports/index.md) covered.

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tasks/get
Mcp-Name: 786512e2-9e0d-44bd-8f29-789f320fe840
```

This is worth pausing on as a piece of protocol design. Sticky routing did not disappear; it became an explicit, inspectable header that a load balancer can read without parsing a body, instead of an implicit cookie nobody could audit.

**The capability must be re-asserted on every task request.** Not only on the initiating `tools/call`, but on `tasks/get`, `tasks/update`, and `tasks/cancel` too. A client that forgets gets an error naming exactly what was missing:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32021,
    "message": "Missing required client capability",
    "data": {
      "requiredCapabilities": {
        "extensions": { "io.modelcontextprotocol/tasks": {} }
      }
    }
  }
}
```

**Mind the code.** The extension specification prints `-32003` in three places. The core 2026-07-28 specification renumbered `MissingRequiredClientCapability` from `-32003` to `-32021`, declared `-32000` through `-32019` legacy, and says new implementations should not use that sub-range at all. The extension text has not caught up. Emit `-32021`, and accept either on the client side until the extension is updated. The Python types package has already picked a side: `mcp_types.jsonrpc.MISSING_REQUIRED_CLIENT_CAPABILITY` is `-32021`.

## 10. What the software development kit gives you today

Two things are true at once here, and most write-ups manage only one of them.

**The Python software development kit (SDK) ships no tasks implementation.** `@mcp.tool()` has no `execution` parameter, `MCPServer` never populates one, and nothing in `mcp` 2.0.0b2 answers `tasks/get`, `tasks/update`, or `tasks/cancel` out of the box. Two lines in a Python session against `mcp` 2.0.0b2 and `mcp-types` 2.0.0b2 tell that half of the story:

```python
>>> import inspect
>>> from mcp.server.mcpserver import MCPServer
>>> list(inspect.signature(MCPServer.tool).parameters)
['self', 'name', 'title', 'description', 'annotations', 'icons', 'meta', 'structured_output']
```

What the types package does ship is the **2025-11-25** feature, labeled as such in its own docstrings:

```python
>>> from mcp_types import Task, CreateTaskResult
>>> Task.__doc__
'Data associated with a task (2025-11-25 only).'
>>> list(Task.model_fields)
['task_id', 'status', 'status_message', 'created_at', 'last_updated_at', 'ttl', 'poll_interval']
>>> list(CreateTaskResult.model_fields)
['meta', 'task']
```

Read those two field lists against section 4. `ttl` and `poll_interval`, not `ttl_ms` and `poll_interval_ms`. A `task` wrapper, not inlined fields. `ToolExecution` carries the same docstring, and so do `GetTaskPayloadRequest` for the deleted `tasks/result` and `ListTasksRequest` for the deleted `tasks/list`. There is no `UpdateTaskRequest` at all. These are the old core types kept for talking to older servers, and using them against a 2026-07-28 peer would produce the wrong messages.

`ClientCapabilities` carries both revisions' fields at once, `extensions` for this one and `tasks` for the last:

```python
>>> from mcp_types import ClientCapabilities
>>> list(ClientCapabilities.model_fields)
['experimental', 'sampling', 'elicitation', 'roots', 'extensions', 'tasks']
```

**The other half: the extension machinery is already there, and it was built with tasks in mind.** `mcp.server.extension` opens with the line "Pluggable extension interface for MCP servers (SEP-2133)", and between the two sides there are seven seams:

| Seam | What it does |
|---|---|
| `MCPServer(extensions=[...])` and the `Extension` base class | advertises `identifier` under `capabilities.extensions` in `server/discover` |
| `Extension.methods()` returning `MethodBinding` | serves a request method the core schema does not define |
| `Extension.intercept_tool_call` | wraps `tools/call` and may answer instead of the tool |
| `require_client_extension(ctx, identifier)` | raises `-32021` with a `requiredCapabilities` payload |
| `Client(extensions=[...])` and `ClientExtension` | puts the identifier in the per-request `_meta` |
| `ClientExtension.claims()` returning `ResultClaim` | registers a `resultType` outside the core set, plus the resolver that finishes it |
| `Request.name_param` | mirrors a params key into the `Mcp-Name` header on every send |

Two of those docstrings say the quiet part out loud. `MethodBinding` is documented as "A new request method an extension serves, e.g. `tasks/get`". `name_param` is documented as "Wire-params key mirrored into the `Mcp-Name` header on sends; SEP-2663 requires it for `tasks/*`". There is also a client-side `advertise(identifier)` shortcut, whose own docstring warns that "advertising an extension you do not implement asserts wire support you do not have".

So you can build it, and the check fits in one file. [snippets/tasks_extension.py](snippets/tasks_extension.py) wires a server-side extension to a client-side claim and runs both ends in one process:

```
$ uv run --with 'mcp==2.0.0b2' python tasks_extension.py
server advertises: {'io.modelcontextprotocol/tasks': {}}
poll 1 -> working
poll 2 -> completed
tasks client got: done 3
plain client got: done 3   (resultType complete, no task involved)
```

Those last two lines are the whole post. Same server, same process, same registered tool: the client that declared the extension got a task and polled it to completion, and the client that did not got its answer directly. Nothing was registered conditionally at startup, which is the point. Under statelessness one process serves a task-capable client and a plain one on interleaved requests, so the branch belongs inside the handler, reading `ctx.session.client_params` fresh each time.

The file cheats in two ways it admits to: the work runs eagerly inside the interceptor rather than out of band, and the store is a dictionary. A real server can afford neither, because of the durability rule. Nor does the file make you compliant. It has no `tasks/update`, no `tasks/cancel`, no `notifications/tasks`, no TTL, and none of the timestamp fields. Read it as proof that the SDK will not stop you, not as an implementation.

Tasks is also absent from the published client support matrix entirely, which tracks only MCP Apps and the two authorization extensions. This post therefore cannot tell you which hosts would understand a task if you sent one, and neither can the documentation.

The practical reading: build the slow tool now, report progress the way section 2 shows, design your state around an explicit handle you mint yourself, and keep the extension behind a per-request check until the wire format settles. [Post 24](../24-mcp-apps-and-frontier/index.md) revisits the extension landscape once more of it has settled.

## 11. When a task is the wrong answer

**When the work is short.** A task costs a create, at least one poll, and durable storage. Under a few seconds, that is more machinery and more latency than just answering.

**When you only need to say "still going".** That is progress reporting, and it works today.

**When the answer is needed to continue a conversation.** A host that has to explain to a user that the result will arrive in four minutes has a user experience problem the protocol cannot solve for it. Sometimes the better tool returns a handle to a job the user can check later through a resource, which is a design [Post 07](../07-resources-and-prompts/index.md) would recognize, and which needs no extension.

**When you cannot store the state durably.** The durability rule is not advisory. If you cannot guarantee that a `tasks/get` immediately after the create will resolve, you cannot correctly return a `CreateTaskResult`.

**When you need the input before you start.** That is MRTR, from section 8.

**When you need progress on the slow work.** Progress notifications are not supported on tasks. If a percentage bar is the requirement, a task is currently the wrong shape.

---

## Common pitfalls

- **Teaching or writing `execution.taskSupport`.** It has zero occurrences in the 2026-07-28 schema. It was the 2025-11-25 mechanism, SEP-2663 deleted it, and there is no replacement per-tool field. If a tutorial shows one, it is a revision behind, and the Python types package still ships the class with a `(2025-11-25 only)` docstring to catch the unwary.
- **Caching the client's capability declaration against the connection.** A server **must not** return a task to a client that did not declare the extension on that request, "regardless of prior declarations". Read `_meta` fresh, every time, including on `tasks/get`, `tasks/update`, and `tasks/cancel`.
- **Branching on `status == "failed"` to detect a failed tool.** A tool that returns `isError: true` produces a `completed` task with the error inside `result`. `failed` is reserved for JSON-RPC faults. This inverted between 2025-11-25 and the extension and it fails silently.
- **Expecting `resultType: "task"` from `tasks/get`.** It is `"complete"`. `"task"` appears exactly once per task, on the `CreateTaskResult`. The extension specification's own error examples get this wrong, which is not much help.
- **Treating `tasks/cancel` as a stop button.** It is cooperative. The server acknowledges, may keep working, and the task may reach a terminal status other than `cancelled`.
- **Re-prompting the user on every poll.** `inputRequests` is a snapshot, re-sent in full while outstanding. Deduplicate by key on the client, and never reuse a key on the server.
- **Sequential or guessable task identifiers.** With no session, the identifier is the access boundary, and the specification permits servers to use it as a bearer token. Generate it with real entropy.
- **Omitting `Mcp-Name` on the task methods over HTTP.** Without it an intermediary cannot route to the instance holding the state, which the specification says is typically required for correctness.

---

## Further reading

- Tasks extension specification, revision draft, `ext-tasks` repository. Every normative rule quoted here: capability negotiation, the lifecycle, `tasks/get`, `tasks/update`, `tasks/cancel`, notifications, and the security considerations. <https://github.com/modelcontextprotocol/ext-tasks>
- SEP-2663, *"Tasks extension"* (2026), status Final, Extensions Track. The motivation quoted in section 3, and the migration rationale for removing `tasks/result` and `tasks/list`.
- SEP-2133, *"Extensions"* (2025), status Final. The definition in section 1, the three tiers, the naming rule, and the graceful-degradation requirement.
- Specification, *"Versioning"* § extension negotiation, revision 2026-07-28. The authoritative shape of an `extensions` declaration, as against the stale `initialize` examples on the extensions overview page.
- Specification, *"Progress"*, revision 2026-07-28. `progressToken` and `notifications/progress`, the mechanism section 2 uses. <https://modelcontextprotocol.io/specification/draft/basic/patterns/progress>
- Extensions overview and client support matrix. <https://modelcontextprotocol.io/extensions/overview>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 10 — Building your own MCP client](../10-mcp-client/index.md)**: the other side of this post. A client that declares the extension has to handle both result shapes and drive the polling loop, and writing one is the fastest way to believe that the server really is the sole decider.
- **[Post 24 — MCP Apps, extensions, and where the protocol goes next](../24-mcp-apps-and-frontier/index.md)**: the rest of the extension landscape, including the one extension that is unambiguously stable, and what promotion into core would mean for tasks.
