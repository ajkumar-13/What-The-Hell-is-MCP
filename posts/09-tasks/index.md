# 09 - Tasks: work that outlives a single request

> **TL;DR.** Some tool calls take minutes, and the Model Context Protocol (MCP) tasks extension replaces "hold the connection open and hope" with an object that has an identifier, a lifecycle, and a cancel method. Task creation is **server-directed**: the client declares `io.modelcontextprotocol/tasks` in its per-request capabilities, and the server decides, request by request, whether to hand back a task instead of a result; the per-tool `execution.taskSupport` field that older tutorials teach belonged to revision 2025-11-25 and does not exist in 2026-07-28. This post reads the extensions framework and the whole task lifecycle on the wire, then builds a working task, with polling and cancellation, on the seams the Python software development kit (SDK) already ships, because the SDK ships no tasks implementation of its own.
>
> **Prerequisites:** [Post 08](../08-elicitation-and-mrtr/index.md).
> **Safe to skip?** Skip it if the reader can already say who decides that a `tools/call` becomes a task, which `resultType` a `tasks/get` result carries, and why a task's input requests are not multi round-trip requests.
>
> **After reading, you will be able to:**
>
> - Declare an extension in per-request capabilities and read a server's extensions from its `server/discover` result.
> - Drive a task by hand from `tools/call` through `tasks/get` polling to a terminal status, including a cancellation.
> - Tell a task apart from a multi round-trip request and choose the one an interaction needs.
> - Report progress from a slow tool with `notifications/progress`, and stand up the tasks extension on the seams the Python SDK ships.

![A state machine. A tools/call returns a result tagged resultType task, creating the task in a working state. Working and input_required sit inside a dashed non-terminal box, with the server moving one way and tasks/update moving the other. A rail down the right edge of that box carries three arrows out to completed, failed, and cancelled.](diagrams/01-task-state-machine.svg)

*Five statuses. Once the task exists the client drives only two of these arrows, and one of those two is a request rather than a command.*

---

## 1. The question: where did the per-tool task field go?

Suppose a tool renders a video, and its author wants the protocol to stop pretending the job will finish inside one request. A search turns up a snippet, and the snippet goes into the tool definition:

```json
"execution": { "taskSupport": "required" }
```

Nothing happens, and not because of a typo. **That field does not exist in revision 2026-07-28.** The wire models that the Python SDK generates from each revision's schema contain the name `taskSupport` once for 2025-11-25 and zero times for 2026-07-28 (section 12 prints both counts). It belonged to a feature that was removed from the core specification and rebuilt somewhere else, and the somewhere else is the first thing worth understanding, because it is the shape the protocol will grow in from now on.

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

There is also a security default worth internalizing before writing a line: both sides **should** treat any new field an extension introduces as untrusted and validate it comprehensively. An extension is not a trusted channel.

### Where the declaration lives, and where the docs are wrong

Extensions are advertised in an `extensions` field on capabilities, a map from extension identifier to a per-extension settings object. An empty object `{}` means "supported, no settings". [Post 03](../03-wire-protocol/index.md) covered why there is no `initialize` handshake in this revision, and the consequence lands here: the client's declaration goes in `_meta` on **every request**, and the server's goes in the `server/discover` result.

The official pages need careful reading. When this series checked them on 2026-07-26, `/extensions/overview`, SEP-2133 itself, and the client support matrix all still documented negotiation as something that happens during an `initialize` handshake, with a `2025-06-18` protocol revision in the examples. Those pages had not been updated for the stateless revision. The authoritative text is the extension-negotiation section of the specification's *Versioning and compatibility* page, which shows bare `capabilities` objects with no `initialize` wrapper. At that check, the tasks extension was the only extension document rewritten for per-request capabilities.

### Is tasks official? Nobody can say cleanly

A post that glossed over the status of the thing it teaches would be dishonest. Four primary sources disagree.

| Source | What it says |
|---|---|
| `/extensions/overview` | Lists **MCP Tasks** under *Official Extension Repositories* |
| SEP-2663 | Status **Final**, Type **Extensions Track**, which means an accepted official extension |
| The `ext-tasks` repository | Uses the official `ext-` prefix, but its README says "It is **not** an official extension and may change significantly or be discontinued" |
| `/extensions/tasks/overview` | Points at an `experimental-ext-tasks` repository that does not exist |

The specification file itself lives at `specification/draft/tasks.md` in that repository. There is no stable or date-versioned tasks specification directory. The most defensible reading is that tasks is an Extensions Track extension whose SEP is Final and which ships alongside 2026-07-28 as the replacement for core tasks, but whose text is still at draft and whose own repository describes itself as experimental. The wire format should be treated as likely to shift. This post does not pick a side that the sources have not picked; the disagreement was last checked on 2026-07-26, and it is the first thing to recheck on the next revision.

