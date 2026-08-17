# 15 · Project 2 · A DevOps first responder

> **TL;DR.** Debugging a Kubernetes cluster is almost entirely reading, correlating, and summarizing, which is exactly the shape of work a read-only Model Context Protocol (MCP) server plus a model is good at. This post builds one: eight tools that list, describe, read logs, and diagnose, with the read/write split enforced by the import graph rather than by an annotation. Two details carry most of the value, and neither is obvious: every call into the synchronous Kubernetes client is pushed onto a worker thread, and every log read is bounded in two dimensions so a pod that has been crashing for two days cannot flood the model's context.
>
> **After reading this you will be able to:**
> - Call a synchronous client library from an `async` tool without freezing the transport.
> - Correlate pod phase, container status, and events into one verdict a human can check.
> - Bound a log read in lines and in bytes, keep the end that matters, and report what was dropped.
> - Make a server's read-only claim structural instead of advisory.

![A left-to-right pipeline in four stages. On the left, a model asks a question in natural language. The second stage is a set of read-only tools that each make one Kubernetes API call on a worker thread. The third stage collects three separate sources for the same pod: its phase, its per-container status including the previous state, and the events recorded against it. The fourth stage is a pure analysis function that correlates all three into a single verdict with a confidence level, a list of findings that each name the API field they came from, and the next call worth making. A dashed boundary runs down the diagram showing that nothing in the pipeline can write to the cluster.](diagrams/01-diagnostic-pipeline.svg) *Four stages, one direction, and no path back to a write.*

---

## 1. The brief

A service is failing. Here is what an engineer types, in roughly this order:

```bash
kubectl get pods
kubectl describe pod checkout-3c-aaa
kubectl logs checkout-3c-aaa --previous
kubectl get events --sort-by=.lastTimestamp
kubectl rollout history deployment/checkout
```

Every one of those commands reads. Not one of them changes anything. The hard part is not running them, it is holding five screens of output in your head at once and noticing which two disagree: the pod says `Running`, the container says `CrashLoopBackOff`, and the exit code that explains both is in a third place neither of them showed you.

That is a correlation problem over structured data, and it is the single thing a model plus a well-shaped tool surface is genuinely good at. So this project builds the reading half of a first responder, and [Post 16](../16-devops-remediation/index.md) builds the half that can change something.

The complete server is in [code/15-devops-responder/](../../code/15-devops-responder/). It has eleven tools across both posts, and eight of them only read. These are those eight, exactly as they appear in `tools/list`:

| Tool | Arguments | What it answers |
|---|---|---|
| `list_pods` | `namespace` | What is running, and how many pods are unhappy |
| `describe_pod` | `pod_name`, `namespace` | Everything about one pod, from all three sources at once |
| `get_events` | `namespace`, `event_type`, `limit` | What the cluster has been doing lately |
| `get_logs` | `pod_name`, `namespace`, `container`, `tail_lines`, `previous`, `max_bytes` | A bounded slice of a container's output |
| `list_deployments` | `namespace` | Which deployments have fewer replicas ready than desired |
| `diagnose_pod` | `pod_name`, `namespace` | Why one pod is broken, with the evidence |
| `find_crash_loops` | `namespace`, `limit` | Every unhealthy pod in a namespace, each diagnosed |
| `get_rollout_history` | `deployment_name`, `namespace` | The revisions you could go back to |

Two of those are worth pausing on before any code. `diagnose_pod` is `describe_pod` with the reasoning already done, which is a different product decision than it looks: it moves the correlation from the model's context window into a function you can unit test. `get_rollout_history` is the odd one out: it only reads, so it carries the same annotations as everything else in the table, but it lives in the writing module because its only real use is deciding what [Post 16](../16-devops-remediation/index.md) should roll back to. The diagnoses in section 7 point at it by name.

## 2. What goes over the wire, before any Python

A tool call in revision 2026-07-28 is one JavaScript Object Notation Remote Procedure Call (JSON-RPC) request with a `name`, an `arguments` object, and a mandatory `_meta` block. There is no `initialize` handshake and no session, so every request carries its own protocol revision and its own declared capabilities. Asking what is running looks like this:

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

