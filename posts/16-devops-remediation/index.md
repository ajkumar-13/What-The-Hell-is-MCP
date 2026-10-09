# 16 - Project 2: Safe remediation with approval

> **TL;DR.** A Model Context Protocol (MCP) tool that changes a cluster is defensible only when a human has seen the exact change first, and "exact" has to be provable rather than promised. This post adds three writing tools to the Kubernetes first responder of [post 15](../15-devops-responder/index.md), each gated by an elicitation whose question is a pure function of the tool's arguments, so what was approved and what is reported are the same text by construction. Two rollback failures get the most attention, because neither raises an error: a rollback that restores only container images, and a revision lookup that rolls one deployment back onto another deployment's pod template. Every result, question, patch and refusal below is printed by a script in `snippets/` against the project's fake cluster under `mcp==2.3.0`, or by the project's own test suite.
>
> **Prerequisites:** [Post 15](../15-devops-responder/index.md), [Post 08](../08-elicitation-and-mrtr/index.md).
> **Safe to skip?** Skip it if the reader can already hide an approval from a tool's published schema with a resolved parameter, keep a rollback from restoring only images or another deployment's template, and place a limit where it refuses before anyone is asked.
>
> **After reading, you will be able to:**
>
> - Gate a mutating tool behind an approval the model can neither see nor forge.
> - Render a change preview that is provably identical in the question and in the result.
> - Roll a deployment back the way kubectl rollout undo does, and say what that does not cover.
> - Put blast-radius limits where they fire before a human is asked.

![Four numbered stages run left to right. Propose: the model calls scale_deployment on checkout with 500 replicas, with no approval argument because Resolve strips it from the schema. Guard: a resolver runs guard_namespace and guard_replicas before anything is shown. Approve: a preview built from the arguments alone goes out in an input_required result, and the client asks a person and retries with a new id and the answer. Apply and verify: the scale patch, a read-back with requested and observed replicas side by side, and the same preview returned. A red arrow from the guard stage marks that a scale to 500 against a maximum of 20 ends with no question shown.](diagrams/01-propose-preview-approve.svg)

*The guard fires before anybody is asked. The preview that is approved is the object that comes back.*

---

## 1. The question: how does a tool that changes a cluster prove a human saw the change first?

[Post 15](../15-devops-responder/index.md) built the reading half of this server: eight tools that list, describe, read logs, and diagnose, seven of them in modules that physically cannot write. This post adds the other half, and it is deliberately small:

| Tool | What it does | Reversible? |
|---|---|---|
| `restart_pod` | deletes a pod so its controller creates a replacement | no |
| `scale_deployment` | sets `spec.replicas` | partly |
| `rollback_deployment` | replaces `spec.template` with a previous revision's | yes, by rolling forward |

Three, and no more. The test for whether a remediation belongs on this list is not whether it is useful; it is whether the change can be described completely in a sentence a person can evaluate in the ten seconds they will actually spend on it. "Delete this pod" passes. "Reconcile the cluster to the desired state" does not.

A promise is a docstring that says "asks before acting"; a proof is a property of the published schema, of the question text, and of the order in which checks run, each of which a test can hold in place. The sections that follow build those three proofs, then the two places where a rollback goes wrong without any error at all.

The project is [code/15-devops-responder/](../../code/15-devops-responder/), the same directory as post 15, and every path below that starts with `src/` or `tests/` is relative to it. Everything in this post lives in [src/k8s_responder/remediate.py](../../code/15-devops-responder/src/k8s_responder/remediate.py), the only module in the package with a `delete_` or `patch_` call; `get_rollout_history`, the read-only tool post 15 listed, sits there too because its only use is choosing a rollback target. Delete the import of `remediate` from `__init__.py` and the writing capability is gone from the process, which is a stronger guarantee than any runtime flag.

## 2. What "approval" cannot mean

The obvious design is a parameter. `snippets/approval_wire.py` publishes it on a throwaway server:

```python
@naive.tool()
async def restart_pod(pod_name: str, approved: bool) -> str:
    if not approved:
        return "Not approved."
```

and prints what `tools/list` then tells the model:

```
restart_pod          properties=['pod_name', 'approved'] required=['pod_name', 'approved']
```

`approved` is in the published input schema, which is part of the tool definition the model reads, which means the model fills it in. That is a field whose only purpose is to be set to `true` by the thing being gated.

The annotation route is no better. Every writing tool here carries `destructive_hint=True`, and it is worth being exact about what that buys, because it is tempting to read it as the thing that triggers a host's approval dialog. It is not. The specification's Tools page prints this note on `ToolAnnotations`:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide a faithful description of tool behavior (including descriptive properties like `title`). Clients should never make tool use decisions based on `ToolAnnotations` received from untrusted servers.

The same page says clients **must** consider annotations untrusted unless they come from trusted servers. Whether to ask the user before a tool call is the host's decision, under that page's rule that clients should prompt for confirmation on sensitive operations, and a host may prompt on `destructiveHint`, on everything, or on nothing. A safety property cannot rest on a flag the other side is told to distrust.

What works is the mechanism of [post 08](../08-elicitation-and-mrtr/index.md): the server answers the tool call with a question instead of a result, and does not act until the client comes back with an answer. Revision 2026-07-28 has no server-to-client request channel, so the server cannot call out and block. It returns `resultType: "input_required"`, the original request ends, and the client retries the same call with `inputResponses` attached. That pattern is Multi Round-Trip Requests (MRTR).