## 2. What a task is for, and the thing that is not a task

Here is a tool that cannot be made fast, because being slow is the point of it. The complete file is [code/05-first-server/src/system_info/progress.py](../../code/05-first-server/src/system_info/progress.py), and it samples central processing unit (CPU) usage once a second for up to thirty seconds:

```python
async def watch_cpu(seconds: int = 5, ctx: Context = None) -> CpuWatch:  # type: ignore[assignment]
    """Sample CPU usage once a second and report the series.

    Args:
        seconds: How long to watch, between 1 and 30.
    """
    seconds = max(1, min(seconds, 30))

    samples: list[CpuSample] = []
    for i in range(seconds):
        # interval=None returns usage since the previous call, which is what we
        # want for a series. asyncio.sleep, not time.sleep: blocking the event
        # loop would stall the transport reader alongside it.
        percent = psutil.cpu_percent(interval=None)
        await asyncio.sleep(1)
        percent = psutil.cpu_percent(interval=None)
        samples.append(CpuSample(second=i + 1, percent=round(percent, 1)))

        if ctx is not None:
            await ctx.report_progress(
                progress=i + 1,
                total=seconds,
                message=f"sampled {i + 1} of {seconds} seconds",
            )
```

Thirty seconds is survivable. Now imagine the same shape around a video render, a full repository index, or a batch job against a warehouse. The request is open the whole time, one specific process is committed to it, and every layer between the client and that process has an opinion about how long a Hypertext Transfer Protocol (HTTP) request may live.

![Two timelines of equal length. The upper one is a single wide bar, one request held open for the whole job. The lower one is a short create exchange, a run of short polls, and a short final exchange returning the completed status.](diagrams/02-sync-vs-task.svg)

*Neither row is faster. The lower one is the one that survives a deploy.*

**Progress reporting and tasks are different mechanisms, and only one of them works everywhere today.** The `ctx.report_progress` call above is core protocol. When the client puts a `progressToken` in the request's `_meta`, the server may emit `notifications/progress` on that request's response stream, and `watch_cpu` does. It needs no extension, and the tool is registered and published like any other. [snippets/progress_today.py](snippets/progress_today.py) connects an in-memory client to the project, reads the listing, and calls the tool for three seconds with a progress callback, which is what makes the SDK client send a `progressToken`:

```text
mcp 2.3.0, Python 3.12.12
tools/list: get_system_info, find_process, terminate_process, watch_cpu
watch_cpu inputSchema properties: {"seconds": {"default": 5, "title": "Seconds", "type": "integer"}}
watch_cpu outputSchema properties: seconds, average_percent, peak_percent, samples

tools/call watch_cpu seconds=3, with a progress callback:
  notifications/progress  1 of 3  sampled 1 of 3 seconds
  notifications/progress  2 of 3  sampled 2 of 3 seconds
  notifications/progress  3 of 3  sampled 3 of 3 seconds
result: resultType complete, isError false, 3 samples, after about 3 seconds
```

What progress reporting does **not** do is change the shape of the exchange. The request is still open. The connection is still held. The work is still one timeout away from being lost, and the server is still telling the client "keep waiting" rather than "come back later". Progress is a courtesy inside a synchronous call; a task is a different call.

There is a sharp corner here. `notifications/progress` and `notifications/message` **must not** be sent on a task's notification stream, and progress is **not supported on tasks at all** in this extension. In 2025-11-25 core tasks, the original `progressToken` persisted into the task and progress notifications kept flowing. That is gone. Turning a tool into a task means giving up progress reporting and replacing it with `statusMessage` plus polling.

## 3. Declaring the extension, and why creation is server-directed

The client declares support in its per-request capabilities. This is the whole handshake, as the extension specification prints it:

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

![A client sending the same capability declaration on two requests, and a server answering one with resultType complete and the other with resultType task. Below, a struck-through tools/list entry carrying an execution taskSupport field, marked as removed.](diagrams/03-extension-negotiation.svg)

*The declaration is identical on both requests. The decision is not.*

Now the part that older material gets wrong. In 2025-11-25, tasks were part of core and the handshake had three moving pieces: a server capability under `tasks.requests.tools.call`, a per-tool `execution.taskSupport` field in `tools/list` with the values `required`, `optional`, or `forbidden`, and a `task` object the client added to the request parameters to opt in. SEP-2663 deleted all three. Its motivation section is worth quoting, because it explains the design and not just the change:

> **The handshake is fragile.** A client that wants to opt into tasks must therefore prime its state with a `tools/list` call before issuing any task-augmented request, and cannot blindly attach a `task` parameter to every request to handle tools isomorphically. This is confusing, implicit, and easy to get wrong.