The answer carries `structuredContent`, which is the part the model actually reasons over. This is a real result, captured from the server running against the test fixture in [tests/conftest.py](../../code/15-devops-responder/tests/conftest.py), trimmed to two of its four pods:

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
      "age": "14h",
      "node": "node-1",
      "reason": "CrashLoopBackOff"
    },
    {
      "name": "checkout-3c-bbb",
      "namespace": "default",
      "phase": "Running",
      "ready": "1/1",
      "restarts": 0,
      "age": "14h",
      "node": "node-1",
      "reason": ""
    }
  ]
}
```

Read the summary line. `running: 4` and `unhealthy: 1` are both true at the same time, because a pod in a crash loop is in phase `Running`. If you only summarize by phase you will report a healthy namespace while one of its services is down. That contradiction is the whole subject of section 5, and it shows up in the very first tool.

On the wire the field is `structuredContent`; the Python software development kit (SDK) exposes it as `structured_content`. Every tool in this server returns a dataclass, and the SDK derives an `outputSchema` from it. [Post 06](../06-tools-in-depth/index.md) covered the silent failure that makes this worth asserting on: a class whose attributes are only assigned inside `__init__` has no type hints for the SDK to read, so `outputSchema` comes out `null`, nothing raises, and the tool ships an object's memory address to the model. Every result class here uses class-body annotations, and a test checks all eleven.

## 3. Talking to Kubernetes from Python

Now the code, and the first real trap.

**The official `kubernetes` client is synchronous.** Every method on `CoreV1Api` is a blocking Hypertext Transfer Protocol (HTTP) request. Your tool is `async def`. Put the two together carelessly and you get this:

```python
@mcp.tool()
async def list_pods(namespace: str = "default") -> PodList:
    result = core_v1.list_namespaced_pod(namespace=namespace)   # blocks the event loop
