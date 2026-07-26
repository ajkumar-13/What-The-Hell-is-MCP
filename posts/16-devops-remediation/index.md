# 16 · Project 2 · Safe remediation with approval

> **TL;DR.** A Model Context Protocol (MCP) tool that changes a cluster is defensible only
> when a human has seen the exact change first, and "exact" has to be provable rather than
> promised. This post adds three writing tools to the Kubernetes first responder from
> [Post 15](../15-devops-responder/index.md), each gated by an elicitation whose question is
> a pure function of the tool's arguments, so what was approved and what is reported are the
> same text by construction. Along the way it fixes two failures the previous edition
> shipped: a rollback that restored only container images, and a revision lookup that could
> roll one deployment back onto another deployment's pod template.
>
> **After reading this you will be able to:**
> - Gate a mutating tool behind an approval the model can neither see nor forge.
> - Render a change preview that is provably identical in the question and in the result.
> - Roll a deployment back the way `kubectl rollout undo` does, and say what that does not cover.
> - Put blast-radius limits where they fire before a human is ever asked.

![A four-stage loop drawn left to right. First, the model proposes a change by calling a tool with arguments. Second, a resolver runs before anything else: it checks the blast-radius guards, and if the change is out of bounds it fails the call here, with no question ever shown to anyone. Third, if the guards pass, the resolver renders a preview built only from the arguments into an elicitation question, the server returns an input required result and the original request ends; the client asks the human, then retries the same tool call with the answer attached. Fourth, on the retry the server applies the change, reads the cluster back to verify it, and returns a result carrying the same preview object that was put in the question, plus the observed before and after state and any warnings. A note marks that the guard stage runs before the human stage, not after.](diagrams/01-propose-preview-approve.svg)
*The guard fires before anybody is asked. The preview that is approved is the object that comes back.*

---

## 1. The three remediations worth automating

[Post 15](../15-devops-responder/index.md) built the reading half of this server: eight tools
that list, describe, read logs, and diagnose, in a package where the read modules physically
cannot write. This post adds the other half, and it is deliberately small:

| Tool | What it does | Reversible? |
|---|---|---|
| `restart_pod` | deletes a pod so its controller creates a replacement | no |
| `scale_deployment` | sets `spec.replicas` | partly |
| `rollback_deployment` | replaces `spec.template` with a previous revision's | yes, by rolling forward |

Three, and no more. The test for whether a remediation belongs on this list is not whether it
is useful, it is whether the change can be described completely in a sentence a person can
evaluate in the ten seconds they will actually spend on it. "Delete this pod" passes.
"Reconcile the cluster to the desired state" does not.

Everything in this post lives in
[remediate.py](../../code/15-devops-responder/src/k8s_responder/remediate.py), which is the
only module in the package that contains a `delete_`, `patch_`, or `create_` call. Delete its
import from `__init__.py` and the writing capability is gone from the process, which is a
stronger guarantee than any runtime flag.

## 2. What "approval" cannot mean

The obvious design is a parameter:

```python
@mcp.tool()
async def restart_pod(pod_name: str, approved: bool) -> str:
    if not approved:
        return "Not approved."
```

`approved` is now in the published input schema, which is in the tool description the model
reads, which means the model fills it in. You have built a field whose only purpose is to be
set to `true` by the thing you were gating.

The annotation route is no better. Every writing tool here does carry
`destructive_hint=True`, and it is worth being exact about what that buys, because the
previous edition of this post said the annotation was what triggered host approval. It is
not. The specification says of `ToolAnnotations`:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide
> a faithful description of tool behavior (including descriptive properties like `title`).
> Clients should never make tool use decisions based on `ToolAnnotations` received from
> untrusted servers.

A host may prompt on `destructiveHint`, may prompt on everything, or may prompt on nothing.
You cannot build a safety property on a flag the other side is explicitly told to distrust.