The replacement is one sentence in the extension specification:

> Task creation is **server-directed**: the client signals support by including the extension in its per-request capabilities, and the server decides on a per-request basis whether to materialize a task.

So: one declaration point, zero per-tool metadata, zero per-request opt-in flags. Each consequence contradicts an intuition somebody has.

- **A client declaring the extension is not requesting a task.** It is saying "I can cope with one if it arrives".
- **The server may return a task in response to any supported request, at its own discretion.** The specification calls it "the sole decider".
- **A server cannot promise in advance.** There is nowhere to put the promise. The same tool may return a normal result at ten in the morning and a task at noon, because the queue got long.
- **Supported methods are `tools/call` only.** Not `resources/read`, not `prompts/get`. The specification notes this is extensible in future revisions.

And the rule that is the cleanest one-line illustration of statelessness in the entire specification:

> A server **MUST NOT** return `CreateTaskResult` to a client that did not include the extension capability on its request, regardless of prior declarations.

That last clause deserves a second reading. It is not enough that the client declared the extension on its last request, or on the request that created the task, or during some notional connection setup. There is no connection setup. Each request stands alone, and a capability the client did not assert on *this* request does not exist as far as *this* request is concerned. A server that caches the client's capabilities in a dictionary keyed by connection is exactly what this sentence forbids.

## 4. Either shape, on any tool call

Because the server decides, a client that declares the extension **must** be prepared to handle either result shape on any `tools/call`. The discriminator is `resultType`, which [post 03](../03-wire-protocol/index.md) introduced and which every result in this revision carries. The extension adds one value to the core set:

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

The field names are `ttlMs` and `pollIntervalMs`, with the `Ms` suffix. In 2025-11-25 they were `ttl` and `pollInterval`. A port that misses this fails silently, because `pollIntervalMs` is optional and a client that finds no such field simply picks its own interval.

Two creation-time rules matter more than they look.

**Durability.** A server **must not** return a `CreateTaskResult` until the task is durably created, meaning until a `tasks/get` for that `taskId` would resolve. In an eventually consistent store the server waits for consistency before answering. This is what lets a client poll immediately without a speculative retry loop, and it is a real constraint on storage design rather than a formality.

**Multi round-trip requests (MRTR) come first.** If a server needs to ask the user something *before* deciding whether to create a task, for example a confirmation, it **should** resolve all of those exchanges synchronously first, and only then answer with a `CreateTaskResult`. Section 8 is about why those two things are not the same mechanism.

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

That is the same split [post 06](../06-tools-in-depth/index.md) drew between a protocol error and a tool execution error, applied to task status. `failed` means the machinery broke. `completed` with `isError: true` means the machinery worked and the tool has bad news. A client that branches on `status == "failed"` to decide whether to show an error shows nothing at all for an ordinary tool failure.

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

**`resultType` here is `"complete"`, not `"task"`.** This trips people because the payload is so obviously about a task. `"task"` is only ever the discriminator on a `CreateTaskResult`; everywhere else the task is just the content of an ordinary complete result. The extension specification's own error-handling examples show `"resultType": "task"` on what they describe as `tasks/get` responses, which contradicts the rule stated a few sections earlier in the same document. Those two examples are errors, not a subtlety.

**Polling discipline.** Clients **should** respect `pollIntervalMs`, and servers **may** rate limit clients that ignore it. Clients **should** keep polling until the task reaches a terminal status or they cancel it. And clients **should** persist task identifiers durably, so a crash or a restart does not orphan work the server is still doing. That last one is the practical difference between a task and an in-process promise: the promise dies with its process.

This is also the answer to the debt [post 04](../04-transports/index.md) left open. Streams are not resumable in this revision, so a broken response stream loses the in-flight request and the client has to re-issue it under a brand new identifier, starting the work again. A task changes what breaking costs. The exchange that dies is a `tasks/get` lasting milliseconds, the work carries on at the server, and the retry is the same poll with the same `taskId`. Resumability came back, one layer up, as an identifier the client stores instead of an event identifier the transport replays.

**Time to live (TTL).** If `ttlMs` is not null, a client **may** treat `createdAt + ttlMs` as a backstop and give up after it. Servers **may** mark a task `failed` any time after the TTL elapses and delete it any time after that, and `ttlMs` itself **may** change over the task's lifetime. A purged task legitimately answers `-32602`, as the specification's example prints it:

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

The server acknowledges with the subset it agreed to, in a `notifications/subscriptions/acknowledged` message. Each `notifications/tasks` payload carries a complete task, identical to what `tasks/get` would have returned at that moment, so there is no follow-up round trip. Clients **may** keep polling as well and need not. Keeping a cheap poll as a backstop is still wise, because that stream is not resumable either: if it drops, the notifications sent meanwhile are simply not delivered, and the `taskId` is the only thing that survives. The notification was called `notifications/tasks/status` in 2025-11-25.