The same script calls `scale_deployment` with a client whose user approves, over the software development kit's (SDK's) in-memory stream transport with a tap on both streams, and prints the JavaScript Object Notation Remote Procedure Call (JSON-RPC) messages of both legs. Leg one is the model's call, `id` `2` (the client's `server/discover` took `1`), with the two arguments and a `_meta` whose `io.modelcontextprotocol/clientCapabilities` declares `"elicitation": {"form": {}, "url": {}}`. Its answer is not a result; it is a question. The script cuts the question text, which section 3 prints whole, and the sealed `requestState`, which is different on every run:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "inputRequests": {
      "k8s_responder.remediate:confirm_scale": {
        "method": "elicitation/create",
        "params": {
          "message": "<the question, printed by preview_answers.py>",
          "mode": "form",
          "requestedSchema": {
            "properties": {
              "approve": {
                "description": "Apply this change to the cluster.",
                "title": "Approve",
                "type": "boolean"
              },
              "reason": {
                "default": "",
                "description": "Optional note recorded in the server log alongside the change.",
                "title": "Reason",
                "type": "string"
              }
            },
            "required": [
              "approve"
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
        "name": "k8s-responder",
        "version": ""
      }
    }
  }
}
```

The script also prints `kubernetes calls made before the human was asked: []`: nothing has touched the cluster yet. The client shows the message to a person and retries with a **new** JSON-RPC id, the same arguments, the answer under `inputResponses`, and the `requestState` echoed back byte for byte (the `_meta` is cut here because it repeats leg one's):

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "inputResponses": {
      "k8s_responder.remediate:confirm_scale": {
        "action": "accept",
        "content": {
          "approve": true,
          "reason": "paged at 02:00"
        }
      }
    },
    "requestState": "v1.<sealed, shortened here>",
    "name": "scale_deployment",
    "arguments": {
      "deployment_name": "checkout",
      "replicas": 5
    },
    "_meta": "<the same three keys as leg 1>"
  }
}
```

Note where those two fields sit: inside `params`, as siblings of `name` and `arguments`, not inside `_meta`. Leg two's result is `"resultType": "complete"` with `approved: true`, `applied: true`, and the replica counts `3`, `5`, `5` that section 5 explains, and the only write in the whole exchange is the one `patch_namespaced_deployment_scale` after the answer.

The key in `inputRequests`, `k8s_responder.remediate:confirm_scale`, is derived from the resolver's `module:qualname`, which is stable across worker processes. That is what makes this work on a stateless Hypertext Transfer Protocol (HTTP) deployment where the retry lands on a different machine, as long as every worker can unseal the state (section 12).

None of that appears in the tool body. In the SDK the approval is a parameter the model cannot see, declared in `src/k8s_responder/remediate.py`:

```python
@mcp.tool(title="Scale a deployment", annotations=SCALING)
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    approval: Annotated[ElicitationResult[Approval], Resolve(confirm_scale)],
    namespace: str = "default",
) -> ScaleResult:
```

`Resolve` strips `approval` from the published schema and fills it in by asking. `snippets/approval_wire.py` prints what `tools/list` publishes for the three writing tools:

```
restart_pod          properties=['pod_name', 'namespace'] required=['pod_name']
                     readOnly=False destructive=True idempotent=False openWorld=False
scale_deployment     properties=['deployment_name', 'replicas', 'namespace'] required=['deployment_name', 'replicas']
                     readOnly=False destructive=True idempotent=True openWorld=False
rollback_deployment  properties=['deployment_name', 'namespace', 'revision'] required=['deployment_name']
                     readOnly=False destructive=True idempotent=False openWorld=False
```

The security property is measured rather than asserted, in `test_the_approval_parameter_is_hidden_from_the_model` of `tests/test_server.py`, which goes on to check `rollback_deployment` the same way:

```python
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    assert set(tools["restart_pod"].input_schema["properties"]) == {
        "pod_name",
        "namespace",
    }
    assert set(tools["scale_deployment"].input_schema["properties"]) == {
        "deployment_name",
        "replicas",
        "namespace",
    }
```

The equality is deliberate. `assert "approval" not in props` would pass forever while the next resolved parameter somebody adds quietly leaks into the schema.

## 3. The preview, and why it must be a pure function

A confirmation dialog that says "Are you sure?" is theater. The question has to name the operation, the target, the exact application programming interface (API) call, whether it can be undone, and how far it reaches. That is a `ChangePreview`, in `src/k8s_responder/remediate.py`:

```python
@dataclass
class ChangePreview:
    """Exactly what a remediation will do, derived only from its arguments.

    Every field here is knowable before touching the cluster, which is what
    makes the rendered question stable across rounds of the approval exchange.
    """

    operation: str
    target: str
    namespace: str
    change: str
    api_call: str
    reversible: str
    blast_radius: str
```

`as_question()` renders it, and `snippets/preview_answers.py` prints the question the server asks for `scale_deployment("checkout", 5)`:

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

Every field is derived from the tool's arguments and nothing else. That constraint is not stylistic. **The SDK recognizes an answered question by a digest of the rendered question, and it re-runs the resolver on every round.** Post 08 measured the first half: the recorded answer is matched against a SHA-256 digest (256-bit Secure Hash Algorithm) of the question's message and schema. The script measures the second half on a throwaway server, counting resolver runs for one approved call:

```
stable  resolver runs=2 questions shown=1 distinct=1 ended: 'scaled to 5'
```

Two runs, one question: the first round renders the question and asks, the second renders it again, finds the digest matches the answer that came back, and lets the tool run. Put anything live in the message, a timestamp, or a replica count read from a cluster that moves between rounds, and the second rendering differs, the answer looks stale, and the server asks again; section 12 prints how that call ends. So the preview describes the **intended** change, which is knowable from the arguments, and the **observed** before and after state is read in the tool body and returned in the result. Those are two different jobs and they belong in two different places.

Two tests hold that shape in place. `test_the_question_is_asked_once_and_shows_the_whole_preview` counts the questions, because one question means the digest matched. The other calls the tool, changes the cluster underneath, calls it again with identical arguments, and asserts the two questions are byte-identical, in `tests/test_server.py`:

```python
async def test_the_same_arguments_render_the_same_question(fake):
    first: list[str] = []
    second: list[str] = []
    async with answering("decline", seen=first) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )
    fake.deployments[0].spec.replicas = 11
    async with answering("decline", seen=second) as c:
        await c.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": 5}
        )

    assert first == second
```

The script repeats it and prints `questions byte-identical: True`. If anybody ever writes "scaling from 3 to 5" into the preview, that test goes red, because the "3" is a live reading and the live reading changed.

**The same object comes back in the result.** `ScaleResult`, `RestartResult`, and `RollbackResult` all carry a `preview` field, populated from the same function call that built the question. So the model can report what was approved without paraphrasing it, and a refused change still returns its preview, so the model can explain what would have happened: the script declines a scale to 6 and the result's `preview.change` is `"set spec.replicas to 6"`.

**Three actions, four paths.** The Elicitation page gives a response three actions: `accept`, the user submitted the form; `decline`, the user explicitly refused; `cancel`, the user dismissed it without choosing. Accept means the form came back, not that the user said yes: a checkbox left unchecked arrives as an accept with `approve: false`. The project keeps all four paths apart, in `src/k8s_responder/remediate.py`:

```python
def _refusal(approval: ElicitationResult[Approval], what: str) -> str | None:
    """Turn a non-approval into the sentence we will report, or None if approved."""
    if isinstance(approval, DeclinedElicitation):
        return f"Not applied. The user declined to approve {what}."
    if isinstance(approval, CancelledElicitation):
        return f"Not applied. The approval prompt for {what} was dismissed."
    if isinstance(approval, AcceptedElicitation) and not approval.data.approve:
        return f"Not applied. The user answered no to {what}."
    return None
```

`snippets/preview_answers.py` gives each answer to `scale_deployment("checkout", 6)`:

```
decline                    asked=1 isError=false approved=false applied=false writes=[]
                           outcome='Not applied. The user declined to approve scaling checkout to 6 replica(s).'
cancel                     asked=1 isError=false approved=false applied=false writes=[]
                           outcome='Not applied. The approval prompt for scaling checkout to 6 replica(s) was dismissed.'
accept {"approve": false}  asked=1 isError=false approved=false applied=false writes=[]
                           outcome='Not applied. The user answered no to scaling checkout to 6 replica(s).'
accept {"approve": true}   asked=1 isError=false approved=true applied=true writes=['patch_namespaced_deployment_scale']
                           outcome="Scaled 'checkout' up from 3 to 6 replica(s) in namespace 'default'. spec.replicas now reads 6. The pods themselves take time to follow; call list_pods to watch them converge."
```

The specification does not say what a server must *return* after a decline or a cancel, and that is worth flagging as an open choice rather than a rule. A `"complete"` result with `isError: true`, a `"complete"` result describing the abandonment without `isError`, and another `input_required` re-asking are all permitted by the text. This server picks the middle one: a declined change is not a tool failure, and the model gets a sentence it can relay.

## 4. Restart, and what a restart actually is

Kubernetes has no restart verb for a pod. Deleting one that a controller owns is the restart: the ReplicaSet notices the shortfall and creates a replacement, with a new name and a new Internet Protocol (IP) address.

That last clause is the whole reason this tool has a refusal in it. If the pod has **no** controlling owner, deleting it is not a restart; it is a removal, and nothing recreates it. The human approved a restart, and doing something strictly more destructive than the thing they were shown is not covered by that approval, so the tool stops. `snippets/restart_scale.py` approves a restart of the fixture's `orphan-pod`:

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

Note `approved: true, applied: false`. Those are two different facts and the result keeps them apart: the human said yes, and the server declined anyway. The script prints `writes: []` after it.

The other habit worth copying is that the tool reads the pod back rather than assuming the delete worked. Kubernetes accepting a delete means the object is marked for deletion, not that it is gone. The read-back in `src/k8s_responder/remediate.py`:

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

The fake cluster removes a deleted pod at once, so the script alters it for one call each to show the other two states: a delete that only marks the pod, as a real API server does during the grace period, and a read-back the cluster forbids. For those two it prints only the sentence of the outcome that differs:

```
  pod removed at once:     controller='ReplicaSet/checkout-3c' writes=['delete_namespaced_pod']
                           outcome="Deleted pod 'checkout-3c-aaa' in namespace 'default'. It is gone. ReplicaSet/checkout-3c is responsible for creating the replacement; call list_pods to see it appear."
  pod marked for deletion: It is terminating.
  read-back forbidden:     It is unverifiable (Kubernetes API error 403 Forbidden). warnings=['Could not read the pod back to confirm the delete.']
```

Three outcomes, all of them reported truthfully, including the one where the server cannot tell.

## 5. Scale, and reading back rather than assuming

`scale_deployment` patches `spec.replicas` through the `scale` subresource, with the content type `application/merge-patch+json` passed explicitly. Two things about it are worth more than the patch itself.

**It reads the deployment back.** The result carries `previous_replicas`, `requested_replicas`, and `observed_replicas` as three separate numbers, and if the observed value does not match the requested one it says so in a warning, naming the two things that usually cause it. The script scales the fixture's `checkout` from 3 to 6, then repeats the call against a fake that keeps the old count, as an autoscaler resetting it would:

```
to 6:  previous=3 requested=6 observed=6 warnings=[]
to 6, against a cluster that keeps the old count:
       previous=3 requested=6 observed=3
       warnings=['The deployment reads back as 3 replica(s), not 6. A HorizontalPodAutoscaler or an admission webhook may be overriding this setting.']
```

Reporting "scaled to 6" when a controller immediately set it back to 3 is worse than reporting nothing.

**Scaling to zero is allowed.** Stopping a service that is melting a database is a legitimate first response. The preview says plainly what it means, and `test_scaling_to_zero_is_allowed_and_says_what_it_means` checks that the sentence survives. Scaling to the count the deployment already has is a no-op that says so and sends no patch at all:

```
to 3:  applied=false outcome="Nothing to do. 'checkout' is already set to 3 replica(s)." writes=[]
to 0:  applied=true blast_radius='Every pod of this deployment. Scaling to 0 stops the deployment entirely and it will serve no traffic.'
```

The no-op matters more than it looks: a tool that re-patches on every call writes to the cluster every time a model retries, for no change at all. The annotations say the same thing in metadata: `scale_deployment` is the one writing tool with `idempotent_hint=True`, because doing it twice lands in the same place.

## 6. Rollback: what goes back, and what does not

A bad deploy usually changes several things at once: a new image, a new environment variable, a lower memory limit. The tempting rollback builds a patch containing only the container names and images. `snippets/rollback.py` writes it against the fixture, where `checkout` is at revision 3 and `target_rs` is the ReplicaSet of revision 2:

```python
    # An image-only "rollback". This is not a rollback.
    patch_body = {"spec": {"template": {"spec": {"containers": [
        {"name": c.name, "image": c.image} for c in target_rs.spec.template.spec.containers
    ]}}}}
```

and merges it into the current template the way a strategic merge patch merges the `containers` list, by `name`:

```
revision 3, current:   image=registry.example.com/app:1.4.0 env={'FEATURE_NEW_CART': 'true', 'DB_HOST': 'db-primary'} memory limit=256Mi
revision 2, target:    image=registry.example.com/app:1.3.0 env={'DB_HOST': 'db-primary'} memory limit=512Mi
after the merge:       image=registry.example.com/app:1.3.0 env={'FEATURE_NEW_CART': 'true', 'DB_HOST': 'db-primary'} memory limit=256Mi
```

That puts the old image back and leaves everything else exactly where the bad deploy left it. The pod comes up on the old code with the new, broken configuration, the tool reports success, and the incident continues with everyone believing it is over. There is no error anywhere.

![Under a header showing the single JSON Patch, a replace of /spec/template sent as application/json-patch+json, two equal columns. Restored: everything under spec.template, from the images of every container to environment variables, resource limits, probes, volumes and the template's own labels. Not restored: spec.replicas, deployment-level fields such as spec.strategy, the Deployment's own labels and annotations, the ConfigMaps, Secrets and autoscaler the template only references, and anything changed outside the Deployment. A bottom band shows the restored and not_restored lists and the replica warning returned with every rollback.](diagrams/02-rollback-scope.svg)

*A rollback restores a pod template. Most of what a bad deploy touches is not in the pod template.*

**The fix is to replace the whole template, with a JSON Patch.** A JSON Patch is a list of operations applied to a document. The Kubernetes page "Rolling Back a Deployment" says what a rollback means: only the Deployment's pod template is rolled back. kubectl's source, `pkg/polymorphichelpers/rollback.go`, shows what `kubectl rollout undo` sends: one JSON Patch with two `replace` operations, `/spec/template` with the target ReplicaSet's template and `/metadata/annotations` with that ReplicaSet's annotations. The project sends the same two, in `src/k8s_responder/remediate.py`:

```python
    patch = [
        {"op": "replace", "path": "/spec/template", "value": template},
        {"op": "replace", "path": "/metadata/annotations", "value": annotations},
    ]

    await call(
        conn.apps_v1.patch_namespaced_deployment,
        name=deployment_name,
        namespace=namespace,
        body=patch,
        _content_type="application/json-patch+json",
    )
```

**A strategic merge patch is the wrong tool here, and the reason is subtle.** It is what `kubectl patch` sends unless told otherwise, and it merges lists by a key rather than replacing them. A container's `env` list is keyed by `name`, so an environment variable that exists in the current template and not in the target is kept rather than removed, and it survives the rollback, as the image-only merge above also shows. "Put it back the way it was" needs a replace of the whole subtree, and the content type is how the request says so.

Here is the patch the fake API server received for an approved `rollback_deployment("checkout")`, as `snippets/rollback.py` prints it:

```json
[
  {
    "op": "replace",
    "path": "/spec/template",
    "value": {
      "metadata": {
        "labels": {
          "app": "checkout"
        }
      },
      "spec": {
        "containers": [
          {
            "env": [
              {
                "name": "DB_HOST",
                "value": "db-primary"
              }
            ],
            "image": "registry.example.com/app:1.3.0",
            "name": "app",
            "resources": {
              "limits": {
                "memory": "512Mi"
              },
              "requests": {
                "memory": "256Mi"
              }
            }
          }
        ]
      }
    }
  },
  {
    "op": "replace",
    "path": "/metadata/annotations",
    "value": {
      "deployment.kubernetes.io/revision": "3",
      "kubernetes.io/change-cause": "rollback to revision 2 via k8s-responder: paged at 02:00"
    }
  }
]
```

The fixture's current revision has `FEATURE_NEW_CART=true` and a 256Mi memory limit. In the patch both are gone: the environment variable is absent, not merged, and the limit is back at 512Mi. The script compares the first operation's value with revision 2's ReplicaSet template and prints `restored template equals revision 2's, pod-template-hash aside: True`. `test_rollback_replaces_the_whole_pod_template` in `tests/test_server.py` asserts it directly:

```python
    # The environment variable the bad deploy introduced is gone, not merged.
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env == {"DB_HOST": "db-primary"}
    assert "FEATURE_NEW_CART" not in env

    # And the memory limit goes back with it.
    assert container["resources"]["limits"]["memory"] == "512Mi"
```

**Two details in that patch are easy to miss.** The `pod-template-hash` label is stripped before the template is sent. The Deployment controller computes that label and applies it to the ReplicaSets it creates, so copying it from an old ReplicaSet into the Deployment's template would be claiming to be that ReplicaSet; `kubectl rollout undo` strips it for the same reason. And nothing is added to the template, so it is revision 2's exactly; carrying the change cause in the second operation rather than in the template is what lets the controller reuse revision 2's ReplicaSet instead of creating a new one.

The second operation is where the reason goes. The Deployment's annotations become the target ReplicaSet's, filtered as kubectl filters them: the Deployment keeps its own values for `deployment.kubernetes.io/revision`, the last-applied configuration and the replica-count annotations, and takes every other annotation from the ReplicaSet. The project then sets `kubernetes.io/change-cause` to the operator's reason, here `rollback to revision 2 via k8s-responder: paged at 02:00`. The Deployment page says the change cause is copied from the Deployment's `kubernetes.io/change-cause` annotation to its revisions, and kubectl's `history.go` shows `kubectl rollout history` reading each revision's cause from its ReplicaSet's annotation, falling back to the pod template's. So the reason the operator typed reaches the rollout history through the Deployment, and the server's log line records it too.

**What a rollback does not restore is returned with every result.** Not in the documentation; in the payload:

```
not_restored:
  - spec.replicas: the replica count lives outside the pod template and is left exactly where it is, including if something scaled it during the incident
  - deployment-level fields such as spec.strategy, minReadySeconds, progressDeadlineSeconds, and revisionHistoryLimit
  - labels on the Deployment object itself, and its revision, last-applied-configuration and replica-count annotations, which keep their current values
  - objects the template only references: ConfigMaps, Secrets, PersistentVolumeClaims, Services, Ingresses, and any HorizontalPodAutoscaler
  - anything changed outside this Deployment, which includes most of what a bad deploy actually touches
warnings: ['spec.replicas was left at 3. A rollback does not restore the replica count.']
```

The warning names the actual number. A caller who does not know that a rollback leaves the replica count alone closes the incident too early; a caller who is told it in the result cannot.

The last list item deserves emphasis. A rollback is a Deployment-scoped operation. If the bad deploy also changed a ConfigMap, rotated a Secret, or applied a database migration, rolling the Deployment back fixes none of it, and may make things worse by putting old code in front of a new schema.

## 7. The ReplicaSet ownership trap

To roll back, the tool needs the previous revision, and a revision is a ReplicaSet. The obvious way to find them is the deployment's own label selector, as `fetch_owned_replica_sets` in `src/k8s_responder/remediate.py` starts:

```python
    result = await call(
        conn.apps_v1.list_namespaced_replica_set,
        namespace=namespace,
        label_selector=label_selector,
    )
```

**A label selector is not an ownership test.** That call returns every ReplicaSet in the namespace whose labels match, and two deployments routinely select the same labels: that is exactly what a canary or a blue/green deployment looks like. The fixture contains that situation on purpose. Alongside `checkout` at revision 3 there is a `checkout-canary` deployment that selects the same `app=checkout` labels, and `snippets/rollback.py` prints what the selector returns:

```
label selector app=checkout returns: ['checkout-canary-2x (rev 2)', 'checkout-1a (rev 1)', 'checkout-2b (rev 2)', 'checkout-3c (rev 3)', 'checkout-canary-9z (rev 9)']
owned by checkout (controller uid):  ['checkout-1a (rev 1)', 'checkout-2b (rev 2)', 'checkout-3c (rev 3)']
```

The canary's revision-2 ReplicaSet is first in the list, ahead of `checkout`'s own revision 2, which is precisely the slot a label-only lookup picks as "the previous revision" of `checkout`. Rolling back to it is not a rollback; it is a cross-deployment overwrite that reports success.

The fix is the controller owner reference, which Kubernetes sets on every ReplicaSet the Deployment controller creates, and which the "Owners and Dependents" page describes as the link from a dependent to its owner. The filter, in `src/k8s_responder/remediate.py`:

```python
    owned = []
    for rs in replica_sets:
        for ref in (rs.metadata.owner_references or []):
            if not getattr(ref, "controller", False):
                continue
            if ref.kind != "Deployment":
                continue
            # Match on uid when both sides have one; uid is unique across
            # deletions and recreations, where a name is not.
            if uid and getattr(ref, "uid", None):
                if ref.uid == uid:
                    owned.append(rs)
                    break
            elif ref.name == name:
                owned.append(rs)
                break
    return owned
```

Matching on the unique identifier (`uid`) rather than the name matters because a uid identifies one object for its whole life, where a deleted deployment's name can be taken by a new one.

This is not a theoretical concern, and the suite proves it. `snippets/ownership_mutant.py` runs the project's suite twice in one process, once as published and once with `owned_replica_sets()` replaced by a pass-through, which is what filtering by label alone amounts to:

```
== the suite as published: exit code 0 ==
78 passed
== the suite with owned_replica_sets as a pass-through: exit code 1 ==
4 failed, 74 passed
FAILED tests/test_server.py::test_rollout_history_only_lists_replicasets_this_deployment_owns
FAILED tests/test_server.py::test_rollback_picks_the_owned_previous_revision
FAILED tests/test_server.py::test_rolling_back_to_another_deployments_revision_is_refused
FAILED tests/test_server.py::test_rollback_replaces_the_whole_pod_template
```

and the second of those failures says exactly what went wrong:

```
>       assert data["restored_images"] == ["registry.example.com/app:1.3.0"]
E       AssertionError: assert ['registry.ex...canary:9.9.9'] == ['registry.ex...om/app:1.3.0']
E         At index 0 diff: 'registry.example.com/canary:9.9.9' != 'registry.example.com/app:1.3.0'
```

`snippets/rollback.py` makes the same substitution for one call and prints the result the model would have read:

```
applied=true from_revision=3 to_revision=2 restored_images=['registry.example.com/canary:9.9.9']
```

The rollback of `checkout` has restored the canary's image, `applied` is `true`, and nothing raised. That is the shape of the bug: it does not fail; it succeeds at the wrong thing.

With the filter in place, `get_rollout_history` for `checkout` returns exactly its own revisions, `[3, 2, 1]`, and asking to roll back to revision 9, which exists in the namespace and belongs to somebody else, is refused with the available list:

```
rollback to revision 9: applied=false outcome="Refused. Revision 9 is not in the history of 'checkout'. Available revisions: 3, 2, 1."
```

## 8. Blast-radius limits, checked before anyone is asked

Some changes should never reach a human at all. Asking somebody to approve a scale to 500 replicas wastes their attention and, worse, trains them to click through prompts.

![Two lanes compare where the same limit runs. The upper lane, crossed out, checks inside the tool body: propose 500, ask the human, the human approves, and the tool refuses anyway. The lower lane, ticked, runs guard_replicas in the resolver before the question is rendered: the call fails with isError and the ask-the-human box is struck through. A table below gives each guard's default and environment variable: 20 replicas, kube-system, kube-public and kube-node-lease closed to writes, and 500 log lines and 40,000 log bytes per call. Closing lines say reads are never restricted and the service account's role is the real control.](diagrams/03-blast-radius.svg)

*A guard inside the tool body runs after the human. A guard inside the resolver runs instead of asking.*

The guards live in the resolver, which the SDK runs before it renders the question, in `src/k8s_responder/remediate.py`:

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

A resolver that raises fails the call without ever producing an elicitation. `test_scaling_past_the_maximum_is_refused_without_asking_anyone` in `tests/test_server.py` asserts all three consequences at once: the call errors, the question list is empty, and the cluster was never touched.

```python
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

`seen == []` is the line that matters. The client in that test is one whose user always approves, and it never got the chance. `snippets/guards.py` makes the same call and prints what the model reads:

```
-- scale_deployment {'deployment_name': 'checkout', 'replicas': 500}
   isError: true
   text:    Error executing tool scale_deployment: Refusing to scale to 500. The configured maximum is 20. A larger change than this should be made deliberately, by a human, with capacity in mind. Set K8S_RESPONDER_MAX_REPLICAS to raise it.
   questions shown: 0
   kubernetes calls: []
   logged:  INFO Tool 'scale_deployment' failed: <the text above>
```

The message reaches the model because of the guard's exception type. The guards raise `PolicyError`, and the script prints its class chain: `k8s_responder.cluster.PolicyError -> k8s_responder.cluster.ClusterError -> mcp.server.mcpserver.exceptions.ToolError`. Under `mcp==2.3.0` a `ToolError` raised in a tool, or in one of its resolvers, comes back as a result with `isError: true` whose text carries the message, and the server logs one line at INFO; any other exception is a crash, and the model reads only `Error executing tool <name>`. [Post 15](../15-devops-responder/index.md) printed that contrast for `ClusterError`. A refusal written for a model to act on is only useful if the model gets to read it.

The protected-namespace guard has the same shape and the same placement, and every resolver calls it first, so all three writing tools refuse a protected namespace before asking. The script tries `restart_pod` and `rollback_deployment` in `kube-system`; both print `questions shown: 0` and `kubernetes calls: []`, with this text:

```
   text:    Error executing tool restart_pod: Refusing to change anything in namespace 'kube-system'. Protected namespaces are: kube-node-lease, kube-public, kube-system. Reading these namespaces is still allowed. Set K8S_RESPONDER_PROTECTED_NAMESPACES to change the list.
```

Note the third sentence. **Reads are never restricted.** Refusing to restart a pod in `kube-system` is not a reason to refuse to look at one; the script's `list_pods` there comes back with `isError=false`, and `test_protected_namespaces_are_still_readable` keeps that path open.

Placement is the whole point, and the script measures it. On a throwaway server it registers a `scale_deployment` whose resolver only asks and whose body calls the same `guard_replicas` after the answer arrives:

```
-- scale_deployment {'deployment_name': 'checkout', 'replicas': 500}
   isError: true
   text identical to the project's refusal above: True
   questions shown: 1
```

The model reads the same refusal either way. The difference is the one line `questions shown: 1`: a person was asked to authorize a change the server was never going to make, which is the upper lane of the figure.

The limits are read from the environment at import, `K8S_RESPONDER_MAX_REPLICAS` and `K8S_RESPONDER_PROTECTED_NAMESPACES`, and the entry point adds `--max-replicas` and `--protect` flags that override them. The configured maximum is enforced against whatever is in force: with it set to 2, a scale to 3 is refused with `The configured maximum is 2.` and, again, `questions shown: 0`.

**These guards are convenience, not security.** They are a local policy in a process the model is talking to, and a bug in this file removes them. The control that actually matters is role-based access control (RBAC): the service account the server runs under. Grant `delete` on `pods` and `patch` on `deployments` and `deployments/scale` in the namespaces meant for remediation, and nowhere else. If the server's role cannot reach a namespace, no bug in this code can change anything there. [Post 19](../19-security/index.md) is the general argument.

## 9. Long rollouts, and why the tasks extension is the wrong tool here

A rollback call returns as soon as the patch is accepted. The rollout it starts takes as long as the deployment's strategy and the pods' readiness probes say it takes, which can be minutes.

This server does not wait. `patch_namespaced_deployment` returns as soon as the API server has accepted the new spec, and the tool verifies what it can verify at that moment, which is that the object reads back with the images it just wrote. It then says so plainly, in the `outcome` `snippets/rollback.py` prints:

```
outcome: "Rolled 'checkout' back from revision 3 to 2 in namespace 'default', by replacing the entire pod template. Kubernetes now rolls the pods according to the deployment's strategy; call list_pods and list_deployments to watch it finish. Reason given: paged at 02:00"
```

`list_deployments` from [post 15](../15-devops-responder/index.md) is the tool that answers "is it finished", by marking a deployment `degraded` when fewer replicas are ready than desired. Polling it is a short loop for the model and costs one API call per check.

The alternative is the tasks extension, `io.modelcontextprotocol/tasks`, covered in [post 09](../09-tasks/index.md), which turns "hold the connection open and hope" into an explicit, pollable lifecycle. It is the right answer when the *server* is doing long work and holds state the client cannot see. It is a poor fit here, because the server is not doing the work: Kubernetes is, and its progress is already readable through an ordinary tool by anyone with `get` on deployments. Wrapping a poll the client can do itself in a task adds a lifecycle to manage and takes nothing away.

The honest caveat is that "verified" here means the spec was accepted, not that the fix worked. A rollback to a revision whose image also fails to pull reports a successful rollback and leaves the service exactly as broken. Verification of *effect* needs the reading tools and a wait, and no tool call can substitute for that.

## 10. When not to build this

**When the same fix is always right.** If a pod always needs restarting under some condition, that is a controller, a liveness probe, or a `restartPolicy`, and it should run without a human and without a model. Automation that needs an approval every time is automation that is not finished.

**When nobody will read the question.** A person who is asked to confirm something at the end of a long agent turn will approve it. Elicitation is a consent mechanism only when the question is rare, specific, and expected. Three tools is a deliberate number.

**When the blast radius is not describable.** Every preview in this file fits in seven short fields. A change that cannot be summarized that way is a change nobody can meaningfully approve, and shipping a prompt for it converts a considered decision into a reflex.

## 11. Make it run: the suite and the scripts behind this post

The project is the post's code, and its suite needs no cluster. From inside `code/15-devops-responder/`:

```bash
uv sync --extra dev                                 # once: the environment, with the test tools
uv run --extra dev pytest                           # 78 passed, for both posts, no Kubernetes anywhere
uv run python -m k8s_responder                      # stdio, what a desktop host spawns
uv run python -m k8s_responder --max-replicas 5     # tighten the replica limit
uv run python -m k8s_responder --protect prod,kube-system   # replace the protected list
```

```
tests\test_diagnose.py ........................                          [ 30%]
tests\test_server.py ................................................... [ 96%]
...                                                                      [100%]

============================= 78 passed in 1.87s ==============================
```

Seventy-eight tests across posts 15 and 16, with no cluster; the count is the result, and the seconds vary by machine. The elicitation loop is exercised in full, because the human is an injectable callback: `answering()` in `tests/test_server.py` builds a `Client(mcp, elicitation_callback=...)` whose user always gives the same answer. One test worth copying outright, `test_every_write_tool_requires_an_answer_before_it_acts`, is parameterized over the set of writing tools and declines each one, then asserts that nothing was deleted or patched, so a fourth writing tool added next year cannot skip the gate.

Six scripts under `snippets/` produce every result, transcript and error message quoted above. Each runs from the series root with no arguments, in a few seconds. The root environment pins `mcp[cli]==2.3.0` and the Kubernetes client, its `dev` extra provides the `pytest` that `ownership_mutant.py` drives, and each script imports the package and the fixture of `tests/conftest.py` from the project's `src/` and `tests/` directories. None needs a cluster, a host, or any network, and none changes a file under `code/`.

```bash
uv run python posts/16-devops-remediation/snippets/approval_wire.py      # sections 2 and 12: the schemas, both MRTR legs, -32021
uv run python posts/16-devops-remediation/snippets/preview_answers.py    # sections 3 and 12: the question, four answers, resolver rounds
uv run python posts/16-devops-remediation/snippets/restart_scale.py      # sections 4 and 5: refusals, read-backs, the no-op
uv run python posts/16-devops-remediation/snippets/rollback.py           # sections 6, 7 and 9: the patch, the scope, ownership
uv run python posts/16-devops-remediation/snippets/ownership_mutant.py   # section 7: the suite with the ownership filter removed
uv run python posts/16-devops-remediation/snippets/guards.py             # sections 8 and 12: the guards, their placement, the limits
```

`approval_wire.py` prints the versions it ran with: `mcp 2.3.0, kubernetes 36.0.3, Python 3.12.12`.

Every transcript in this post comes from the fake cluster. To walk the same path on a real one, point a kubeconfig context at a local cluster, create a `checkout` deployment and a second deployment that selects the same labels, change the first one's image twice, then from a host connected to the server scale it through the approval, decline a second change, and roll it back; `get_rollout_history` and the pods' images show whether the rollback landed on `checkout`'s own pod template, and a scale past the maximum shows the refusal arriving with no question.

## 12. What can go wrong?

Four failures a first deployment of this server actually meets.

**A client that never declared elicitation.** The resolver refuses to ask, because the MRTR page forbids an `inputRequests` entry for a capability the client did not declare, and the call fails before the tool body runs. `snippets/approval_wire.py` connects a client with no `elicitation_callback`:

```
MCPError -32021: Client did not declare the form elicitation capability required by resolver 'k8s_responder.remediate:confirm_scale'
data: {"requiredCapabilities": {"elicitation": {"form": {}}}}
kubernetes calls: []
```

That is `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`), a protocol error rather than an `isError` result, and to a user who sees only that the call failed it looks like a broken tool. Passing an `elicitation_callback` is what declares the capability; a test client built with `Client(mcp)` and nothing else hits the same error.