What works is the mechanism from [Post 08](../08-elicitation-and-mrtr/index.md): the server
answers the tool call with a question instead of a result, and does not act until the client
comes back with an answer. Since revision 2026-07-28 there is no server-to-client request
channel, so the server cannot call out and block. It returns `resultType: "input_required"`,
the original request ends, and the client retries the same call with `inputResponses`
attached. That pattern is Multi Round-Trip Requests (MRTR).

Here is the shape on the wire. Round one, the model's call:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "method": "tools/call",
  "params": {
    "name": "scale_deployment",
    "arguments": { "deployment_name": "checkout", "replicas": 5 },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } }
    }
  }
}
```

Round one's answer is not a result, it is a question:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "resultType": "input_required",
    "inputRequests": {
      "k8s_responder.remediate:confirm_scale": {
        "method": "elicitation/create",
        "params": {
          "mode": "form",
          "message": "Approve this change to the cluster?\n\n  operation:    scale deployment\n  target:       deployment/checkout\n  ...",
          "requestedSchema": {
            "type": "object",
            "properties": {
              "approve": { "type": "boolean" },
              "reason": { "type": "string", "default": "" }
            },
            "required": ["approve"]
          }
        }
      }
    },
    "requestState": "v1.aead..."
  }
}
```

Then the client shows the message to a person, and retries with a **new** JSON-RPC id, the
same arguments, the answer under `inputResponses`, and the `requestState` echoed back byte
for byte. Note where those two fields sit: inside `params`, as siblings of `name` and
`arguments`, not inside `_meta`.

The key in `inputRequests` is derived from the resolver's `module:qualname`, which is stable
across workers. That is what makes this work on a stateless Hypertext Transfer Protocol
(HTTP) deployment where the retry lands on a different machine.

None of that appears in the tool body. In the Python software development kit (SDK) it is a
parameter the model cannot see:

```python
@mcp.tool(title="Scale a deployment", annotations=SCALING)
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    approval: Annotated[ElicitationResult[Approval], Resolve(confirm_scale)],
    namespace: str = "default",
) -> ScaleResult:
```

`Resolve` strips `approval` from the published schema and fills it in by asking. The security
property is measured rather than asserted, in
[tests/test_server.py](../../code/15-devops-responder/tests/test_server.py):

```python
async def test_the_approval_parameter_is_hidden_from_the_model():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    assert set(tools["scale_deployment"].input_schema["properties"]) == {
        "deployment_name",
        "replicas",
        "namespace",
    }
```

The equality is deliberate. `assert "approval" not in props` would pass forever while the
next resolved parameter you add quietly leaks into the schema.

## 3. The preview, and why it must be a pure function

A confirmation dialog that says "Are you sure?" is theater. The question has to name the
operation, the target, the exact API call, whether it can be undone, and how far it reaches.
That is a `ChangePreview`:

```python
@dataclass
class ChangePreview:
    operation: str
    target: str
    namespace: str
    change: str
    api_call: str
    reversible: str
    blast_radius: str
```

Every field is derived from the tool's arguments and nothing else. That constraint is not
stylistic, and getting it wrong produces one of the nastiest bugs in this revision of the
protocol.

**The SDK pins a recorded answer to a digest of the exact rendered question.** It computes a
SHA-256 hash, where SHA-256 is the 256-bit Secure Hash Algorithm, over the question text, and
it re-runs the resolver on every round of the exchange. Put a live replica count or a
timestamp in the message and round two renders a different string, the digest stops matching,
the recorded answer looks stale, and the server asks again. The call never converges; it
spins until it hits `input_required_max_rounds` and raises. From the outside it looks as
though the user's answer was ignored.

So the preview describes the **intended** change, which is knowable from the arguments, and
the **observed** before and after state is read in the tool body and returned in the result.
Those are two different jobs and they belong in two different places.

Here is the question the server actually produced for `scale_deployment("checkout", 5)`,
captured verbatim from a real run:

```
Approve this change to the cluster?

  operation:    scale deployment
  target:       deployment/checkout
  namespace:    default
  change:       set spec.replicas to 5
  api call:     PATCH /apis/apps/v1/namespaces/default/deployments/checkout/scale
  reversible:   Partly. The replica count can be set back, but pods terminated on the way down do not come back, and pods created on the way up start cold.
  blast radius: Every pod of this deployment. Scaling to 5 changes how much traffic it can serve and how much cluster capacity it holds.
```

Two tests hold that shape in place. The first counts the questions, because one question
means the digest matched:

```python
async def test_the_question_is_asked_once_and_shows_the_whole_preview(fake):
    seen: list[str] = []
    async with answering("accept", APPROVE, seen=seen) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )

    assert len(seen) == 1
```

The second is the interesting one. It calls the tool, changes the cluster underneath, calls
the tool again with identical arguments, and asserts the two questions are byte-identical:

```python
async def test_the_same_arguments_render_the_same_question(fake):
    ...
    fake.deployments[0].spec.replicas = 11
    ...
    assert first == second
```

If anybody ever writes "scaling from 3 to 5" into the preview, that test goes red, because
the "3" is a live reading and the live reading changed.

**The same object comes back in the result.** `ScaleResult`, `RestartResult`, and
`RollbackResult` all carry a `preview` field, and it is populated from the same function call
that built the question. So the model can report what was approved without paraphrasing it,
and a refused change still returns its preview, so the model can explain what would have
happened:

```python
async def test_a_refused_change_still_returns_its_preview(fake):
    async with answering("decline") as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 6}
        )

    preview = result.structured_content["preview"]
    assert preview["change"] == "set spec.replicas to 6"
```

**Three answers, not two.** An `ElicitResult` carries `accept`, `decline`, or `cancel`, and
accept means the form came back rather than that the user said yes. A checkbox left unchecked
arrives as an accept with `approve: false`. All four paths are distinct here:

```python
def _refusal(approval: ElicitationResult[Approval], what: str) -> str | None:
    if isinstance(approval, DeclinedElicitation):
        return f"Not applied. The user declined to approve {what}."
    if isinstance(approval, CancelledElicitation):
        return f"Not applied. The approval prompt for {what} was dismissed."
    if isinstance(approval, AcceptedElicitation) and not approval.data.approve:
        return f"Not applied. The user answered no to {what}."
    return None
```

The specification does not say what a server must *return* after a decline or a cancel, and
it is worth flagging that as an open choice rather than a rule. A `"complete"` result with
`isError: true`, a `"complete"` result describing the abandonment without `isError`, and
another `input_required` re-asking are all permitted. This server picks the middle one: a
declined change is not a tool failure, and the model gets a sentence it can relay.

## 4. Restart, and what a restart actually is

Kubernetes has no restart verb for a pod. Deleting one that a controller owns is the restart:
the ReplicaSet notices the shortfall and creates a replacement, with a new name and a new
Internet Protocol (IP) address.

That last clause is the whole reason this tool has a refusal in it. If the pod has **no**
controlling owner, deleting it is not a restart, it is a removal. Nothing recreates it. The
human approved a restart, and doing something strictly more destructive than the thing they
were shown is not covered by that approval, so the tool stops:

```json
{
  "approved": true,
  "applied": false,
  "outcome": "Refused. Pod 'orphan-pod' has no controlling owner, so deleting it would not restart it, it would remove it permanently. Delete it with kubectl if that is genuinely what you want.",
  "warnings": [
    "A pod with no controller is usually either created by hand or left behind by a deleted controller."
  ]
}
```

Note `approved: true, applied: false`. Those are two different facts and the result keeps them
apart: the human said yes, and the server declined anyway.

The other habit worth copying is that the tool reads the pod back rather than assuming the
delete worked. Kubernetes accepting a delete means the object is marked for deletion, not that
it is gone:

```python
    try:
        after = await call(
            conn.core_v1.read_namespaced_pod, name=pod_name, namespace=namespace
        )
        state = (
            "terminating"
            if after.metadata.deletion_timestamp
            else "still present with no deletion timestamp"
        )
    except ClusterError as err:
        state = "gone" if err.status == 404 else f"unverifiable ({err})"
```

Three outcomes, all of them reported truthfully, including the one where the server cannot
tell.

## 5. Scale, and reading back rather than assuming

`scale_deployment` patches `spec.replicas` through the `scale` subresource. Two things about
it are worth more than the patch itself.

**It reads the deployment back.** The result carries `previous_replicas`,
`requested_replicas`, and `observed_replicas` as three separate numbers, and if the observed
value does not match the requested one it says so in a warning, naming the two things that
usually cause it: a HorizontalPodAutoscaler, or an admission webhook. Reporting "scaled to 5"
when a controller immediately set it back to 3 is worse than reporting nothing.

**Scaling to zero is allowed.** Stopping a service that is melting a database is a legitimate
first response. The preview says plainly what it means, and there is a test that checks the
sentence survives:

```python
async def test_scaling_to_zero_is_allowed_and_says_what_it_means(fake):
    ...
    assert "serve no traffic" in data["preview"]["blast_radius"]
```

Scaling to the count it already has is a no-op that says so, and makes no API call at all.
That matters more than it looks: an idempotent tool that quietly re-patches on every call
generates a rollout event every time a model retries.

## 6. Rollback: what goes back, and what does not

This is the section the previous edition of this post got wrong, and the failure mode is
specific enough to be worth naming.

A bad deploy usually changes several things at once. A new image, a new environment variable,
a lower memory limit. The previous edition's rollback built a patch containing only the
container names and images:

```python
# the previous edition. This is not a rollback.
patch_body = {"spec": {"template": {"spec": {"containers": [
    {"name": c.name, "image": c.image} for c in target_rs.spec.template.spec.containers
]}}}}
```

That puts the old image back and leaves everything else exactly where the bad deploy left it.
The pod comes up on the old code with the new, broken configuration, the tool reports success,
and the incident continues with everyone believing it is over. There is no error anywhere.