## 7. `tasks/update` and `tasks/cancel`

A task in `input_required` surfaces its questions on every `tasks/get`, in an `inputRequests` map whose shape is the same one [post 08](../08-elicitation-and-mrtr/index.md) used for MRTR: keys are server-assigned identifiers, values are bare request objects with a `method` and `params` and no envelope.

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

The server acknowledges with an empty result and `resultType: "complete"`. Several of the rules around this are counterintuitive.

- **The acknowledgment is eventually consistent.** The task's observable status may not reflect the input yet, so the next poll is no place for an assertion about it.
- **`inputRequests` is a point-in-time snapshot, not a queue.** Every poll re-sends every outstanding request. Clients **should** deduplicate by key, or the user gets the same dialog on every poll.
- **Keys are unique for the whole life of the task.** A server **must not** reuse a key after a response for it was delivered, and **must not** use one key for two distinct questions. This is stronger than the MRTR rule, where keys need only be unique within one request.
- **Partial answers are allowed.** A server **may** accept a subset; the task stays `input_required` until the rest arrive. It **should** ignore responses for keys that were never issued, already answered, or superseded.
- **A task is not a higher-trust channel.** Clients **must** treat each entry exactly as they would the equivalent standalone request: same consent, same rendering, same suspicion. That phrase is the specification's own.

One caution on the example above. The extension specification's worked flow answers an elicitation whose schema declares a `name` property with `"content": {"input": "Luca"}`, keyed by `input` rather than by the schema property. The core MRTR page keys `content` by the schema property, and that is almost certainly the correct form. The example above uses the MRTR form deliberately.

**Cancellation is a request, not a command.** `tasks/cancel` takes a `taskId`, returns an empty acknowledgment, and that is all it promises:

- `notifications/cancelled`, the core cancellation notification, **must not** be used for tasks.
- Cancellation is **cooperative**. The server is obliged to acknowledge, not to stop. Reaching `cancelled` is explicitly not guaranteed, and the task may land in a different terminal status.
- It is eventually consistent, so the status may remain `working` after the acknowledgment.
- Clients **may** discard all task state the moment they send the cancel, and need not poll to confirm.

Cancellation that actually stops work has to be built: the server checks a flag between units of work, or cancels the job it is running, and accepts that anything already committed stays committed. The server of section 10 does the second.

## 8. Tasks against MRTR, and the distinction that trips people up

Both mechanisms produce a result that means "not yet", and both use the same `inputRequests` and `inputResponses` value shapes, so they are easy to conflate. The specification anticipated this and says so directly:

> While task `inputRequests` share structural similarities with multi round-trip requests, they are a distinct mechanism: task `inputRequests` are surfaced via `tasks/get` and fulfilled via `tasks/update`, not via retries of the original method. A server that needs client input *before* returning a `CreateTaskResult` uses the multi round-trip request flow on the original request; a server that needs client input *during* task execution uses the `inputRequests`/`inputResponses` mechanism described here.

|  | MRTR, core | Task `inputRequests`, extension |
|---|---|---|
| When | Before the task exists | During task execution |
| Surfaced on | The original request's result, `resultType: "input_required"` | A `tasks/get` result, `status: "input_required"` |
| Fulfilled by | **Retrying the original request** with a new JSON-RPC id, `inputResponses`, and the echoed `requestState` | **`tasks/update`** with `taskId` and `inputResponses` |
| Server state | Stateless; carried in the opaque `requestState` | Stateful; keyed by `taskId` |
| Key uniqueness | Within one request | Over the whole life of the task |
| Repeats | The server may return `input_required` again on the retry | The server re-sends outstanding requests on every poll |

The practical test is a question about time. If the work cannot even start without the answer, that is MRTR: return `input_required`, get retried, and finish those exchanges before creating the task, as the specification says. If the work is already running and hits something it cannot decide alone, that is a task input request, and the original `tools/call` finished long ago.

## 9. `taskId` is the state handle

Tasks is the worked example of the design SEP-2567 prescribed when it deleted sessions: cross-call state uses explicit, server-minted handles passed as ordinary parameters. The `taskId` **is** that handle. Everything hangs off it, and it is re-sent on every request that touches the task. Four consequences follow, and they are the most operationally important part of this post.

**`tasks/result` and `tasks/list` were both removed.** `tasks/result` existed to hold open a stream so the server could push unsolicited elicitation requests down it, which is illegal now that servers cannot initiate requests at all. `tasks/list` died for a subtler reason, stated plainly in SEP-2663: "While it was possible for tasks to instead be bound to a session, SEP-2567 removes sessions from the protocol. There is no other natural scope a server can define unilaterally." The extension's security considerations then turn the loss into a gain: "Because there is no `tasks/list`, a server cannot inadvertently leak the existence of one caller's tasks to another."