**A question that changes between rounds.** `snippets/preview_answers.py` gives its throwaway server a second tool whose question carries a reading that moves every time the resolver runs, and a user who always approves:

```
live    resolver runs=11 questions shown=10 distinct=10 ended: InputRequiredRoundsExceededError: Server returned InputRequiredResult for more than 10 rounds; raise input_required_max_rounds on the Client, or use client.session.<method>(..., allow_input_required=True) to drive the loop manually.
```

The user saw ten dialogs and said yes to every one, and nothing happened. The call ends only because the SDK's client stops after ten rounds, its default `input_required_max_rounds`. From the outside it looks as though the answers were ignored.

**A protected list that replaces the default rather than extending it.** `K8S_RESPONDER_PROTECTED_NAMESPACES` and `--protect` set the whole list. `snippets/guards.py` sets the variable to protect one more namespace:

```
K8S_RESPONDER_MAX_REPLICAS=50 K8S_RESPONDER_PROTECTED_NAMESPACES=payments gives
   max_replicas=50 protected=['payments']
```

`kube-system` is no longer on it. An operator who meant to add `payments` has removed the three defaults; write them into the value too.

**More than one worker with the SDK's default `requestState` key.** The state the client echoes is sealed by default under a process-local ephemeral key, which is correct for a single standard input and output (stdio) process and wrong behind a load balancer: a retry that reaches another worker, or a restarted one, cannot be unsealed and is refused. [Post 08](../08-elicitation-and-mrtr/index.md) measures that failure and its fix: pass `RequestStateSecurity(keys=[...])` with the same key of at least 32 bytes to every worker.

