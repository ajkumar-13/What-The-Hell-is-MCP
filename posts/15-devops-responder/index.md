# 15 - Project 2: A DevOps first responder

> **TL;DR.** Debugging a Kubernetes cluster is almost entirely reading, correlating, and summarizing, which is exactly the shape of work a read-only Model Context Protocol (MCP) server plus a model is good at. This post builds one: eight tools that list, describe, read logs, and diagnose, with the read/write split enforced by the import graph rather than by an annotation. Two details carry most of the value, and neither is obvious: every call into the synchronous Kubernetes client is pushed onto a worker thread, and every log read is bounded in two dimensions so a pod that has been crashing for two days cannot flood the model's context. Every result, transcript and error message below is printed by a script in `snippets/` against the project's fake cluster, or by the project's own test suite.
>
> **Prerequisites:** [Post 12](../12-testing-and-debugging/index.md).
> **Safe to skip?** Skip it if the reader can already keep a synchronous client library off an `async` tool's event loop, explain why a crash-looping pod reports phase `Running`, and bound a tool's output in bytes as well as in lines.
>
> **After reading, you will be able to:**
>
> - Call a synchronous client library from an async tool through one wrapper that keeps the transport free and turns its errors into messages a model can read.
> - Correlate pod phase, container status, and events into one verdict a human can check.
> - Bound a log read in lines and in bytes, keep the end that matters, and report what was dropped.
> - Make a server's read-only claim structural instead of advisory.

