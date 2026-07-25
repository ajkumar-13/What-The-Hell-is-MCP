# k8s-responder

The Kubernetes first responder built across posts 15 and 16. It reads a cluster,
explains why a pod is broken, and can restart, scale, or roll back once a human
approves the exact change.

| File | What it is | Post |
|---|---|---|
| `src/k8s_responder/app.py` | the server instance, stderr logging | [15](../../posts/15-devops-responder/index.md) |
| `src/k8s_responder/cluster.py` | credentials, the `asyncio.to_thread` wrapper, blast-radius limits | [15](../../posts/15-devops-responder/index.md) |
| `src/k8s_responder/inspect.py` | read-only tools, including bounded log reading | [15](../../posts/15-devops-responder/index.md) |
| `src/k8s_responder/diagnose.py` | crash-loop analysis, as a pure function | [15](../../posts/15-devops-responder/index.md) |
| `src/k8s_responder/remediate.py` | restart, scale, rollback, each behind an approval | [16](../../posts/16-devops-remediation/index.md) |

## Requirements

Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), and a kubeconfig or an
in-cluster service account. The test suite needs none of those last two.

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`, the pre-release that
implements protocol revision 2026-07-28. Floating that pin will break the
imports outright: the 1.x line uses `mcp.server.fastmcp` and `mcp.types`, both
of which no longer exist.

## Run

```bash
uv run python -m k8s_responder                    # stdio, what a desktop host spawns
uv run python -m k8s_responder --context staging  # pick a kubeconfig context
uv run python -m k8s_responder --max-replicas 5 --protect kube-system,prod
uv run python -m k8s_responder --http             # Streamable HTTP on 127.0.0.1:8000
```

Credentials load on the first tool call, not at startup, so a missing kubeconfig
produces a readable tool error rather than a server that will not start.
In-cluster configuration is tried first; a kubeconfig is the fallback.

Under stdio, **stdout is the protocol channel**. A stray `print()` anywhere in
this package would be parsed as a JSON-RPC frame and break the connection. All
logging goes to stderr, which is set up in `app.py`.

## Test

```bash
uv run pytest
```

**The suite needs no cluster.** `tests/conftest.py` is a fake CoreV1Api and
AppsV1Api pair that returns real `kubernetes.client` model objects and raises
real `ApiException`s, installed through `cluster.use()`. Nothing in the package
type-checks the API objects, so the fakes are indistinguishable from a cluster
to every line under test, including the `asyncio.to_thread` hop.

Each test opens its client with `async with` in the test body rather than taking
it from a yield fixture. The client owns an anyio task group, a task group must
be exited by the task that entered it, and a yield fixture tears down elsewhere:
every test fails with a cancel-scope error if you try.

## The four decisions worth reading the code for

### 1. The read/write split is structural

`inspect.py` and `diagnose.py` import nothing that can change a cluster. There is
no `delete_namespaced_*`, `patch_namespaced_*`, or `create_namespaced_*` call
anywhere in them, and `test_the_read_modules_contain_no_mutating_api_calls`
asserts it against the module source. `remediate.py` is the only module that
writes. Delete its import from `__init__.py` and the capability is gone from the
process, which is a stronger guarantee than any runtime flag.

This matters because **annotations are hints, not enforcement**. `readOnlyHint`
does not stop a tool from writing, does not suppress a host's approval dialog,
and is not a permission. A host may show it, ignore it, or apply its own policy.
The read tools here are safe because they contain no writing code.

### 2. The kubernetes client is synchronous, so it never touches the event loop

Every API call goes through `cluster.call()`, which is `asyncio.to_thread`. A
blocking HTTP request made directly from an `async def` tool freezes the whole
transport until the API server answers: no second tool call, no progress
notification, no cancellation. `test_kubernetes_calls_run_on_a_worker_thread`
records the thread of every fake API call and asserts none of them ran on
`MainThread`.

`asyncio.get_event_loop().run_in_executor(...)` is the older spelling of this and
is not used: `get_event_loop()` is deprecated inside a coroutine and creates a
second loop in some threaded contexts.

### 3. Logs are bounded in two dimensions

`get_logs` caps lines and bytes separately, because one bound is not enough.
`tail_lines` bounds what crosses the wire from the API server; `max_bytes` bounds
what reaches the model, which matters because one line of structured JSON logging
can be kilobytes on its own. The newest end is kept, the cut lands on a line
boundary, and `dropped_bytes` / `dropped_lines` / `note` say exactly what was
thrown away. Shipping five megabytes of logs into a context window is the failure
this project exists to avoid.

### 4. Every remediation is previewed, then approved

`ChangePreview` is a pure function of the tool's arguments: operation, target,
the exact API call, whether it can be undone, and how far it reaches. The same
object is rendered into the approval prompt and returned in the result, so what
was approved and what was reported are provably the same text.

**The question is built from the arguments and nothing else.** The SDK pins a
recorded answer to a SHA-256 digest of the rendered question and re-runs the
resolver on every round of the exchange. Put a live replica count or a timestamp
in the message and round two renders a different question, the digest stops
matching, the answer looks stale, and the call loops until it hits
`input_required_max_rounds`. So the preview describes the *intended* change,
which is knowable from the arguments; the *observed* before and after state is
read in the tool body and returned in the result.

Blast-radius guards run in the resolver, before the elicitation is rendered, so
nobody is ever invited to approve a change the server was always going to refuse:

| Guard | Default | Override |
|---|---|---|
| Maximum replica count | 20 | `K8S_RESPONDER_MAX_REPLICAS`, `--max-replicas` |
| Namespaces closed to writes | `kube-system`, `kube-public`, `kube-node-lease` | `K8S_RESPONDER_PROTECTED_NAMESPACES`, `--protect` |
| Log lines per call | 500 | `K8S_RESPONDER_MAX_TAIL_LINES` |
| Log bytes per call | 40000 | `K8S_RESPONDER_MAX_LOG_BYTES` |

Reads are never restricted. Refusing to restart a pod in `kube-system` is not a
reason to refuse to look at one.

## Two traps this code exists to demonstrate

### A rollback that only restores images is not a rollback

A bad deploy typically changes several things at once: the image, an environment
variable, a memory limit. Put only the image back and the pod comes up on the old
code with the new, broken configuration, the tool reports success, and the
incident continues with everyone believing it is over.

`rollback_deployment` sends a **JSON Patch that replaces `/spec/template`
outright**, which is what `kubectl rollout undo` does. A strategic merge patch
would not do: it merges lists by key, so an environment variable present in the
current template and absent from the target would survive the rollback.

What comes back: images, command, args, environment, resource requests and
limits, probes, volumes, securityContext, and everything else under
`spec.template`. What does not: `spec.replicas`, deployment-level fields such as
`spec.strategy`, labels and annotations on the Deployment itself, and every
object the template merely references (ConfigMaps, Secrets, PVCs, Services, any
HorizontalPodAutoscaler). Both lists are returned on every rollback in `restored`
and `not_restored`, so the scope travels with the result.

### A label selector is not an ownership test

`list_namespaced_replica_set(label_selector=...)` returns every ReplicaSet whose
labels match, and two deployments in one namespace routinely select the same
labels: that is what a canary or a blue/green looks like. Sort those by revision
and "the previous revision" can be a pod template belonging to a different
deployment entirely. Rolling back to it is not a rollback; it is a
cross-deployment overwrite that reports success.

`owned_replica_sets()` filters on the **controller owner reference UID**, which
is the authoritative link the Deployment controller sets on every ReplicaSet it
creates. The test fixture contains a `checkout-canary` deployment whose
ReplicaSet shares `checkout`'s labels and sorts first, precisely so
`test_rollback_picks_the_owned_previous_revision` fails if that filter is ever
removed.

## RBAC

The guards in this server are convenience, not security. The real control is the
service account it runs under. A minimal read-only role for post 15:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: k8s-responder-read
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list"]
```

Post 16 additionally needs `delete` on `pods`, `patch` on `deployments`, and
`patch` on `deployments/scale`. Grant those in the namespaces you actually intend
to remediate and nowhere else. If the server cannot reach a namespace, no bug in
this code can put it there.

## Connecting it to a host

The command a host needs to spawn:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/15-devops-responder", "run", "python", "-m", "k8s_responder"]
}
```

Where that JSON goes differs per host, and the key it sits under differs too.
Post 23 has the matrix.