**Task identifier entropy is security-critical.** With no session and possibly no authorization context, the identifier is the only boundary around the task. The specification is direct: a server **may** use task identifiers as bearer tokens for its stored state, and servers **must** generate them with sufficient entropy that a third party cannot enumerate or guess them. A sequential integer is a vulnerability, not an inconvenience.

**`Mcp-Name: <taskId>` replaces sticky sessions.** Over Streamable HTTP, a client **must** set the `Mcp-Name` header to `params.taskId` on `tasks/get`, `tasks/update`, and `tasks/cancel`, so intermediaries can route the request to the instance holding the state, "which is typically required for correctness". `Mcp-Method` carries the method name, per the header conventions [post 04](../04-transports/index.md) covered.

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tasks/get
Mcp-Name: 786512e2-9e0d-44bd-8f29-789f320fe840
```

This is worth pausing on as a piece of protocol design. Sticky routing did not disappear; it became an explicit, inspectable header that a load balancer can read without parsing a body, instead of an implicit cookie nobody could audit.

**The capability must be re-asserted on every task request.** Not only on the initiating `tools/call`, but on `tasks/get`, `tasks/update`, and `tasks/cancel` too. A client that forgets gets a Missing Required Client Capability error whose `data` names exactly what was missing. Here is the demonstration server of section 10 answering a `tasks/get` from a client that did not declare the extension, as [snippets/tasks_by_hand.py](snippets/tasks_by_hand.py) prints it:

```text
== tasks/get from a client that did not declare the extension ==
error -32021: Client did not declare required extension 'io.modelcontextprotocol/tasks'
data: {"requiredCapabilities": {"extensions": {"io.modelcontextprotocol/tasks": {}}}}
```

**Mind the code.** The extension specification prints this error with code `-32003` and the message `Missing required client capability`, in three places. The core 2026-07-28 specification renumbered `MissingRequiredClientCapability` from `-32003` to `-32021`, declared `-32000` through `-32019` legacy, and says new implementations should not use that sub-range at all. The extension text has not caught up. A server emits `-32021`. A client may still meet `-32003` from a server written to the extension text, but the core page says receivers must not assume a meaning for codes in the legacy range, so such a client recognizes the case by the `requiredCapabilities` shape in `data`, not by the number. The Python SDK has already picked a side: the code above comes from its own `require_client_extension` helper, and `mcp_types.jsonrpc.MISSING_REQUIRED_CLIENT_CAPABILITY` is `-32021`. The message text is the SDK's own and differs from the specification's example; the code and the `data` shape are what a client should branch on.

## 10. What the software development kit gives today

Two things are true at once here.

**The Python SDK ships no tasks implementation.** `@mcp.tool()` has no `execution` parameter, `MCPServer` never populates one, and nothing in `mcp==2.3.0` answers `tasks/get`, `tasks/update`, or `tasks/cancel` out of the box. What the types package does ship is the **2025-11-25** feature, labeled as such in its own docstrings. [snippets/sdk_surface.py](snippets/sdk_surface.py) reads it back from the installed packages:

```text
MCPServer.tool parameters: ['self', 'name', 'title', 'description', 'annotations', 'icons', 'meta', 'structured_output']

Task types in mcp_types:
  Task                   Data associated with a task (2025-11-25 only).
  CreateTaskResult       A response to a task-augmented request (2025-11-25 only).
  ToolExecution          Execution-related properties for a tool (2025-11-25 only).
  GetTaskRequest         A request to retrieve the state of a task (2025-11-25 only).
  CancelTaskRequest      A request to cancel a task (2025-11-25 only).
  GetTaskPayloadRequest  A request to retrieve the result of a completed task (2025-11-25 only).
  ListTasksRequest       A request to retrieve a list of tasks (2025-11-25 only).
  UpdateTaskRequest      absent