![A pipeline in four numbered stages, left to right. A person asks the host why checkout is failing, and the host's model sends one tools/call request across a dashed protocol boundary. The second stage lists five read tools, each making one blocking Kubernetes call through a single helper onto a worker thread. The third holds four cards for the same failure: the pod phase Running, the waiting reason CrashLoopBackOff, the previous terminated state with exit code 1, and a BackOff event seen 42 times. The fourth is the verdict, crash_loop at high confidence, with findings that name the field each came from and the next call to make. A band along the bottom states that read-only is structural: neither read module contains a delete, patch, create or replace call.](diagrams/01-diagnostic-pipeline.svg)

*Four stages, one direction, and no path back to a write.*

---

## 1. The question: why is cluster debugging a job for a read-only server?

A service is failing. Here is what an engineer types, in roughly this order:

```bash
kubectl get pods
kubectl describe pod checkout-3c-aaa
kubectl logs checkout-3c-aaa --previous
kubectl get events --sort-by=.lastTimestamp
kubectl rollout history deployment/checkout
```

Every one of those commands reads. Not one of them changes anything. The hard part is not running them; it is holding five screens of output in mind at once and noticing which two disagree: the pod says `Running`, the container says `CrashLoopBackOff`, and the exit code that explains both is in a third place neither of them showed.

That is a correlation problem over structured data, and it is the kind of work a model plus a well-shaped tool surface is good at. So this project builds the reading half of a first responder, and [post 16](../16-devops-remediation/index.md) builds the half that can change something. The question this post answers is why that reading half is the right job for a read-only MCP server, and what makes "read-only" a property of the code rather than a promise in its metadata.

The complete server is in [code/15-devops-responder/](../../code/15-devops-responder/), and every path below that starts with `src/` or `tests/` is relative to it. It has eleven tools across both posts, and eight of them only read. `snippets/tool_surface.py` lists them from `tools/list` and prints `11 tools, 8 with readOnlyHint true, 11 with an outputSchema`. These are the eight:

| Tool | Arguments | What it answers |
|---|---|---|
| `list_pods` | `namespace` | What is running, and how many pods are unhappy |
| `describe_pod` | `pod_name`, `namespace` | Everything about one pod, from all three sources at once |
| `get_events` | `namespace`, `event_type`, `limit` | What the cluster has been doing lately |
| `get_logs` | `pod_name`, `namespace`, `container`, `tail_lines`, `previous`, `max_bytes` | A bounded slice of a container's output |
| `list_deployments` | `namespace` | Which deployments have fewer replicas ready than desired |
| `diagnose_pod` | `pod_name`, `namespace` | Why one pod is broken, with the evidence |
| `find_crash_loops` | `namespace`, `limit` | Every unhealthy pod in a namespace, each diagnosed |
| `get_rollout_history` | `deployment_name`, `namespace` | The revisions a rollback could go back to |

Only `pod_name` and `deployment_name` are required; every other argument has a default. Two of those tools are worth pausing on before any code. `diagnose_pod` is `describe_pod` with the reasoning already done, which is a different product decision than it looks: it moves the correlation from the model's context window into a function that can be unit tested. `get_rollout_history` is the odd one out: it only reads, so it carries the same annotations as everything else in the table, but it lives in the writing module, `remediate.py`, because its only real use is deciding what [post 16](../16-devops-remediation/index.md) should roll back to. The diagnoses in section 7 point at it by name.

## 2. What goes over the wire, before any Python

A tool call in revision 2026-07-28 is one JavaScript Object Notation Remote Procedure Call (JSON-RPC) request with a `name`, an `arguments` object, and a `_meta` object that must carry `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` and should carry `io.modelcontextprotocol/clientInfo`. There is no `initialize` handshake and no session, so every request carries its own protocol revision and its own declared capabilities. `snippets/wire_by_hand.py` starts the real package as a standard input and output (stdio) server with the fake cluster of `tests/conftest.py` installed in place of a real one, and writes this request to it as one line of JavaScript Object Notation (JSON), laid out here over several:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_pods",
    "arguments": { "namespace": "default" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

The result that comes back has five keys, `_meta`, `content`, `isError`, `resultType` and `structuredContent`, and carries the data twice: as `structuredContent`, and serialized as JSON in a single text block, which the Tools page says a tool that returns structured content should also send for backwards compatibility. The `_meta` names the server, `{"io.modelcontextprotocol/serverInfo": {"name": "k8s-responder", "version": ""}}`, with an empty version because `app.py` passes none. Here is the `structuredContent`, trimmed by the script to two of its four pods:

```json
{
  "namespace": "default",
  "total": 4,
  "running": 4,
  "unhealthy": 1,
  "pods": [
    {
      "name": "checkout-3c-aaa",
      "namespace": "default",
      "phase": "Running",
      "ready": "0/1",
      "restarts": 7,
      "age": "74d",
      "node": "node-1",
      "reason": "CrashLoopBackOff"
    },
    {
      "name": "checkout-3c-bbb",
      "namespace": "default",
      "phase": "Running",
      "ready": "1/1",
      "restarts": 0,
      "age": "74d",
      "node": "node-1",
      "reason": ""
    }
  ]
}
```

`age` is measured from the fixture's fixed creation time, 2026-07-26 10:30 UTC (Coordinated Universal Time), to the moment of the run, so it grows with the date; everything else is the same on every run.

Read the summary line. `running: 4` and `unhealthy: 1` are both true at the same time, because a pod in a crash loop is in phase `Running`. A summary by phase alone reports a healthy namespace while one of its services is down. That contradiction is the whole subject of section 5, and it shows up in the very first tool.

On the wire the field is `structuredContent`; the Python software development kit (SDK) exposes it as `structured_content`. Every tool in this server returns a dataclass, and the SDK derives an `outputSchema` from it. [Post 06](../06-tools-in-depth/index.md) covered the silent failure that makes this worth asserting on: a class whose attributes are only assigned inside `__init__` has no type hints for the SDK to read, so `outputSchema` comes out `null`, nothing raises, and the tool ships an object's memory address to the model. Every result class here uses class-body annotations, and the test `test_every_tool_publishes_an_output_schema` checks all eleven tools.

## 3. Talking to Kubernetes from Python

Now the code, and the first real trap.

**The official `kubernetes` client is synchronous.** Every method on `CoreV1Api` is a blocking Hypertext Transfer Protocol (HTTP) request, and a tool is `async def`. `snippets/event_loop.py` puts the two together carelessly, on a throwaway server, against a fake application programming interface (API) that takes half a second to answer:

```python
@mcp.tool()
async def list_pods(namespace: str = "default") -> PodList:
    result = core_v1.list_namespaced_pod(namespace=namespace)   # blocks the event loop
```

That line runs on the event loop thread. For as long as the API server takes to answer, the transport is frozen: no second tool call, no progress notification, no cancellation, nothing. The script sends two concurrent calls to that tool and to the project's real `list_pods`, over an in-memory client that shares one event loop with the server as a stdio server shares one with its transport, while a heartbeat task tries to run every 20 milliseconds:

```
1. two concurrent list_pods calls, fake API takes 0.5 s per call
   called on the event loop:       1.1 s in total, heartbeat waited up to 1147 ms
   through cluster.call (a thread): 0.5 s in total, heartbeat waited up to 33 ms
```

On the event loop the two calls ran one after the other and nothing else ran at all for more than a second; through the project's wrapper they overlapped and the loop stayed responsive. The milliseconds vary by machine; the shape does not. On a healthy cluster the difference is invisible. On the cluster that matters, the one whose API server is under load because something is wrong, it is the difference between a server that answers and one that hangs.

The whole of the fix is one function in `src/k8s_responder/cluster.py`:

```python
async def call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run one blocking Kubernetes API call on a worker thread.

    This is the single chokepoint. If a call to the cluster does not go through
    here it is running on the event loop, and the transport is frozen for as
    long as the API server takes to answer.
    """
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ApiException as exc:
        raise ClusterError(
            f"Kubernetes API error {exc.status} {exc.reason}".strip(),
            status=exc.status or 0,
            reason=exc.reason or "",
        ) from exc
    except urllib3.exceptions.HTTPError as exc:
        raise ClusterError(f"Could not reach the Kubernetes API: {exc}") from exc
```

Every tool in the package reaches the cluster through that one function and no other. Being a single chokepoint is what makes the next three properties cheap: error translation happens once, the `ApiException` type stops leaking into tool code, and the threading rule is checkable.

**The error it raises is one the model can read.** `ClusterError` is declared at the top of the same file, and its base class is the SDK's `ToolError`:

```python
class ClusterError(ToolError):
    """Anything the Kubernetes API refused to do, in a shape tools can read.

    It subclasses the SDK's `ToolError`, the exception for a failure the tool
    anticipated: the message reaches the model in an `isError` result and the
    server logs it without a traceback. Any other exception is a crash, and the
    model then sees only `Error executing tool <name>`.

    `status` carries the HTTP status when there was one, so callers can branch
    on 404 without importing the kubernetes exception type everywhere.
    """

    def __init__(self, message: str, status: int = 0, reason: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
```

That base class is the SDK's contract for refusals, which [post 06](../06-tools-in-depth/index.md) set out: under `mcp==2.3.0` a `ToolError` raised in a tool comes back as a result with `isError: true` whose text carries the message, and the server logs one line at INFO; any other exception is treated as a crash, the model reads only `Error executing tool <name>`, and the traceback goes to the server's log at ERROR. `snippets/refusals.py` asks `describe_pod` for a pod that does not exist, and the model gets a sentence it can act on:

```
isError: true
text:   Error executing tool describe_pod: No pod named 'does-not-exist' in namespace 'default'.
```

The same contract covers credentials. They load on the first tool call rather than at startup, on a worker thread, and a machine with neither a service account nor a kubeconfig gets a refusal that says so (section 10 prints it) instead of a server that refuses to launch and tells the host nothing.

**Use `asyncio.to_thread`, not `run_in_executor`.** The older spelling, still common in examples, is `asyncio.get_event_loop().run_in_executor(None, partial(fn, **kwargs))`. `run_in_executor` forwards positional arguments only, which is why every example built on it wraps the call in `functools.partial`; `to_thread` forwards both kinds. The same script prints the difference:

```
   asyncio.to_thread(add, 1, b=2) -> 3
   loop.run_in_executor(None, add, 1, b=2) -> TypeError: BaseEventLoop.run_in_executor() got an unexpected keyword argument 'b'
   inside a coroutine, asyncio.get_event_loop() is asyncio.get_running_loop(): True
```

Fetching the loop with `get_event_loop()` is the other half of the old spelling. Inside a coroutine it does no harm, but it does nothing more than `asyncio.get_running_loop()`, which says what it means; `to_thread` needs neither, and it is the spelling `cluster.py` uses.

**The rule is asserted, not documented.** The fakes in `tests/conftest.py` record the thread name of every API call they receive, so the property becomes an ordinary test in `tests/test_server.py`:

```python
async def test_kubernetes_calls_run_on_a_worker_thread(fake):
    """The kubernetes client is synchronous. On the loop it freezes the transport.

    A blocking HTTP call made directly from an `async def` tool stops
    everything: no other tool call, no progress notification, no cancellation,
    until the API server answers. `cluster.call` exists to make that impossible,
    and this asserts it rather than trusting it.
    """
    async with Client(mcp) as c:
        await c.call_tool("list_pods", {})
        await c.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})
        await c.call_tool("list_deployments", {})

    assert fake.recorder.calls, "the fake cluster was never called"
    assert "MainThread" not in fake.recorder.threads()
```

`snippets/event_loop.py` makes the same three calls and prints what the recorder saw: four API calls (`describe_pod` reads the pod and then its events), on threads named `['asyncio_0', 'asyncio_1']`, and `'MainThread' among them: False`. That is the difference between a convention and a guarantee. Somebody will add a tool in six months and call `conn.core_v1.list_namespaced_pod(...)` directly because it is one line shorter, and this test is what tells them.

Connecting is the other job in that module, and the order matters. In-cluster service account credentials are tried first, and a kubeconfig context is the fallback. When the server runs as a pod, the service account is the only correct answer, and falling through to a developer's kubeconfig inside a cluster would be surprising in exactly the way infrastructure tooling should never be.

## 4. Read-only by construction

Every tool in the table of section 1 is annotated `read_only_hint=True`. That annotation does nothing.

It is worth being precise about how little it does. `ToolAnnotations` carries this note, as the specification's Tools page prints it:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide a faithful description of tool behavior (including descriptive properties like `title`). Clients should never make tool use decisions based on `ToolAnnotations` received from untrusted servers.

And the same page adds that clients **must** consider annotations untrusted unless they come from trusted servers. So `readOnlyHint` does not stop a tool from writing, does not suppress a host's approval dialog, and is not a permission. It is metadata a user interface may use as it sees fit. Whether to ask the user before a call is the host's decision, under the same page's rule that clients should prompt for confirmation on sensitive operations, and a hint from a server the host does not trust cannot settle it.

What actually makes these tools read-only is that they contain no writing code. The package splits along that line:

| Module | Job | Can it write? |
|---|---|---|
| `app.py` | the `MCPServer` instance and stderr logging | no |
| `cluster.py` | credentials, the `to_thread` wrapper, limits | no |
| `inspect.py` | listing, describing, events, logs | no |
| `diagnose.py` | crash-loop analysis | no |
| `remediate.py` | restart, scale, rollback ([post 16](../16-devops-remediation/index.md)) | yes, and only here |

`inspect.py` and `diagnose.py` import `cluster` for the connection and make only `list_namespaced_*` and `read_namespaced_*` calls. There is no `delete_`, no `patch_`, and no `create_` anywhere in either file, so there is no code path from either module to a change in the cluster, whatever any annotation claims. That is checkable too, and `tests/test_server.py` checks it:

```python
def test_the_read_modules_contain_no_mutating_api_calls():
    """The read/write split is structural, and this is the proof.

    Not a naming convention and not a review habit: there is no call in either
    module that could change the cluster, so there is no path from a read tool
    to a mutation regardless of what any annotation claims.
    """
    for module in (inspect_module, diagnose_module):
        source = pyinspect.getsource(module)
        for verb in (
            "delete_namespaced",
            "patch_namespaced",
            "create_namespaced",
            "replace_namespaced",
        ):
            assert verb not in source, f"{module.__name__} can call {verb}"
```

`snippets/tool_surface.py` runs the same search over all three tool modules, and the third line is the one that makes the first two mean something:

```
mutating API verbs in each module's source:
  inspect.py    none
  diagnose.py   none
  remediate.py  delete_namespaced, patch_namespaced
```

Searching a project's own source in a test looks crude the first time. It is also the form of this check that a refactor cannot quietly route around: it does not care which tool or helper the call sits in, only whether the module can make it at all.

The same reasoning is why there is no `--read-only` flag. The writing tools exist because [`src/k8s_responder/__init__.py`](../../code/15-devops-responder/src/k8s_responder/__init__.py) imports `remediate`. Delete that one line and the capability is gone from the process, which is a stronger guarantee than any switch parsed at runtime.

None of this is the real control. The real control is role-based access control (RBAC): the service account the server runs under. A minimal role for this post grants `get` and `list` on `pods`, `pods/log`, and `events` in the core API group, and on `deployments` and `replicasets` in `apps`. If the server's role has no `delete` verb, no bug in this code can delete anything. [Post 19](../19-security/index.md) is the general version of that argument, and [post 13](../13-database-analyst/index.md) made it about database roles.

## 5. Where a crash loop actually shows up

Here is the correlation problem, concretely. One pod, three sources, and no single one of them says what is wrong.

![Three stacked panels for one failing pod, checkout-3c-aaa, each pairing what a part of the Kubernetes API says with what that source alone lets a reader conclude. The pod status says phase Running, which is true and useless. The container status carries two rows: the waiting reason CrashLoopBackOff with a restart count of seven, which is the symptom, and the previous terminated state with exit code 1, the one row marked as the cause. The events show a BackOff seen 42 times, the sequence rather than the cause. An arrow joins all three into a verdict box: crash_loop at high confidence, and a next call that reads the previous instance's log.](diagrams/02-where-a-crashloop-shows-up.svg)

*Read any one of the three and stop, and the report is true and useless.*

**The pod phase says `Running`.** That is not a bug in Kubernetes. The pod exists, it is scheduled, and it has a container that keeps being started, so `Running` is the honest answer to the question "is this pod alive". It is the wrong question.

**The container status says `waiting`, reason `CrashLoopBackOff`.** Stopping here is tempting, and it only restates the symptom. `CrashLoopBackOff` means "the kubelet is waiting before trying again", which was already obvious.

**The previous container state has the exit code.** `lastState.terminated.exitCode` is the one field that says anything about the cause, and it describes an instance of the container that no longer exists. It is also the easiest field to miss, and it is why `get_logs` has a `previous` parameter at all.

**The events say how the kubelet got there.** They are the cluster narrating itself: scheduling decisions, image pulls, probe failures, back-off timers. They also expire, one hour by default, so an empty list means "nothing recently", not "nothing ever".

`describe_pod` returns all of it in one result: `phase`, `events`, and a `containers` list whose entries carry each container's current *and* previous state side by side. The part worth copying is `container_states()` in `src/k8s_responder/inspect.py`, which flattens `status.containerStatuses` and then appends `status.initContainerStatuses`:

```python
    statuses = list(pod.status.container_statuses or []) if pod.status else []
    statuses += list(pod.status.init_container_statuses or []) if pod.status else []
```

A pod that never gets past its init container looks like a pod that is merely starting up if only `containerStatuses` is read. `snippets/diagnosis.py` builds one, phase `Pending`, its main container waiting with `PodInitializing` and its init container `wait-for-db` in `CrashLoopBackOff`, and diagnoses it twice:

```
   init container looping, read fully    crash_loop (high)
   the same pod, init statuses unread    pending (low)
```

Without the second line the verdict is the reassuring one, "usually an image still downloading". That is two lines to avoid an entire category of wrong answer, and the test `test_init_containers_are_analyzed_too` in `tests/test_diagnose.py` fails if the second one is removed.

One more piece of defensive shape. Events are a separate RBAC resource from pods, so a service account can perfectly well be allowed one and not the other. Losing events should degrade a diagnosis, not fail it, so the helper that fetches a pod's events in `inspect.py` ends:

```python
    except ClusterError:
        # Event access is a separate RBAC verb from pod access. Losing events is
        # a degraded diagnosis, not a failed one.
        return []
```

## 6. Logs, and the size problem

A pod that has been restarting for two days can hold megabytes of logs. The naive tool hands all of it to the model. This is the failure the project exists to avoid, and one bound is not enough to avoid it.

![On the left, a container's whole log drawn as a tall column from oldest at the top to newest at the bottom, with two nested windows at its newest end: the outer one is what tail_lines selects from the API server, which bounds the wire, and the inner one is what max_bytes keeps, which bounds the model's context window. On the right, two panels compare two ways of cutting that window down. The upper one keeps the oldest end, as the API's own limitBytes does when tailLines is set, and is marked wrong because the traceback at the newest end is thrown away. The lower one keeps the newest end and moves the cut forward to the first newline, and is marked correct. A band at the bottom gives the counters of a real run: 1,979 bytes returned, 31,020 bytes and 470 lines dropped, reported rather than silently discarded.](diagrams/03-log-truncation.svg)

*`tail_lines` bounds the wire. `max_bytes` bounds the context. They are not the same bound.*

**`tail_lines` bounds what crosses the wire.** It is the API server's own parameter, and it selects a number of lines from the end of the log. It says nothing about size.

**`max_bytes` bounds what reaches the model.** This matters because a single line of structured JSON logging can be several kilobytes on its own. Five hundred lines is a reasonable-sounding request that can be megabytes of context.

So `get_logs` in `src/k8s_responder/inspect.py` takes both, clamps both against configured maxima, and reports what happened:

```python
    cfg = limits()
    tail_lines = clamp(tail_lines, 1, cfg.max_tail_lines)
    max_bytes = clamp(max_bytes, 1_000, cfg.max_log_bytes)
```

Clamping rather than rejecting is deliberate. A model that asks for a hundred thousand lines is not being malicious; it just does not know the limit. Silently doing the sensible thing beats an error it has to recover from, as long as the result says what was actually done, and it does. `snippets/bounded_logs.py` asks for `tail_lines=100000` and `max_bytes=1000000000`:

```
   lines_requested in the result: 500
   tail_lines the API received:   500
   configured maxima: max_tail_lines=500, max_log_bytes=40000
```

**Why the API's own `limitBytes` is not used.** The Kubernetes client documents it like this:

> If set, the number of bytes to read from the server before terminating the log output. This may not display a complete final line of logging, and may return slightly more or slightly less than the specified limit.

Read that against `tailLines`. The tail is selected first, and the byte limit then counts forward from the beginning of that selection and stops. The bytes it drops are the newest ones, which are the only ones that ever explain a crash, and it may stop in the middle of a line. Both halves of that are the opposite of what a diagnosis needs. That reading comes from the documentation; the fake cluster does not implement `limitBytes`, so nothing in this post measures it. The byte bound is applied locally instead, in `_tail_bytes`:

```python
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0, 0

    window = encoded[-max_bytes:]
    newline = window.find(b"\n")
    if newline != -1 and newline + 1 < len(window):
        window = window[newline + 1 :]
```

Three decisions in seven lines. The slice is taken from the **end**, because that is where the failure is. The cut is then moved forward to the first newline, so the first line the model sees is a whole line: a fragment reads like a different error than it is, and a model will confidently reason about a stack trace that was cut in half. And the slicing happens on bytes with the boundary fixed afterward, because slicing a `str` gives no control over the byte size at all, while a cut at an arbitrary byte can split a multi-byte UTF-8 (Unicode Transformation Format, 8-bit) character; a cut just after a newline never does. The same script cuts a 10,948-byte log that ends in a traceback both ways, to 2,000 bytes:

```
   oldest end kept: contains KeyError=False, last line kept='{"level": "i'
   newest end kept: contains KeyError=True, first line kept='{"level": "info", "seq": 165, "msg": "request served"}'
```

Here is the result of asking the real tool for five hundred lines of a 32,999-byte, 500-line log with a 2,000-byte ceiling, the log the suite's `test_get_logs_caps_bytes_and_says_how_much_it_dropped` uses, as `snippets/bounded_logs.py` prints the counters:

```json
{
  "pod": "checkout-3c-aaa",
  "lines_requested": 500,
  "lines_returned": 30,
  "bytes_returned": 1979,
  "truncated": true,
  "dropped_bytes": 31020,
  "dropped_lines": 470,
  "note": "Truncated to the most recent 2000 bytes. 470 older line(s) and 31020 byte(s) were dropped from the start of this slice. Raise max_bytes or lower tail_lines to see a different window."
}
```

The `note` field is doing real work. A model handed a silently shortened log will reason about it as though it were complete. Told that 470 lines are missing from the front, it can ask for a different window, or say it cannot tell. Being explicitly incomplete is more useful than being quietly wrong.

Two smaller things in the same tool. A 404 becomes a sentence in `note` with an empty `logs` string rather than an error, because "that pod does not exist" is an answer: the script's `get_logs` for a pod named `ghost` comes back with `isError=false` and the note `No pod named 'ghost' in namespace 'default'.` And a 400 on a `previous=True` read becomes the sentence "No previous instance of this container has logs. The container has either never restarted, or its previous log was already rotated away.", which is the difference between a tool that helps and a tool that answers with nothing but an HTTP status.

## 7. Diagnosing, as a pure function

The analysis in [src/k8s_responder/diagnose.py](../../code/15-devops-responder/src/k8s_responder/diagnose.py) is one function, `analyze(pod, events)`, that touches no network. The tools are thin wrappers that fetch and then call it. That split is worth the small amount of plumbing it costs: the interesting logic is the correlation, and correlation is testable without a cluster, a server, or an event loop. All twenty-four tests in [tests/test_diagnose.py](../../code/15-devops-responder/tests/test_diagnose.py) work that way, handing synthetic pods straight to `analyze()`, and `uv run --extra dev pytest tests/test_diagnose.py` runs them in a few hundredths of a second.

The function runs ten checks in order and returns on the first match:

```python
    checks = (
        _check_terminating,
        _check_oom,
        _check_image,
        _check_config,
        _check_unschedulable,
        _check_crash_loop,
        _check_probe_failure,
        _check_failed_phase,
        _check_restarting,
        _check_pending,
    )
```

**The order is specificity, not severity.** `_check_oom` runs before `_check_crash_loop` because a pod being killed for memory reports *both*: a `CrashLoopBackOff` waiting reason and an `OOMKilled` previous state, and only one of those is actionable. Reporting the loop sends the reader to the application logs; reporting the kill sends them to `resources.limits.memory`, which is where the answer is. `snippets/diagnosis.py` builds such a pod and prints `oom_killed (high)` for it, with the findings `phase, CrashLoopBackOff, OOMKilled, restarts`; the test `test_oom_beats_crashloop` pins that ordering.

**Exit codes are translated, not repeated.** A number is not a diagnosis. The module carries a small table of the ones worth explaining, including the `128+N` codes that are a signal number in disguise: 137 is SIGKILL, which means the out-of-memory (OOM) killer or a liveness probe that gave up waiting; 139 is a segmentation fault; 127 means the entrypoint is not in the image. Unknown codes say so rather than guessing.

The most interesting entry in that table is zero:

```python
    0: (
        "the process exited successfully, which under restartPolicy Always is "
        "still a restart; a container whose main process finishes is a crash "
        "loop as far as Kubernetes is concerned"
    ),
```

which comes with a comment in `_check_crash_loop` worth reading twice, because it describes a bug this code does not have and much code does:

```python
    # `_EXIT_CODES.get(code or -1)` would be a bug: exit code 0 is falsy, and 0
    # is the one code most worth explaining, because "it exited successfully"
    # reads like good news right up until you notice it is in a restart loop.
```

**Two failure shapes that look like something else.** A container between back-off windows is genuinely `running`, so a check that only looks for the `CrashLoopBackOff` waiting reason reports the pod as fine. The fallback catches it on restart count plus a non-zero previous exit code, and drops the confidence to `medium`. And a pod that is `Pending` is usually not broken at all, it is downloading an image, so `Pending` on its own is a low-confidence `pending` verdict, while `Pending` plus a `FailedScheduling` event is a high-confidence `unschedulable` one with the scheduler's own message quoted back. The script prints all three:

```
   running again between back-offs       crash_loop (medium)
   Pending, no events                    pending (low)
   Pending plus FailedScheduling         unschedulable (high)
```

**Every finding names the field it came from.** This is the part that makes a machine-written diagnosis checkable by a human:

```python
@dataclass
class Finding:
    """One observation, and the exact field it came from.

    Naming the source is the difference between a diagnosis a human can check
    and one they have to trust. Every finding here can be verified with a single
    `kubectl` command against the field named.
    """

    source: str
    signal: str
    detail: str
```

Here is the complete `diagnose_pod` result against the fixture, as `snippets/diagnosis.py` prints it:

```json
{
  "pod": "checkout-3c-aaa",
  "namespace": "default",
  "healthy": false,
  "verdict": "crash_loop",
  "confidence": "high",
  "summary": "Container app is in a crash loop.",
  "likely_cause": "Container app has restarted 7 time(s). Its previous instance exited with code 1, which is a generic application error; the log tail almost always names it.",
  "restart_count": 7,
  "findings": [
    { "source": "status.phase", "signal": "phase", "detail": "Running" },
    { "source": "status.containerStatuses[app].state.waiting", "signal": "CrashLoopBackOff", "detail": "back-off 5m0s restarting" },
    { "source": "status.containerStatuses[app].lastState.terminated", "signal": "Error", "detail": "previous instance exited with code 1" },
    { "source": "status.containerStatuses[app].restartCount", "signal": "restarts", "detail": "7" },
    { "source": "events", "signal": "BackOff", "detail": "Back-off restarting failed container app in pod checkout-3c-aaa (seen 42 time(s))" }
  ],
  "next_steps": [
    "get_logs(pod_name='checkout-3c-aaa', container='app', previous=True) is the log that matters; the current instance has usually produced nothing yet",
    "get_events(namespace='default', event_type='Warning')",
    "If the exit code appeared right after a deploy, compare against get_rollout_history"
  ]
}
```

Every `source` there is a path that one `kubectl` command can check. That is the difference between a diagnosis a human can verify and one they have to trust, and on infrastructure the second kind is worth very little.

`next_steps` is the other half. It names the exact next call, with the arguments filled in, including `previous=True`, which is the argument a model will otherwise omit. A tool that tells the model what to do next is cheaper than a tool that makes the model work it out.

And when nothing matches, the verdict is `unknown` with confidence `low` and the sentence "Not determined from pod status, container status, or events.", which the script prints for a running container that is not ready and matches no signature. A wrong confident answer costs more than an honest empty one, especially at the point in an incident where somebody is deciding whether to page a second person.

## 8. When this is the wrong tool

Three honest limits.

**This is not monitoring.** Every tool here is a point-in-time read, taken when somebody asks. It has no history, no alerting, and no idea what normal looks like for a given cluster. It answers "what is wrong right now", which is a question to ask after the monitoring has already said something is wrong.

**It cannot see inside the application.** Exit code 1 with an unhelpful log tail is where this server runs out of things to say, and it says so rather than inventing a cause. Metrics, traces, and profiles live in systems this does not touch.

**It is a poor fit for very large namespaces.** `list_pods` returns every pod in the namespace, and a namespace with four hundred pods is a large result to put in a context window regardless of how tidy the rows are. `find_crash_loops` is the answer to that: it scans everything, diagnoses only what is unhealthy, and caps the diagnoses it returns. It also reads the namespace's events once rather than once per pod, so a whole namespace costs two API calls; `snippets/diagnosis.py` prints `API calls: 2 (list_namespaced_pod, list_namespaced_event)` for the fixture, and the test `test_find_crash_loops_reads_the_namespace_events_once` counts them.

## 9. Make it run: the project and the scripts behind this post

The project is the post's code, and its suite needs no cluster. From inside `code/15-devops-responder/`:

```bash
uv sync --extra dev                                 # once: the environment, with the test tools
uv run --extra dev pytest                           # 78 passed, no Kubernetes anywhere
uv run python -m k8s_responder                      # stdio, what a desktop host spawns
uv run python -m k8s_responder --context staging    # pick a kubeconfig context
```

```
tests\test_diagnose.py ........................                          [ 30%]
tests\test_server.py ................................................... [ 96%]
...                                                                      [100%]

============================= 78 passed in 1.72s ==============================
```

Seventy-eight tests across both posts, with no Kubernetes anywhere; the count is the result, and the seconds vary by machine. `tests/conftest.py` is a fake `CoreV1Api` and `AppsV1Api` pair, installed through `cluster.use()`, and two decisions make it worth having. The fakes return **real `kubernetes.client` model objects**, `V1Pod` and `V1ContainerStatus` and `CoreV1Event`, so the nested-optional shape the production code has to survive is the shape it is tested against; a dict-shaped fake would let `pod["status"]["phase"]` pass in the suite and fail against a cluster. And they raise **real `ApiException`s**, so the 404 and 400 branches are exercised with the exception the real client throws. Nothing in the package type-checks the API objects, which is exactly why the substitution works. [Post 12](../12-testing-and-debugging/index.md) has the general pattern, including the reason every test here opens its client with `async with` inside the test body instead of taking it from a yield fixture.

Six scripts under `snippets/` produce every result, transcript and error message quoted above. Each runs from the series root with no arguments, in a few seconds. The root environment pins `mcp[cli]==2.3.0` and the Kubernetes client, and each script imports the package and the fixture of `tests/conftest.py` from the project's `src/` and `tests/` directories. None needs a cluster, a host, or any network, and each stops whatever it started before it exits.

```bash
uv run python posts/15-devops-responder/snippets/tool_surface.py   # sections 1 and 4: tools/list, the mutating verbs per module
uv run python posts/15-devops-responder/snippets/wire_by_hand.py   # section 2: one tools/call over stdio, by hand
uv run python posts/15-devops-responder/snippets/event_loop.py     # section 3: the frozen loop, to_thread, the worker threads
uv run python posts/15-devops-responder/snippets/refusals.py       # sections 3 and 10: ToolError, missing credentials, a crash
uv run python posts/15-devops-responder/snippets/bounded_logs.py   # section 6: two bounds, clamping, the newest end
uv run python posts/15-devops-responder/snippets/diagnosis.py      # sections 5, 7 and 8: verdicts from the fixture and synthetic pods
```

`tool_surface.py` prints the versions it ran with: `mcp 2.3.0, kubernetes 36.0.3, Python 3.12.12`. `wire_by_hand.py` is both halves of its experiment: with no argument it is the client, and it starts itself again with `--serve` as the stdio server, a flag that is not meant to be run by hand.

Every transcript in this post comes from that fake cluster. To see the same verdict on a real one, point a kubeconfig context at a local cluster, create a pod whose command exits with code 1, wait for the kubelet to put it into `CrashLoopBackOff`, and ask a host connected to the server to diagnose it; `get_logs` with a small `max_bytes` then shows the dropped-bytes report against real output.

## 10. What can go wrong?

Three failures reach the model, and `snippets/refusals.py` prints each of them.

**A refusal that is not a `ToolError`.** `ClusterError` subclasses `ToolError` on purpose. Declared as a plain `Exception` subclass, the same refusal from the same tool reaches the model with its message removed:

```
== the same refusal raised as a plain Exception subclass ==
isError: true
text:   Error executing tool describe_pod
logged: ERROR mcp.server.mcpserver.server: Tool 'describe_pod' raised an unexpected exception (with a traceback)
```

The model learns only that something failed, so it guesses or retries the same call. Any new error type in the package, or any exception a helper lets escape, has the same symptom; catch what can be predicted and raise `ClusterError` (or `ToolError`) with the sentence written out.

**No credentials, on the first call.** A machine with no service account and no kubeconfig starts the server without complaint, because credentials load lazily, and the first tool call says what is missing:

```
== list_pods with no credentials anywhere ==
isError: true
text:   Error executing tool list_pods: No Kubernetes credentials. This server needs either an in-cluster service account or a kubeconfig file. Underlying error: Invalid kube-config file. No configuration found.
```

The Kubernetes client looks for the file named by `KUBECONFIG`, or `~/.kube/config` when that is unset. A desktop host does not inherit the environment a terminal builds from the shell profile, the trap [post 05](../05-first-server/index.md) describes for `PATH`, so a `KUBECONFIG` exported there may never reach the server; a kubeconfig at the default location, chosen with `--context`, does not depend on it.

**A role that cannot read events.** The diagnosis still succeeds, which is the design, but it is quieter than it looks:

```
== describe_pod while the cluster forbids reading events (403) ==
isError: false
events: [], containers: 1
logged: (nothing at INFO or above)
```

An empty `events` list reads as "nothing happened recently" when it means "this server was not allowed to look", and nothing in the log says which. When a diagnosis has no event findings at all, check the role for `list` on `events` before trusting the silence.

One more mistake never reaches the model. Under stdio, standard output is the protocol channel. Once `mcp.run()` is serving, the SDK points the process's standard output at standard error, so a flushed `print()` in a tool lands harmlessly there; but a `print()` that runs at import, before the transport starts, goes onto the pipe and the client fails to parse it, and an unflushed one inside a tool reaches the pipe at exit. All logging goes to standard error, configured on the first lines of `app.py`. [Post 04](../04-transports/index.md) has the full anatomy of that failure.

## Common pitfalls

1. **Calling a synchronous client from an `async` tool.** On the event loop it freezes the entire transport until the API server answers; route every call through one `asyncio.to_thread` helper, and assert on the thread name in a test.
2. **Treating `readOnlyHint` as enforcement.** It is a hint the specification says clients must treat as untrusted; what makes a module read-only is that it contains no writing code, and what makes a service account read-only is RBAC.
3. **Bounding a log by lines alone.** Five hundred lines of structured JSON logging can be megabytes; bound the bytes separately, keep the newest end, snap the cut to a line boundary, and report the dropped counts.
4. **Using the API's `limitBytes` together with `tailLines`.** It counts forward from the start of the selected tail, so it discards the newest lines, the only ones that explain a crash, and may cut mid-line.
5. **Reading the pod phase and stopping.** A crash-looping pod is in phase `Running`; the reason is in the container's waiting state, the cause in its *previous* terminated state, and init containers keep their own statuses in `initContainerStatuses`.
6. **Writing `codes.get(exit_code or -1)`.** Exit code 0 is falsy, and it is the code most worth explaining, because a container whose main process finishes cleanly restarts forever under `restartPolicy: Always`.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `ToolAnnotations` note quoted in section 4, the requirement that clients treat annotations as untrusted, and the text block a tool with structured content should also send. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Hungerford, O., Morrow, S., and Chang, L., *"Tool Annotations as Risk Vocabulary: What Hints Can and Can't Do"* (2026-03-16), Model Context Protocol blog. What the annotation hints can and cannot do, the theme of section 4. <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
- Kubernetes Python client. The synchronous API surface this project wraps, and the source of the `limitBytes` description in section 6. <https://github.com/kubernetes-client/python>
- Kubernetes, *"Debug Running Pods"*. The manual version of the correlation in section 5, including `--previous` and the container state fields. <https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/>
- MCP Python SDK, release 2.3.0. `MCPServer`, `ToolError`, and the in-memory `Client` every script and test here uses. <https://github.com/modelcontextprotocol/python-sdk>
- [code/15-devops-responder/](../../code/15-devops-responder/). The complete project, with the tests run in section 9.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 16 - Project 2: Safe remediation with approval](../16-devops-remediation/index.md):** the other half of this server, where three tools can change the cluster and none of them does so until a human has seen the exact change.
- **[Post 14 - Project 1: Writes, transactions, and an audit trail](../14-database-writes/index.md):** the same read-then-write progression applied to a database, with the audit trail this project does not have.