```

That line runs on the event loop thread. For as long as the application programming interface (API) server takes to answer, the transport is frozen: no second tool call, no progress notification, no cancellation, nothing. On a healthy cluster you will never notice. On the cluster you actually care about, the one where the API server is under load because something is wrong, you will notice a great deal.

The whole of the fix is one function in [cluster.py](../../code/15-devops-responder/src/k8s_responder/cluster.py):

```python
async def call(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run one blocking Kubernetes API call on a worker thread."""
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

**Use `asyncio.to_thread`, not `get_event_loop().run_in_executor`.** The older spelling, which the previous edition of this post used and which most tutorials still show, is `asyncio.get_event_loop().run_in_executor(None, partial(fn, **kwargs))`. Three things are wrong with it. `get_event_loop()` is deprecated when called from a coroutine. It returns the wrong object when no loop is running, and in a threaded context it can quietly create a second one. And it does not forward keyword arguments, which is why every example wraps the call in `functools.partial`. `to_thread` has none of those problems.

**The rule is asserted, not documented.** The fakes in `conftest.py` record the thread name of every API call they receive, so the property becomes an ordinary test:

```python
async def test_kubernetes_calls_run_on_a_worker_thread(fake):
    async with Client(mcp) as c:
        await c.call_tool("list_pods", {})
        await c.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})
        await c.call_tool("list_deployments", {})

    assert fake.recorder.calls, "the fake cluster was never called"
    assert "MainThread" not in fake.recorder.threads()
```

That is the difference between a convention and a guarantee. Somebody will add a tool in six months and call `conn.core_v1.list_namespaced_pod(...)` directly because it is one line shorter, and this test is what tells them.

Connecting is the other job in that module, and the order matters. In-cluster service account credentials are tried first, and a kubeconfig context is the fallback. When the server runs as a pod, the service account is the only correct answer, and falling through to a developer's kubeconfig inside a cluster would be surprising in exactly the way you do not want infrastructure tooling to be surprising. Credentials load on the first tool call rather than at startup, so a missing kubeconfig produces a readable tool error instead of a server that refuses to launch and tells the host nothing.

## 4. Read-only by construction

Every tool in this post is annotated `read_only_hint=True`. That annotation does nothing.

It is worth being precise about how little it does, because the previous edition of this post said these tools were "safe, no approval dialog needed", and that is not what the specification says. `ToolAnnotations` carries this note:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide a faithful description of tool behavior (including descriptive properties like `title`). Clients should never make tool use decisions based on `ToolAnnotations` received from untrusted servers.

And the tools page adds that clients **must** consider annotations untrusted unless they come from trusted servers. So `readOnlyHint` does not stop a tool from writing, does not suppress a host's approval dialog, and is not a permission. It is metadata a user interface may use as it sees fit, and a host is free to prompt on every call or on none.

What actually makes these tools read-only is that they contain no writing code. The package splits along that line:

| Module | Job | Can it write? |
|---|---|---|
| `app.py` | the `MCPServer` instance and stderr logging | no |
| `cluster.py` | credentials, the `to_thread` wrapper, limits | no |
| `inspect.py` | listing, describing, events, logs | no |
| `diagnose.py` | crash-loop analysis | no |
| `remediate.py` | restart, scale, rollback (Post 16) | yes, and only here |

`inspect.py` and `diagnose.py` import `cluster` for the connection and the `list_namespaced_*` and `read_namespaced_*` calls, and nothing else. There is no `delete_`, no `patch_`, and no `create_` anywhere in either file, so there is no code path from either module to a change in the cluster, whatever any annotation claims. That is checkable too, and it is checked:

```python
def test_the_read_modules_contain_no_mutating_api_calls():
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

Grepping your own source in a test looks crude the first time you see it. It is also the only form of this check that cannot be defeated by a refactor, because it does not care how the call is spelled or which helper wraps it.

The same reasoning is why there is no `--read-only` flag. The writing tools exist because [`__init__.py`](../../code/15-devops-responder/src/k8s_responder/__init__.py) imports `remediate`. Delete that one line and the capability is gone from the process, which is a stronger guarantee than any switch parsed at runtime.

None of this is the real control. The real control is role-based access control (RBAC): the service account the server runs under. A minimal role for this post grants `get` and `list` on `pods`, `pods/log`, and `events` in the core API group, and on `deployments` and `replicasets` in `apps`. If the server has no `delete` verb, no bug in this code can delete anything. [Post 19](../19-security/index.md) is the general version of that argument, and [Post 13](../13-database-analyst/index.md) made it about database roles.

## 5. Where a crash loop actually shows up

Here is the correlation problem, concretely. One pod, three sources, and no single one of them tells you what is wrong.

![Three stacked panels showing the same failing pod from three different parts of the Kubernetes API. The first panel is the pod status, which reports phase Running, which looks fine. The second panel is the container status inside that pod, which reports state waiting with reason CrashLoopBackOff and a restart count of seven, which says there is a loop but not why. The third panel is the previous container state, terminated with exit code 1, and alongside it the events recorded against the pod, a BackOff warning seen forty-two times. An arrow joins all three into a single verdict box at the bottom. Annotations mark what each source alone would let you conclude, and each of the first two alone is wrong.](diagrams/02-where-a-crashloop-shows-up.svg) *Read any one of the three and stop, and you report something that is true and useless.*

**The pod phase says `Running`.** That is not a bug in Kubernetes. The pod exists, it is scheduled, and it has a container that keeps being started, so `Running` is the honest answer to the question "is this pod alive". It is the wrong question.

**The container status says `waiting`, reason `CrashLoopBackOff`.** This is where most tutorials stop, and it is a restatement of the symptom. `CrashLoopBackOff` means "the kubelet is waiting before trying again", which you knew.

**The previous container state has the exit code.** `lastState.terminated.exitCode` is the one field that says anything about the cause, and it describes an instance of the container that no longer exists. This is the single most-missed field in Kubernetes debugging, and it is why `get_logs` has a `previous` parameter at all.

**The events say how the kubelet got there.** They are the cluster narrating itself: scheduling decisions, image pulls, probe failures, back-off timers. They also expire, one hour by default, so an empty list means "nothing recently", not "nothing ever".

`describe_pod` returns all four in one result: `phase`, `events`, and a `containers` list whose entries carry each container's current *and* previous state side by side. The part worth copying is `container_states()`, which flattens `status.containerStatuses` and then appends `status.initContainerStatuses`:

```python
statuses = list(pod.status.container_statuses or []) if pod.status else []
statuses += list(pod.status.init_container_statuses or []) if pod.status else []
```

A pod that never gets past its init container looks identical to a healthy pod if you only read `containerStatuses`. That is two lines to avoid an entire category of wrong answer, and there is a test named `test_init_containers_are_analysed_too` that fails if either is removed.

One more piece of defensive shape. Events are a separate RBAC resource from pods, so a service account can perfectly well be allowed one and not the other. Losing events should degrade a diagnosis, not fail it:

```python
    except ClusterError:
        # Event access is a separate RBAC verb from pod access. Losing events is
        # a degraded diagnosis, not a failed one.
        return []
```

## 6. Logs, and the size problem

A pod that has been restarting for two days can hold hundreds of megabytes of logs. The naive tool hands all of it to the model. This is the failure the project exists to avoid, and one bound is not enough to avoid it.

![A diagram in two halves. The left half shows a container's full log as a tall column, with a small window at its bottom end marked as what tail_lines selects from the API server, and an even smaller window inside that marked as what max_bytes keeps for the model, illustrating that the two bounds act on different quantities: one bounds the wire, the other bounds the context window. The right half shows two ways of cutting that window down to size side by side. The upper one keeps the oldest end and is marked as wrong, because the stack trace that explains the failure is at the newest end and has been thrown away. The lower one keeps the newest end and snaps the cut up to the nearest newline, and is marked as correct, with the dropped byte and line counts reported back in the result.](diagrams/03-log-truncation.svg) *`tail_lines` bounds the wire. `max_bytes` bounds the context. They are not the same bound.*

**`tail_lines` bounds what crosses the wire.** It is the API server's own parameter, and it selects a number of lines from the end of the log. It says nothing about size.

**`max_bytes` bounds what reaches the model.** This matters because a single line of structured JSON logging can be several kilobytes on its own. Five hundred lines is a reasonable-sounding request that can be five megabytes of context.

So `get_logs` takes both, clamps both against configured maxima, and reports what happened:

```python
    cfg = limits()
    tail_lines = clamp(tail_lines, 1, cfg.max_tail_lines)
    max_bytes = clamp(max_bytes, 1_000, cfg.max_log_bytes)
```

Clamping rather than rejecting is deliberate. A model that asks for a hundred thousand lines is not being malicious, it just does not know the limit. Silently doing the sensible thing beats an error it has to recover from, as long as the result says what was actually done, and it does: `lines_requested` comes back as the clamped value.

**Why the API's own `limitBytes` is not used.** The Kubernetes client documents it like this:

> If set, the number of bytes to read from the server before terminating the log output. This may not display a complete final line of logging, and may return slightly more or slightly less than the specified limit.

Read that against `tailLines`. The API server selects the tail, then reads bytes forward from the beginning of that selection and stops. The bytes it drops are the newest ones, which are the only ones that ever explain a crash, and it will happily stop in the middle of a line. Both halves of that are the opposite of what you want, so the byte bound is applied locally instead, in `_tail_bytes`:

```python
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0, 0

    window = encoded[-max_bytes:]
    newline = window.find(b"\n")
    if newline != -1 and newline + 1 < len(window):
        window = window[newline + 1 :]
```

Three decisions in seven lines. The slice is taken from the **end**, because that is where the failure is. The cut is then moved forward to the first newline, so the first line the model sees is a whole line: a fragment reads like a different error than it is, and a model will confidently reason about a stack trace that was cut in half. And the slicing happens on bytes with the boundary fixed afterward, because slicing a `str` gives you no control over the byte size at all while slicing bytes blindly can split a multi-byte character.

Here is the result of asking for five hundred lines of a log with a two-kilobyte ceiling, captured from a real run:

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

Two smaller things in the same tool. A 404 becomes a sentence in `note` with an empty `logs` string rather than an error, because "that pod does not exist" is an answer. And a 400 on a `previous=True` read becomes the sentence "No previous instance of this container has logs. The container has either never restarted, or its previous log was already rotated away", which is the difference between a tool that helps and a tool that returns `ApiException: (400)`.

## 7. Diagnosing, as a pure function

The analysis in [diagnose.py](../../code/15-devops-responder/src/k8s_responder/diagnose.py) is one function, `analyse(pod, events)`, that touches no network. The tools are thin wrappers that fetch and then call it. That split is worth the small amount of plumbing it costs: the interesting logic is the correlation, and correlation is testable without a cluster, a server, or an event loop. All twenty-four tests in [tests/test_diagnose.py](../../code/15-devops-responder/tests/test_diagnose.py) call it directly, and they run in 0.06 seconds.

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

**The order is specificity, not severity.** `_check_oom` runs before `_check_crash_loop` because a pod being killed for memory reports *both*. It has a `CrashLoopBackOff` waiting reason and an `OOMKilled` previous state, and only one of those is actionable. Report the loop and you send the reader to the application logs; report the kill and you send them to `resources.limits.memory`, which is where the answer is. There is a test called `test_oom_beats_crashloop` whose entire job is to pin that ordering.

**Exit codes are translated, not repeated.** A number is not a diagnosis. The module carries a small table of the ones worth explaining, including the `128+N` codes that are a signal number in disguise: 137 is SIGKILL, which means the out-of-memory killer or a liveness probe that stopped waiting; 139 is a segmentation fault; 127 means the entrypoint is not in the image. Unknown codes say so rather than guessing.

The most interesting entry in that table is zero:

```python
    0: (
        "the process exited successfully, which under restartPolicy Always is "
        "still a restart; a container whose main process finishes is a crash "
        "loop as far as Kubernetes is concerned"
    ),
```

which comes with a comment worth reading twice, because it describes a bug this code does not have and most code does:

```python
    # `_EXIT_CODES.get(code or -1)` would be a bug: exit code 0 is falsy, and 0
    # is the one code most worth explaining, because "it exited successfully"
    # reads like good news right up until you notice it is in a restart loop.
```

**Two failure shapes that look like something else.** A container between back-off windows is genuinely `running`, so a check that only looks for the `CrashLoopBackOff` waiting reason reports the pod as fine. The fallback catches it on restart count plus a non-zero previous exit code, and drops the confidence to `medium`. And a pod that is `Pending` is usually not broken at all, it is downloading an image, so `Pending` on its own is a low-confidence `pending` verdict, while `Pending` plus a `FailedScheduling` event is a high-confidence `unschedulable` one with the scheduler's own message quoted back.

**Every finding names the field it came from.** This is the part that makes a machine-written diagnosis checkable by a human:

```python
@dataclass
class Finding:
    source: str
    signal: str
    detail: str
```

Here is a real `diagnose_pod` result against the fixture, with the prose fields trimmed:

```json
{
  "pod": "checkout-3c-aaa",
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

Every `source` there is a path you can check with one `kubectl` command. That is the difference between a diagnosis a human can verify and one they have to trust, and on infrastructure the second kind is worth very little.

`next_steps` is the other half. It names the exact next call, with the arguments filled in, including `previous=True`, which is the argument a model will otherwise omit. A tool that tells the model what to do next is cheaper than a tool that makes the model work it out.

And when nothing matches, the verdict is `unknown` with confidence `low` and the sentence "Not determined from pod status, container status, or events." A wrong confident answer costs more than an honest empty one, especially at the point in an incident where somebody is deciding whether to page a second person.

## 8. Running it

The suite needs no cluster:

```bash
cd code/15-devops-responder && PYTHONPATH=src pytest tests -q
```
```
........................................................................ [ 92%]
......                                                                   [100%]
78 passed in 1.34s
```

Seventy-eight tests across both posts, in under two seconds, with no Kubernetes anywhere. `tests/conftest.py` is a fake `CoreV1Api` and `AppsV1Api` pair, installed through `cluster.use()`, and two decisions make it worth having. The fakes return **real `kubernetes.client` model objects**, `V1Pod` and `V1ContainerStatus` and `CoreV1Event`, so the nested-optional shape the production code has to survive is the shape it is tested against; a dict-shaped fake would let `pod["status"]["phase"]` pass in the suite and fail against a cluster. And they raise **real `ApiException`s**, so the 404 and 400 branches are exercised with the exception the real client throws.

Nothing in the package type-checks the API objects, which is exactly why the substitution works. [Post 12](../12-testing-and-debugging/index.md) has the general pattern, including the reason every test here opens its client with `async with` inside the test body instead of taking it from a yield fixture.

To run the server against a real cluster:

```bash
uv run python -m k8s_responder                    # stdio, what a desktop host spawns
uv run python -m k8s_responder --context staging  # pick a kubeconfig context
```

Under stdio, standard output is the protocol channel. A stray `print()` anywhere in the package would be parsed as a JSON-RPC frame and break the connection in a way that names neither `print` nor your tool. All logging goes to standard error, configured on the first lines of `app.py`. [Post 04](../04-transports/index.md) has the full anatomy of that failure.

## 9. When this is the wrong tool

Three honest limits.

**This is not monitoring.** Every tool here is a point-in-time read, taken when somebody asks. It has no history, no alerting, and no idea what normal looks like for your cluster. It answers "what is wrong right now", which is a question you ask after your monitoring has already told you something is wrong.

**It cannot see inside your application.** Exit code 1 with an unhelpful log tail is where this server runs out of things to say, and it says so rather than inventing a cause. Metrics, traces, and profiles live in systems this does not touch.

**It is a poor fit for very large namespaces.** `list_pods` returns every pod in the namespace, and a namespace with four hundred pods is a large result to put in a context window regardless of how tidy the rows are. `find_crash_loops` is the answer to that: it scans everything, describes only what is unhealthy, and caps the diagnoses it returns. It also reads the namespace's events once rather than once per pod, which is two API calls for a whole namespace instead of two per pod, and there is a test that counts them.

---

## Common pitfalls

- **Calling a synchronous client from an `async` tool.** The `kubernetes` package blocks, and on the event loop it freezes the entire transport until the API server answers. Route every call through one `asyncio.to_thread` helper, and assert on the thread name in a test rather than trusting the next person to remember.
- **Reaching for `asyncio.get_event_loop().run_in_executor`.** It is the spelling most tutorials still show. `get_event_loop()` is deprecated inside a coroutine, returns the wrong thing with no loop running, and can create a second loop in a threaded context. It also forces a `functools.partial` because it will not forward keyword arguments.
- **Treating `readOnlyHint` as enforcement.** It is a hint, the specification says clients must consider annotations untrusted, and it neither prevents a write nor suppresses a prompt. What makes a module read-only is that it contains no writing code, and what makes a service account read-only is RBAC.
- **Bounding a log by lines alone.** Five hundred lines of structured JSON logging is megabytes. Bound the bytes separately, keep the newest end, snap the cut to a line boundary, and report the dropped counts so the model knows it is looking at a window.
- **Using the API's `limitBytes` together with `tailLines`.** It reads forward from the start of the selected tail and stops, so it discards the newest lines, which are the only ones that explain a crash, and it may cut mid-line while doing it.
- **Reading the pod phase and stopping.** A crash-looping pod is in phase `Running`. The reason is in the container's waiting state, the cause is in its *previous* terminated state, and the sequence is in the events. Any single one of those alone produces a confident wrong answer.
- **Ignoring init containers.** A pod wedged in its init container has an empty `containerStatuses` list and looks perfectly healthy. Read `initContainerStatuses` too.
- **Writing `codes.get(exit_code or -1)`.** Exit code 0 is falsy, and it is the code most worth explaining, because a container whose main process finishes cleanly restarts forever under `restartPolicy: Always`.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `ToolAnnotations` note quoted in section 4, and the requirement that clients treat annotations as untrusted. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Kubernetes Python client. <https://github.com/kubernetes-client/python>. The synchronous API surface this project wraps, and the source of the `limitBytes` description in section 6.
- Kubernetes documentation, *"Debug Running Pods"*. The manual version of the correlation in section 5, including `--previous` and the container state fields. <https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/>
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026). <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
- MCP Python SDK, `mcp==2.0.0b2`. Every result and every transcript in this post came from this version driving [code/15-devops-responder/](../../code/15-devops-responder/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 16 — Project 2 · Safe remediation with approval](../16-devops-remediation/index.md)**: the other half of this server, where three tools can change the cluster and none of them does so until a human has seen the exact change.
- **[Post 14 — Project 1 · Writes, transactions, and an audit trail](../14-database-writes/index.md)**: the same read-then-write progression applied to a database, and the audit trail this project does not have.
- **[Post 12 — Testing and debugging MCP](../12-testing-and-debugging/index.md)**: the in-memory client pattern that lets seventy-eight tests exercise a Kubernetes server with no Kubernetes.