Task fields: ['task_id', 'status', 'status_message', 'created_at', 'last_updated_at', 'ttl', 'poll_interval']
CreateTaskResult fields: ['meta', 'task']
ClientCapabilities fields: ['experimental', 'sampling', 'elicitation', 'roots', 'extensions', 'tasks']
```

Those field lists read against section 4 tell the story. `ttl` and `poll_interval`, not `ttl_ms` and `poll_interval_ms`. A `task` wrapper, not inlined fields. `GetTaskPayloadRequest` is the deleted `tasks/result`, `ListTasksRequest` the deleted `tasks/list`, and there is no `UpdateTaskRequest` at all. These are the old core types kept for talking to older servers, and using them against a 2026-07-28 peer would produce the wrong messages. `ClientCapabilities` carries both revisions' fields at once, `extensions` for this one and `tasks` for the last.

**The other half: the extension machinery is already there, and it was built with tasks in mind.** `mcp.server.extension` opens with the line "Pluggable extension interface for MCP servers (SEP-2133)", and between the two sides there are seven seams this post uses:

| Seam | What it does |
|---|---|
| `MCPServer(extensions=[...])` and the `Extension` base class | advertises `identifier` under `capabilities.extensions` in `server/discover` |
| `Extension.methods()` returning `MethodBinding` | serves a request method the core schema does not define |
| `Extension.intercept_tool_call` | wraps `tools/call` and may answer instead of the tool |
| `require_client_extension(ctx, identifier)` | raises `-32021` with a `requiredCapabilities` payload |
| `Client(extensions=[...])` and `ClientExtension` | puts the identifier in the per-request `_meta` |
| `ClientExtension.claims()` returning `ResultClaim` | registers a `resultType` outside the core set, plus the resolver that finishes it |
| `Request.name_param` | mirrors a params key into the `Mcp-Name` header on every send |

Two of those docstrings say the quiet part out loud, and the same snippet prints them:

```text
MethodBinding: A new request method an extension serves, e.g. `tasks/get`.
Request.name_param: Wire-params key mirrored into the `Mcp-Name` header on sends; SEP-2663 requires it for tasks/*.
advertise: Advertising an extension you do not implement asserts wire support you do not have; for behavioral extensions construct the real extension instead.
```

The third line is the docstring of the client-side `advertise(identifier)` shortcut, which declares an extension without implementing it, and its warning applies to every extension.

So the extension can be built, and the check fits in one file. [snippets/tasks_extension.py](snippets/tasks_extension.py) puts a server-side extension around the real `watch_cpu`, registered a second time on a demonstration server, wires it to a client-side claim, and runs both ends in one process. The decision lives in the interceptor:

```python
    async def intercept_tool_call(self, params: Any, ctx: Any, call_next: Any) -> Any:
        if not declared_tasks(ctx):
            return await call_next(ctx)  # core behavior, as SEP-2133 requires
        if params.name != "watch_cpu":
            return await call_next(ctx)  # only the slow tool becomes a task
        arguments = dict(params.arguments or {})
        if arguments.get("seconds", 5) < 2:
            return await call_next(ctx)  # the server's choice: short work is answered directly

        task_id = str(uuid.uuid4())  # random, because the id is the access boundary
        stamp = now()
        STORE[task_id] = {
            "taskId": task_id,
            "status": "working",
            "createdAt": stamp,
            "lastUpdatedAt": stamp,
            "ttlMs": 60000,
            "pollIntervalMs": 500,
        }
        RUNNING[task_id] = asyncio.create_task(run(task_id, arguments))
        return {"resultType": "task", **STORE[task_id]}
```

`declared_tasks` reads `ctx.session.client_capabilities`, the capabilities of the request being handled, every time. The SDK's own docstring explains why that attribute and not `client_params`: under 2026-07-28, `client_params` is `None` whenever the request carried no `clientInfo`, which the specification only says a client **should** send, so a check built on it would quietly answer every such client without a task. The interceptor's other branch is the server deciding: it answers a one-second watch directly and makes anything longer a task. The work then runs in `run`, which calls `watch_cpu` without a request context, so the `ctx is not None` branch of section 2 never reports progress, exactly as the extension requires. The run prints this:

```text
server advertises: {'io.modelcontextprotocol/tasks': {}}
tasks client, watch_cpu seconds=1:
  got resultType complete, 1 samples (answered directly)
tasks client, watch_cpu seconds=3:
  statuses seen while polling: working -> completed
  got resultType complete, 3 samples
plain client, watch_cpu seconds=3:
  got resultType complete, 3 samples (no task involved)
```

Those lines are the post in miniature. Same server, same process, same registered tool: the client that declared the extension got a direct answer for one second of work and a task for three, which its claim polled to completion and turned back into an ordinary result, and the client that did not declare it got its answer directly. Nothing was registered conditionally at startup, which is the point. Under statelessness one process serves a task-capable client and a plain one on interleaved requests, so the branch belongs inside the handler.

The file cheats in two ways it admits to: the store is a dictionary and the work is an `asyncio` task in the same process, so the durability rule holds only because nothing else ever reads the store, and a restart loses everything. Nor does the file make a server compliant. It has no `tasks/update`, no `notifications/tasks`, no TTL enforcement, and no `statusMessage`. It is proof that the SDK does not stand in the way, not an implementation.

Tasks was also absent from the published client support matrix when this series checked it on 2026-07-26; the matrix tracked only MCP Apps and the two authorization extensions. This post therefore cannot say which hosts would understand a task if a server sent one, and neither could the documentation.

The practical reading: build the slow tool now, report progress the way section 2 shows, design state around an explicit handle the server mints itself, and keep the extension behind a per-request check until the wire format settles. [Post 24](../24-mcp-apps-and-frontier/index.md) revisits the extension landscape once more of it has settled.

## 11. When a task is the wrong answer

**When the work is short.** A task costs a create, at least one poll, and durable storage. Under a few seconds, that is more machinery and more latency than just answering, which is why the demonstration server answers a one-second watch directly.

**When the only message is "still going".** That is progress reporting, and it works today.

**When the answer is needed to continue a conversation.** A host that has to explain to a user that the result will arrive in four minutes has a user experience problem the protocol cannot solve for it. Sometimes the better tool returns a handle to a job the user can check later through a resource, which is a design [post 07](../07-resources-and-prompts/index.md) would recognize, and which needs no extension.

**When the state cannot be stored durably.** The durability rule is not advisory. A server that cannot guarantee that a `tasks/get` immediately after the create will resolve cannot correctly return a `CreateTaskResult`.

**When the input is needed before the work starts.** That is MRTR, from section 8.

**When the slow work needs a progress bar.** Progress notifications are not supported on tasks. If a percentage bar is the requirement, a task is currently the wrong shape.

## 12. Make it run: a task by hand, and the SDK's surface

Five scripts carry this post. All run from the series root, whose `pyproject.toml` pins `mcp[cli]==2.3.0`, and all except `sdk_surface.py` import the project under [code/05-first-server/](../../code/05-first-server/) in memory, as that project's tests do, with no subprocess and no socket:

```text
uv run python posts/09-tasks/snippets/progress_today.py
uv run python posts/09-tasks/snippets/sdk_surface.py
uv run python posts/09-tasks/snippets/tasks_extension.py
uv run python posts/09-tasks/snippets/tasks_by_hand.py
uv run python posts/09-tasks/snippets/what_can_go_wrong.py
```

The first prints the progress transcript of section 2 in about five seconds; the second prints the SDK's surface of section 10 without starting anything; the third prints the two-client run of section 10 in about nine seconds, because every sample of `watch_cpu` takes a real second. `sdk_surface.py` also prints the count section 1 relied on:

```text
mcp 2.3.0, mcp-types 2.3.0
'taskSupport' in the 2025-11-25 wire models: 1
'taskSupport' in the 2026-07-28 wire models: 0
```

The fourth, `tasks_by_hand.py`, is the lab of this post. It uses the same demonstration server but sends every request itself through the client's session, so no claim resolves anything behind its back, and it prints each result in its wire spelling with the random identifier and the clock replaced by their kinds. Its polling loop is the whole client side of section 6:

```python
        statuses: list[str] = []
        while True:
            got = await poll(session, created.task_id)
            if not statuses or statuses[-1] != got.status:
                statuses.append(got.status)
            if got.status not in ("working", "input_required"):
                break
            await asyncio.sleep(got.poll_interval_ms / 1000)
```

It runs in about five seconds and prints this, of which the last case already appeared in section 9:

```text
== tools/call watch_cpu seconds=2, as a task ==
result: {"resultType": "task", "taskId": "<uuid4>", "status": "working", "createdAt": "<UTC timestamp>", "lastUpdatedAt": "<UTC timestamp>", "ttlMs": 60000, "pollIntervalMs": 500}
statuses: working -> completed
final tasks/get: {"resultType": "complete", "taskId": "<uuid4>", "status": "completed", "createdAt": "<UTC timestamp>", "lastUpdatedAt": "<UTC timestamp>", "ttlMs": 60000, "pollIntervalMs": 500}
result inside it: isError false with 2 samples

== tools/call watch_cpu seconds=30, cancelled mid-flight ==
poll before cancel: working
tasks/cancel result: {"resultType": "complete"}
poll after cancel: cancelled

== tasks/get for a taskId the server never minted ==
error -32602: Failed to retrieve task: Task not found
```

Every rule of sections 4 to 7 that the demonstration implements is visible here. The `CreateTaskResult` carries `resultType: "task"` with the task fields inlined; the `tasks/get` that finds it finished carries `resultType: "complete"` and, beside the line printed, the tool's own result with `isError` false (the client's `GetTaskResult` model accepts only `"complete"` there, so a server that sent `"task"` would fail validation). The cancellation is acknowledged with an empty complete result, and the task reaches `cancelled` only because this server chooses to stop the job; the specification would allow it to keep working. An identifier the server never minted earns `-32602` with the message the specification's example uses, which the demonstration copies. The task identifier is a random version-4 universally unique identifier (UUID), which the script checks before masking it.

The two result shapes a client must accept are already in the third script's output: one `tools/call` from the same client answered as a complete result, another as a task.

## 13. What can go wrong?

Two of these are triggered on purpose by `snippets/what_can_go_wrong.py`; the third is a design fault the demonstration server is written to avoid.

**A 2025-11-25 tutorial is followed literally.** The decorator has no `execution` parameter, so the server fails at import rather than publishing anything:

```text
== a per-tool execution setting, pasted from a 2025-11-25 tutorial ==
TypeError: MCPServer.tool() got an unexpected keyword argument 'execution'
```

**The work is run out of band through the server's own `call_tool`.** It looks like the natural way to run a registered tool in the background, but a tool that reports progress needs a request to report it on, and a background job has none:

```text
== watch_cpu run through MCPServer.call_tool with no request behind it ==
UnexpectedToolError: Error executing tool watch_cpu
  caused by ValueError: Context is not available outside of a request
```

The model would see only `Error executing tool watch_cpu`, because under `mcp==2.3.0` an exception other than `ToolError` is treated as a crash and its text stays on the server. The demonstration calls the function itself instead, with `ctx` left at `None`, and the same script shows that path returning a `CpuWatch` with one sample.

**A background job dies without recording it.** If `run` let an exception escape, the asyncio task would end, nothing would ever set a terminal status, and every later `tasks/get` would still answer `working`. A client that polls until the status is terminal, as section 6 says it should, would then poll until `ttlMs` ran out, or forever if `ttlMs` is null. That is why `run` in `tasks_extension.py` catches every exception and records `failed` with a `-32603` error, the internal-error code the extension names for this case, and why a client should treat `createdAt + ttlMs` as the backstop the specification allows.

## Common pitfalls

1. **Teaching or writing `execution.taskSupport`.** It was the 2025-11-25 mechanism, SEP-2663 deleted it, there is no replacement per-tool field, and the SDK's 2026-07-28 wire models never mention it.
2. **Caching the client's capability declaration against the connection.** A server **must not** return a task to a client that did not declare the extension on that request, "regardless of prior declarations"; it reads `_meta` fresh every time, including on `tasks/get`, `tasks/update`, and `tasks/cancel`.
3. **Branching on `status == "failed"` to detect a failed tool.** A tool that returns `isError: true` produces a `completed` task with the error inside `result`, and `failed` is reserved for JSON-RPC faults.
4. **Expecting `resultType: "task"` from `tasks/get`.** It is `"complete"`; `"task"` appears exactly once per task, on the `CreateTaskResult`, whatever the extension specification's own error examples show.
5. **Treating `tasks/cancel` as a stop button.** It is cooperative: the server acknowledges, may keep working, and the task may reach a terminal status other than `cancelled`.
6. **Sequential or guessable task identifiers.** With no session, the identifier is the access boundary and may serve as a bearer token, so it needs real entropy.

## Further reading

- Tasks extension specification, `ext-tasks` repository, `specification/draft/tasks.md`. Every normative rule quoted here: capability negotiation, the lifecycle, `tasks/get`, `tasks/update`, `tasks/cancel`, notifications, and the security considerations.
- SEP-2663, *Move Tasks from core into an extension*, status Final, Extensions Track. The motivation quoted in section 3, and the rationale for removing `tasks/result` and `tasks/list`.
- SEP-2133, *The Extensions framework*, status Final. The definition in section 1, the three tiers, the naming rule, and the fallback requirement.
- SEP-2567, *Sessionless MCP*. Why cross-call state is a server-minted handle, of which `taskId` is the worked example.
- Model Context Protocol specification, *Versioning and compatibility*, revision 2026-07-28, the extension-negotiation section. The authoritative shape of an `extensions` declaration, as against the stale `initialize` examples on the extensions overview page.
- Model Context Protocol specification, *Progress* and *Subscriptions*, revision 2026-07-28. `progressToken` and `notifications/progress`, the mechanism section 2 uses, and the `subscriptions/listen` request that `notifications/tasks` rides on.
- Extensions index and Extension Support Matrix. The status table of section 1 and the absence of tasks from the matrix.
- MCP Python SDK, release 2.3.0. The extension seams and the types every transcript in sections 2, 9, 10, 12, and 13 was printed against.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 10 - Building your own MCP client](../10-mcp-client/index.md):** the client side of these exchanges: one `Client` over three transports, results read by `isError` rather than by exceptions, and the `input_required` loop driven from the client, which is where a task-capable client would add its polling.
- **[Post 24 - MCP Apps, extensions, and where the protocol goes next](../24-mcp-apps-and-frontier/index.md):** the rest of the extension landscape, including the one extension that is unambiguously stable, and what promotion into core would mean for tasks.