![A diagram in two columns under a single deployment object. The left column, headed restored, lists what replacing the whole pod template puts back: container images for every container and init container, command and arguments and environment variables, resource requests and limits, all three kinds of probe, volumes and mounts and security context and service account, the template's own labels and annotations, and everything else under spec dot template. The right column, headed not restored, lists what a rollback deliberately leaves alone: spec dot replicas, which lives outside the pod template, deployment-level fields such as the rollout strategy and minimum ready seconds, labels and annotations on the deployment object itself, and every object the template merely references, including config maps, secrets, persistent volume claims, services, and any horizontal pod autoscaler. A footer notes that both lists are returned in every rollback result, so the scope travels with the answer rather than living only in documentation.](diagrams/02-rollback-scope.svg)
*A rollback restores a pod template. Most of what a bad deploy touches is not in the pod template.*

**The fix is to replace the whole template, with a JSON Patch.** JSON is JavaScript Object
Notation, and a JSON Patch is a list of operations applied to a document. This is what
`kubectl rollout undo` does:

```python
    patch = [{"op": "replace", "path": "/spec/template", "value": template}]

    await call(
        conn.apps_v1.patch_namespaced_deployment,
        name=deployment_name,
        namespace=namespace,
        body=patch,
        _content_type="application/json-patch+json",
    )
```

**A strategic merge patch is the wrong tool here, and the reason is subtle.** Strategic merge
is what the Kubernetes API uses by default for a deployment patch, and it merges lists by
their key rather than replacing them. A container's `env` list is keyed by `name`. So an
environment variable that exists in the current template and does *not* exist in the target
would be merged rather than removed, and it would survive the rollback. "Put it back the way
it was" needs a replace of the whole subtree, and the content type is how you ask for one.

Here is the patch this server actually sent, captured from a run against the test fixture:

```json
[
  {
    "op": "replace",
    "path": "/spec/template",
    "value": {
      "metadata": {
        "labels": { "app": "checkout" },
        "annotations": {
          "kubernetes.io/change-cause": "rollback to revision 2 via k8s-responder: paged at 02:00"
        }
      },
      "spec": {
        "containers": [
          {
            "name": "app",
            "image": "registry.example.com/app:1.3.0",
            "env": [ { "name": "DB_HOST", "value": "db-primary" } ],
            "resources": {
              "limits": { "memory": "512Mi" },
              "requests": { "memory": "256Mi" }
            }
          }
        ]
      }
    }
  }
]
```

The fixture's current revision has `FEATURE_NEW_CART=true` and a 256Mi memory limit. In the
patch both are gone: the environment variable is absent, not merged, and the limit is back at
512Mi. That is asserted directly:

```python
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env == {"DB_HOST": "db-primary"}
    assert "FEATURE_NEW_CART" not in env
    assert container["resources"]["limits"]["memory"] == "512Mi"
```

**Two details in that patch that are easy to miss.** The `pod-template-hash` label is stripped
before the template is sent. That label is computed and applied by the Deployment controller
and it identifies the ReplicaSet a template belongs to, so copying it from an old ReplicaSet
into the Deployment's template would be claiming to be that ReplicaSet. `kubectl rollout undo`
strips it for the same reason. And a `kubernetes.io/change-cause` annotation is added, which is
the one field this tool writes rather than restores, so `kubectl rollout history` later shows
why the template changed and what reason the operator typed.

**What a rollback does not restore is returned with every result.** Not in the documentation,
in the payload:

```json
"not_restored": [
  "spec.replicas: the replica count lives outside the pod template and is left exactly where it is, including if something scaled it during the incident",
  "deployment-level fields such as spec.strategy, minReadySeconds, progressDeadlineSeconds, and revisionHistoryLimit",
  "labels and annotations on the Deployment object itself",
  "objects the template only references: ConfigMaps, Secrets, PersistentVolumeClaims, Services, Ingresses, and any HorizontalPodAutoscaler",
  "anything changed outside this Deployment, which includes most of what a bad deploy actually touches"
]
```

plus a warning naming the actual number: `spec.replicas was left at 3. A rollback does not
restore the replica count.` A caller who does not know that closes the incident too early. A
caller who is told it in the result cannot.

That last list item deserves emphasis. A rollback is a Deployment-scoped operation. If the bad
deploy also changed a ConfigMap, rotated a Secret, or applied a database migration, rolling
the Deployment back fixes none of it, and may make things worse by putting old code in front
of a new schema.

## 7. The ReplicaSet ownership trap

To roll back you need the previous revision, and the previous revision is a ReplicaSet. The
obvious way to find one is the deployment's own label selector:

```python
    result = await call(
        conn.apps_v1.list_namespaced_replica_set,
        namespace=namespace,
        label_selector=label_selector,
    )
```

**A label selector is not an ownership test.** That call returns every ReplicaSet in the
namespace whose labels match, and two deployments routinely select the same labels: that is
exactly what a canary or a blue/green deployment looks like. Sort the results by revision and
"the previous revision" can be a pod template belonging to a different deployment. Rolling
back to it is not a rollback, it is a cross-deployment overwrite that reports success.

The test fixture contains that situation on purpose. Alongside `checkout` at revision 3 there
is a `checkout-canary` deployment that selects the same `app=checkout` labels, and its
ReplicaSets sit in the list at revision 2 and revision 9. The revision-2 one is first in the
list, which is precisely the slot a label-only lookup would pick as "the previous revision" of
`checkout`.

The fix is the controller owner reference, which Kubernetes sets on every ReplicaSet the
Deployment controller creates:

```python
    for rs in replica_sets:
        for ref in (rs.metadata.owner_references or []):
            if not getattr(ref, "controller", False):
                continue
            if ref.kind != "Deployment":
                continue
            if uid and getattr(ref, "uid", None):
                if ref.uid == uid:
                    owned.append(rs)
                    break
```

Matching on `uid` rather than `name` matters because a uid is unique across deletions and
recreations, and a name is not. Delete a deployment and recreate it with the same name and the
old ReplicaSets can linger.

This is not a theoretical concern, and the suite proves it. Replacing `owned_replica_sets()`
with a pass-through, which is what "filtering by label only" amounts to, turns four tests red:

```
FAILED tests/test_server.py::test_rollout_history_only_lists_replicasets_this_deployment_owns
FAILED tests/test_server.py::test_rollback_picks_the_owned_previous_revision
FAILED tests/test_server.py::test_rolling_back_to_another_deployments_revision_is_refused
FAILED tests/test_server.py::test_rollback_replaces_the_whole_pod_template
4 failed, 74 passed in 1.80s
```

and the second of those failures says exactly what went wrong:

```
>       assert data["restored_images"] == ["registry.example.com/app:1.3.0"]
E       AssertionError: assert ['registry.ex...canary:9.9.9'] == ['registry.ex...om/app:1.3.0']
E         At index 0 diff: 'registry.example.com/canary:9.9.9' != 'registry.example.com/app:1.3.0'
```

The rollback of `checkout` has restored the canary's image, `applied` is `true`, and nothing
raised. That is the shape of the bug: it does not fail, it succeeds at the wrong thing.

With the filter in place, `get_rollout_history` for `checkout` returns exactly its own three
revisions, and asking to roll back to revision 9, which exists in the namespace and belongs to
somebody else, is refused with the available list:

```
Refused. Revision 9 is not in the history of 'checkout'. Available revisions: 3, 2, 1.
```

## 8. Blast-radius limits, checked before anyone is asked

Some changes should never reach a human at all. Asking somebody to approve a scale to 500
replicas wastes their attention and, worse, trains them to click through prompts.

![A two-lane diagram comparing where a limit can be enforced. The upper lane, marked wrong, puts the check inside the tool body: the model proposes an out-of-bounds change, a human is shown an approval prompt for it, the human approves, and only then does the tool refuse, so a person was asked to authorize something the server was never going to do. The lower lane, marked correct, puts the check inside the resolver, which runs before the elicitation is rendered: the guard raises, the call fails immediately, and no question is ever shown. Below the lanes, a table lists the three guards this server applies, the maximum replica count of twenty, the set of protected namespaces closed to writes, and the log line and byte ceilings, each with the environment variable and command-line flag that changes it, and a note that reads are never restricted because refusing to restart a pod in kube-system is not a reason to refuse to look at one.](diagrams/03-blast-radius.svg)
*A guard inside the tool body runs after the human. A guard inside the resolver runs instead of asking.*

The guards live in the resolver, which the SDK runs before it renders the question:

```python
def confirm_scale(
    deployment_name: str, replicas: int, namespace: str
) -> Elicit[Approval]:
    guard_namespace(namespace)
    guard_replicas(replicas)
    return Elicit(
        preview_scale(deployment_name, replicas, namespace).as_question(), Approval
    )
```

A resolver that raises fails the call without ever producing an elicitation. The test asserts
all three consequences at once: the call errors, the question list is empty, and the cluster
was never touched.

```python
async def test_scaling_past_the_maximum_is_refused_without_asking_anyone(fake):
    seen: list[str] = []
    async with answering("accept", APPROVE, seen=seen) as c:
        result = await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 500}
        )

    assert result.is_error
    assert "maximum is 20" in result.content[0].text
    assert seen == []
    assert fake.recorder.names() == []
```

`seen == []` is the line that matters. The client in that test is one whose user always
approves, and it never got the chance.

The message a refusal produces is written for a model to act on, not for a log:

```
Refusing to scale to 500. The configured maximum is 20. A larger change than this
should be made deliberately, by a human, with capacity in mind. Set
K8S_RESPONDER_MAX_REPLICAS to raise it.
```

The protected-namespace guard is the same shape and the same placement:

```
Refusing to change anything in namespace 'kube-system'. Protected namespaces are:
kube-node-lease, kube-public, kube-system. Reading these namespaces is still
allowed. Set K8S_RESPONDER_PROTECTED_NAMESPACES to change the list.
```

Note the second sentence. **Reads are never restricted.** Refusing to restart a pod in
`kube-system` is not a reason to refuse to look at one, and there is a test that keeps the
read path open.

**These guards are convenience, not security.** They are a local policy in a process the model
is talking to, and a bug in this file removes them. The control that actually matters is
role-based access control (RBAC): the service account the server runs under. Grant `delete` on
`pods` and `patch` on `deployments` and `deployments/scale` in the namespaces you intend to
remediate, and nowhere else. If the server cannot reach a namespace, no bug in this code can
put it there. [Post 19](../19-security/index.md) is the general argument.

## 9. Long rollouts, and what "done" means

A rollback returns in milliseconds. The rollout it starts takes as long as the deployment's
strategy and the pods' readiness probes say it takes, which can be minutes.

This server does not wait. `patch_namespaced_deployment` returns as soon as the API server has
accepted the new spec, and the tool verifies what it can verify at that moment, which is that
the object reads back with the template it just wrote. It then says so plainly:

> Kubernetes now rolls the pods according to the deployment's strategy; call `list_pods` and
> `list_deployments` to watch it finish.

`list_deployments` from [Post 15](../15-devops-responder/index.md) is the tool that answers
"is it finished", by marking a deployment `degraded` when fewer replicas are ready than
desired. Polling it is a two-line loop for the model and costs one API call per check.

The alternative is the tasks extension, `io.modelcontextprotocol/tasks`, covered in
[Post 09](../09-tasks/index.md), which turns "hold the connection open and hope" into an
explicit pollable lifecycle. It is the right answer when the *server* is doing long work and
holds state the client cannot see. It is a poor fit here, because the server is not doing the
work: Kubernetes is, and its progress is already readable through an ordinary tool by anyone
with `get` on deployments. Wrapping a poll the client can do itself in a task adds a
lifecycle to manage and takes nothing away.

The honest caveat is that "verified" here means the spec was accepted, not that the fix
worked. A rollback to a revision whose image also fails to pull will report a successful
rollback and leave you exactly as broken. Verification of *effect* needs the reading tools and
a wait, and no tool call can substitute for that.

## 10. Running it

```bash
cd code/15-devops-responder && PYTHONPATH=src pytest tests -q
```
```
........................................................................ [ 92%]
......                                                                   [100%]
78 passed in 1.34s
```

Seventy-eight tests for both posts, in under two seconds, with no cluster. The elicitation
loop is exercised in full, because the human is an injectable callback:

```python
def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always answers the approval prompt the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)
```

One test worth stealing outright is parameterized over every writing tool, so a fourth one
added next year cannot skip the gate:

```python
@pytest.mark.parametrize("tool_name", sorted(WRITE_TOOLS))
async def test_every_write_tool_requires_an_answer_before_it_acts(tool_name, fake):
    ...
    async with answering("decline") as c:
        result = await c.call_tool(tool_name, arguments)

    assert result.structured_content["applied"] is False
    assert fake.deleted == []
    assert fake.patches == []
```

Two operational notes for a real deployment. Passing an `elicitation_callback` is what
declares the capability, and without it the server returns `-32021`
(`MISSING_REQUIRED_CLIENT_CAPABILITY`) before it ever asks, which gets reported to you as "the
tool is broken". And `requestState` is sealed by the SDK under a process-local ephemeral key by
default, which is correct for stdio and wrong for more than one worker: pass
`RequestStateSecurity(keys=[...])` with at least 32 bytes per key. [Post 08](../08-elicitation-and-mrtr/index.md)
covers both.

## 11. When not to build this

**When the same fix is always right.** If a pod always needs restarting under condition X,
that is a controller, a liveness probe, or a `restartPolicy`, and it should run without a
human and without a model. Automation that needs an approval every time is automation you
have not finished.

**When nobody will read the question.** A person who is asked to confirm something at the end
of a long agent turn will approve it. Elicitation is a consent mechanism only when the
question is rare, specific, and expected. Three tools is a deliberate number.

**When the blast radius is not describable.** Every preview in this file fits in seven short
fields. A change that cannot be summarized that way is a change nobody can meaningfully
approve, and shipping a prompt for it converts a considered decision into a reflex.

---

## Common pitfalls

- **Adding an `approved: bool` parameter.** It lands in the published input schema, so the
  model fills it in. Use a resolved parameter, and assert on the exact property set the tool
  publishes rather than on the absence of one name.
- **Believing `destructiveHint` triggers a prompt.** It is a hint, the specification tells
  clients to treat annotations from untrusted servers as untrustworthy, and a host may prompt
  on everything or on nothing. What stops the change is that the server does not act without
  an answer.
- **Putting a live reading in the approval message.** The recorded answer is pinned to a
  digest of the rendered question and the resolver re-runs every round, so "scaling from 3 to
  5" makes the call loop until it hits the round limit. Build the message from the arguments
  and nothing else, and test that the same arguments render the same string.
- **Rolling back only the container images.** The bad deploy also changed an environment
  variable and a memory limit, and a merge patch keeps both. Replace `/spec/template` with a
  JSON Patch `replace`, because strategic merge merges lists by key and a removed environment
  variable will survive.
- **Finding the previous revision by label selector.** Two deployments in one namespace
  routinely select the same labels, which is what a canary is. Filter ReplicaSets by
  controller owner reference `uid`, or you will roll one workload back onto another's pod
  template and report success.
- **Copying `pod-template-hash` into the restored template.** That label belongs to the
  ReplicaSet the Deployment controller computed it for. Strip it, exactly as
  `kubectl rollout undo` does.
- **Deleting a pod with no controller and calling it a restart.** Nothing recreates it. That
  is a removal, it is strictly more destructive than what the human approved, and the tool
  should refuse rather than silently do it.
- **Reporting success from the API's acknowledgement.** An accepted delete means marked for
  deletion, and an accepted scale can be overridden by a HorizontalPodAutoscaler seconds
  later. Read the object back, report the observed value next to the requested one, and warn
  when they differ.
- **Treating the guards as security.** They live in the same process as the bug you have not
  found yet. RBAC on the service account is the control; the guards only stop an obvious
  mistake before a human is bothered with it.

---

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The four steps, the
  `input_required` result, and the rule that the retry carries a different JSON-RPC id.
  <https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr>
- Specification, *"Elicitation"*, revision 2026-07-28. Form-mode schema limits, and the three
  actions this server branches on.
  <https://modelcontextprotocol.io/specification/draft/client/elicitation>
- Specification, *"Tools"*, revision 2026-07-28. The `ToolAnnotations` note quoted in
  section 2. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Kubernetes documentation, *"Rolling Back a Deployment"*. What `kubectl rollout undo` does,
  and why revisions are ReplicaSets.
  <https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-back-a-deployment>
- Kubernetes documentation, *"Owners and Dependents"*. Controller owner references, and why
  they are the authoritative link a label selector is not.
  <https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/>
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026).
  <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 17 — Project 3 · A deep research browser](../17-research-browser/index.md)**: the
  next project, where the hard problem stops being permission and becomes throwing away the
  ninety-five percent of a page that would waste the model's context.
- **[Post 15 — Project 2 · A DevOps first responder](../15-devops-responder/index.md)**: the
  reading half of this server, and the diagnosis that decides which of these three tools is
  the right one.
- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**:
  the mechanism underneath every approval here, message by message.