## Common pitfalls

1. **Adding an `approved: bool` parameter.** It lands in the published input schema, so the model fills it in; use a resolved parameter, and assert on the exact property set the tool publishes rather than on the absence of one name.
2. **Putting a live reading in the approval message.** The recorded answer is matched by a digest of the rendered question and the resolver re-runs every round, so a timestamp or a moving count makes the call ask until the round limit; build the message from the arguments alone.
3. **Rolling back only the container images.** A strategic merge keeps the bad deploy's environment variable and memory limit, because it merges the container's lists by key; replace `/spec/template` with a JSON Patch `replace`.
4. **Finding the previous revision by label selector.** A canary selects the same labels, so the lookup can return another deployment's ReplicaSet; filter by the controller owner reference's `uid`, or a rollback overwrites one workload with another's template and reports success.
5. **Reporting success from the API's acknowledgement.** An accepted delete means marked for deletion, and an accepted scale can be overridden by an autoscaler seconds later; read the object back and report the observed value next to the requested one.
6. **Treating the guards as security.** They live in the same process as the bug not yet found; RBAC on the service account is the control, and the guards only stop an obvious mistake before a human is bothered with it.

## Further reading

- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28. The `input_required` result, the retry with a different JSON-RPC id, and the rule against asking for a capability the client did not declare. <https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr>
- Specification, *"Elicitation"*, revision 2026-07-28. Form mode, and the three actions this server branches on. <https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation>
- Specification, *"Tools"*, revision 2026-07-28. The `ToolAnnotations` note quoted in section 2, the requirement that clients treat annotations as untrusted, and the rule that clients should prompt for confirmation. <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- Hungerford, O., Morrow, S., and Chang, L., *"Tool Annotations as Risk Vocabulary: What Hints Can and Can't Do"* (2026-03-16), Model Context Protocol blog. What `destructiveHint` can and cannot do. <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
- Kubernetes, *"Rolling Back a Deployment"*. Why only the pod template rolls back, why revisions are ReplicaSets, and where the rollout history's change cause comes from. <https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-back-a-deployment>
- kubectl source, `pkg/polymorphichelpers/rollback.go` and `history.go`. The two operations `kubectl rollout undo` sends, the annotations it keeps from the Deployment, and where `kubectl rollout history` reads a change cause. <https://github.com/kubernetes/kubectl/tree/master/pkg/polymorphichelpers>
- Kubernetes, *"Update API Objects in Place Using kubectl patch"*. How a strategic merge patch merges the `containers` list by `name`, and the JSON Patch type. <https://kubernetes.io/docs/tasks/manage-kubernetes-objects/update-api-object-kubectl-patch/>
- Kubernetes, *"Owners and Dependents"*. Controller owner references, the authoritative link a label selector is not. <https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/>
- MCP Python SDK, release 2.3.0. `Resolve`, `Elicit`, `ElicitationResult`, `ToolError`, and `Client` with an `elicitation_callback`. <https://github.com/modelcontextprotocol/python-sdk>
- [code/15-devops-responder/](../../code/15-devops-responder/). The complete project, with the tests run in section 11.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 17 - Project 3: A deep research browser](../17-research-browser/index.md):** the next project, where the hard problem stops being permission and becomes throwing away most of a page before it reaches the model's context.
- **[Post 14 - Project 1: Writes, transactions, and an audit trail](../14-database-writes/index.md):** the same propose, preview, approve shape applied to database writes, with the audit trail this server does not keep.
