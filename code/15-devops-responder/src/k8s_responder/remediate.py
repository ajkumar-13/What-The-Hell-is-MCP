"""The tools that change the cluster. Post 16.

This is the only module in the package that can write. Everything in `inspect`
and `diagnose` reads; the `delete_`, `patch_`, and `create_` calls live here and
nowhere else. If you want to run this server in a mode that genuinely cannot
touch anything, delete the import of this module from `__init__.py` and the
capability is gone, not merely discouraged.

Three things are true of every tool below.

**A change is previewed before it is approved.** `ChangePreview` is a pure
function of the tool's arguments: the operation, the target, the exact API call,
whether it can be undone, and how far the damage reaches. The same object is put
to the human in the approval prompt and returned in the result, so what was
approved and what was reported are provably the same text.

**The question is built from the arguments and nothing else.** The SDK pins a
recorded answer to a SHA-256 digest of the rendered question, and it re-runs the
resolver on every round of the exchange. Put a live replica count or a timestamp
in the message and the second round renders a different question, the digest
stops matching, the answer looks stale, and the call loops until it hits
`input_required_max_rounds`. So the preview describes the *intended* change,
which is knowable from the arguments, and the *observed* before-and-after state
is read in the tool body and returned in the result.

**Blast-radius limits are checked in the resolver, before the human is asked.**
A resolver that raises fails the call without ever rendering an elicitation, so
nobody is invited to approve a scale to 500 replicas that the server was always
going to refuse.

And one thing that is not true: the `destructive_hint` annotation on these tools
does not make a host ask for confirmation, and `read_only_hint` on the other
modules does not stop one from asking. Annotations are hints. A host may show
them, ignore them, or invent its own policy. The approval below is real because
the server does not act until it has an answer, not because of a flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from kubernetes import client
from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    Resolve,
)
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from .app import log, mcp
from .cluster import ClusterError, PolicyError, call, current, limits, timestamp

REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANNOTATION = "kubernetes.io/change-cause"
POD_TEMPLATE_HASH = "pod-template-hash"

DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)

# Scaling to a count is idempotent: doing it twice lands in the same place.
SCALING = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


# --------------------------------------------------------------------------
# The approval question
# --------------------------------------------------------------------------


class Approval(BaseModel):
    """What the human is asked before anything changes."""

    approve: bool = Field(
        description="Apply this change to the cluster.",
    )
    reason: str = Field(
        default="",
        description="Optional note recorded in the server log alongside the change.",
    )


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

    def as_question(self) -> str:
        """Render the preview as the approval prompt.

        Deterministic by construction: same arguments, same string, same digest.
        """
        return "\n".join(
            [
                "Approve this change to the cluster?",
                "",
                f"  operation:    {self.operation}",
                f"  target:       {self.target}",
                f"  namespace:    {self.namespace}",
                f"  change:       {self.change}",
                f"  api call:     {self.api_call}",
                f"  reversible:   {self.reversible}",
                f"  blast radius: {self.blast_radius}",
            ]
        )


# --------------------------------------------------------------------------
# Blast-radius guards
# --------------------------------------------------------------------------


def guard_namespace(namespace: str) -> None:
    """Refuse to write to a protected namespace.

    This is a local guard and it is not a security boundary. RBAC is. It exists
    because the cheapest way for an operator to be sure a model never restarts
    the DNS pods is for the tool that could do it to refuse by name.
    """
    protected = limits().protected_namespaces
    if namespace in protected:
        raise PolicyError(
            f"Refusing to change anything in namespace {namespace!r}. "
            f"Protected namespaces are: {', '.join(sorted(protected))}. "
            "Reading these namespaces is still allowed. Set "
            "K8S_RESPONDER_PROTECTED_NAMESPACES to change the list."
        )


def guard_replicas(replicas: int) -> None:
    """Refuse a replica count outside the configured range."""
    cap = limits().max_replicas
    if replicas < 0:
        raise PolicyError(
            f"Refusing to scale to {replicas}. A replica count cannot be negative."
        )
    if replicas > cap:
        raise PolicyError(
            f"Refusing to scale to {replicas}. The configured maximum is {cap}. "
            "A larger change than this should be made deliberately, by a human, "
            "with capacity in mind. Set K8S_RESPONDER_MAX_REPLICAS to raise it."
        )


# --------------------------------------------------------------------------
# Result shapes
# --------------------------------------------------------------------------


@dataclass
class RestartResult:
    """The outcome of restarting one pod."""

    approved: bool
    applied: bool
    pod: str
    namespace: str
    controller: str
    outcome: str
    preview: ChangePreview
    warnings: list[str]


@dataclass
class ScaleResult:
    """The outcome of scaling one deployment, with observed before and after."""

    approved: bool
    applied: bool
    deployment: str
    namespace: str
    previous_replicas: int
    requested_replicas: int
    observed_replicas: int
    outcome: str
    preview: ChangePreview
    warnings: list[str]


@dataclass
class RolloutRevision:
    """One entry of a deployment's revision history."""

    revision: int
    images: list[str]
    replicas: int
    ready_replicas: int
    created: str
    current: bool
    replica_set: str


@dataclass
class RolloutHistory:
    """A deployment's revision history, newest first."""

    deployment: str
    namespace: str
    current_revision: int
    revisions: list[RolloutRevision]


@dataclass
class RollbackResult:
    """The outcome of a rollback, and an exact account of its scope.

    `restored` and `not_restored` exist because a rollback that quietly puts
    back the image and leaves a bad environment variable in place is worse than
    no rollback: it reports success and the incident continues. This tool
    replaces the entire pod template, and says so, and says what that still does
    not cover.
    """

    approved: bool
    applied: bool
    deployment: str
    namespace: str
    from_revision: int
    to_revision: int
    previous_images: list[str]
    restored_images: list[str]
    restored: list[str]
    not_restored: list[str]
    outcome: str
    preview: ChangePreview
    warnings: list[str]


# What replacing spec.template does and does not put back. Stated once, returned
# on every rollback, and repeated in the tool's docstring.
RESTORED_BY_ROLLBACK = [
    "container images, for every container and init container in the template",
    "command, args, and environment variables, including valueFrom references",
    "resource requests and limits",
    "liveness, readiness, and startup probes",
    "volumes, volume mounts, securityContext, and serviceAccountName",
    "the pod template's own labels and annotations",
    "every other field under spec.template",
]

NOT_RESTORED_BY_ROLLBACK = [
    "spec.replicas: the replica count lives outside the pod template and is left "
    "exactly where it is, including if something scaled it during the incident",
    "deployment-level fields such as spec.strategy, minReadySeconds, "
    "progressDeadlineSeconds, and revisionHistoryLimit",
    "labels and annotations on the Deployment object itself",
    "objects the template only references: ConfigMaps, Secrets, PersistentVolume"
    "Claims, Services, Ingresses, and any HorizontalPodAutoscaler",
    "anything changed outside this Deployment, which includes most of what a "
    "bad deploy actually touches",
]


# --------------------------------------------------------------------------
# Previews, pure functions of the tool arguments
# --------------------------------------------------------------------------


def preview_restart(pod_name: str, namespace: str) -> ChangePreview:
    return ChangePreview(
        operation="restart pod",
        target=f"pod/{pod_name}",
        namespace=namespace,
        change=(
            f"delete pod/{pod_name} so its controller creates a replacement with a "
            "new name and a new IP address"
        ),
        api_call=f"DELETE /api/v1/namespaces/{namespace}/pods/{pod_name}",
        reversible=(
            "No. The pod is gone and everything held in its memory with it. The "
            "replacement is a new pod, not this one resumed."
        ),
        blast_radius=(
            "One pod. Requests in flight on it are dropped. If the pod turns out "
            "to have no controlling owner this tool refuses, because nothing "
            "would recreate it."
        ),
    )


def preview_scale(deployment_name: str, replicas: int, namespace: str) -> ChangePreview:
    return ChangePreview(
        operation="scale deployment",
        target=f"deployment/{deployment_name}",
        namespace=namespace,
        change=f"set spec.replicas to {replicas}",
        api_call=(
            f"PATCH /apis/apps/v1/namespaces/{namespace}/deployments/"
            f"{deployment_name}/scale"
        ),
        reversible=(
            "Partly. The replica count can be set back, but pods terminated on the "
            "way down do not come back, and pods created on the way up start cold."
        ),
        blast_radius=(
            f"Every pod of this deployment. Scaling to {replicas} "
            + (
                "stops the deployment entirely and it will serve no traffic."
                if replicas == 0
                else "changes how much traffic it can serve and how much cluster "
                "capacity it holds."
            )
        ),
    )


def preview_rollback(
    deployment_name: str, namespace: str, revision: int
) -> ChangePreview:
    target = (
        f"revision {revision}" if revision > 0 else "the revision before the current one"
    )
    return ChangePreview(
        operation="roll back deployment",
        target=f"deployment/{deployment_name}",
        namespace=namespace,
        change=(
            f"replace the whole of spec.template with the pod template from {target}, "
            "which restores images, environment, resources, and probes together"
        ),
        api_call=(
            f"PATCH /apis/apps/v1/namespaces/{namespace}/deployments/"
            f"{deployment_name} (application/json-patch+json, replace /spec/template)"
        ),
        reversible=(
            "Yes, by rolling forward again. The rollback itself creates a new "
            "revision, so nothing in the history is lost."
        ),
        blast_radius=(
            "Every pod of this deployment, replaced according to its rollout "
            "strategy. spec.replicas is NOT changed, and nothing outside this "
            "Deployment is touched, so a bad ConfigMap or Secret survives this."
        ),
    )


# --------------------------------------------------------------------------
# Resolvers. Each one guards, then asks.
# --------------------------------------------------------------------------


def confirm_restart(pod_name: str, namespace: str) -> Elicit[Approval]:
    guard_namespace(namespace)
    return Elicit(preview_restart(pod_name, namespace).as_question(), Approval)


def confirm_scale(
    deployment_name: str, replicas: int, namespace: str
) -> Elicit[Approval]:
    guard_namespace(namespace)
    guard_replicas(replicas)
    return Elicit(
        preview_scale(deployment_name, replicas, namespace).as_question(), Approval
    )


def confirm_rollback(
    deployment_name: str, namespace: str, revision: int
) -> Elicit[Approval]:
    guard_namespace(namespace)
    return Elicit(
        preview_rollback(deployment_name, namespace, revision).as_question(), Approval
    )


def _refusal(approval: ElicitationResult[Approval], what: str) -> str | None:
    """Turn a non-approval into the sentence we will report, or None if approved."""
    if isinstance(approval, DeclinedElicitation):
        return f"Not applied. The user declined to approve {what}."
    if isinstance(approval, CancelledElicitation):
        return f"Not applied. The approval prompt for {what} was dismissed."
    if isinstance(approval, AcceptedElicitation) and not approval.data.approve:
        return f"Not applied. The user answered no to {what}."
    return None


def _note(approval: ElicitationResult[Approval]) -> str:
    if isinstance(approval, AcceptedElicitation):
        return approval.data.reason.strip()
    return ""


# --------------------------------------------------------------------------
# ReplicaSet ownership
# --------------------------------------------------------------------------


def owned_replica_sets(replica_sets: list[Any], deployment: Any) -> list[Any]:
    """Keep only the ReplicaSets this Deployment actually controls.

    A label selector is not enough and this is not a theoretical concern. Two
    deployments in one namespace that both select `app=web`, which is exactly
    what a blue/green or a canary looks like, each see the other's ReplicaSets
    through `list_namespaced_replica_set(label_selector=...)`. Sort those by
    revision and "the previous revision" can be a pod template that belongs to a
    different deployment entirely. Rolling back to it is not a rollback, it is a
    cross-deployment overwrite that reports success.

    The controller owner reference is the authoritative link. Kubernetes sets it
    on every ReplicaSet the Deployment controller creates, and `controller: true`
    distinguishes the owner from a merely-related object.
    """
    meta = deployment.metadata
    uid = getattr(meta, "uid", None)
    name = meta.name

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


def revision_of(obj: Any) -> int:
    """Read the revision annotation off a ReplicaSet or Deployment."""
    annotations = (obj.metadata.annotations or {}) if obj.metadata else {}
    try:
        return int(annotations.get(REVISION_ANNOTATION, 0))
    except (TypeError, ValueError):
        return 0


def images_of(template: Any) -> list[str]:
    """Container images in a pod template, main containers first."""
    if template is None or template.spec is None:
        return []
    containers = list(template.spec.containers or [])
    containers += list(template.spec.init_containers or [])
    return [c.image or "" for c in containers]


_serializer: Any = None


def template_as_dict(template: Any) -> dict:
    """A pod template as plain JSON, with the controller's own label removed.

    `pod-template-hash` is computed and applied by the Deployment controller. It
    identifies the ReplicaSet the template belongs to, so copying it from an old
    ReplicaSet into the Deployment's template would be claiming to be that
    ReplicaSet. `kubectl rollout undo` strips it for the same reason.
    """
    global _serializer
    if _serializer is None:
        _serializer = client.ApiClient()

    body = _serializer.sanitize_for_serialization(template) or {}
    labels = (body.get("metadata") or {}).get("labels")
    if isinstance(labels, dict):
        labels.pop(POD_TEMPLATE_HASH, None)
    return body


async def fetch_deployment(name: str, namespace: str) -> Any:
    conn = await current()
    try:
        return await call(
            conn.apps_v1.read_namespaced_deployment, name=name, namespace=namespace
        )
    except ClusterError as err:
        if err.status == 404:
            raise ClusterError(
                f"No deployment named {name!r} in namespace {namespace!r}.", status=404
            ) from err
        raise


async def fetch_owned_replica_sets(deployment: Any, namespace: str) -> list[Any]:
    """List the deployment's ReplicaSets: selector to narrow, ownership to decide."""
    conn = await current()
    selector = deployment.spec.selector if deployment.spec else None
    match_labels = (selector.match_labels or {}) if selector else {}
    label_selector = ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))

    result = await call(
        conn.apps_v1.list_namespaced_replica_set,
        namespace=namespace,
        label_selector=label_selector,
    )
    return owned_replica_sets(list(result.items), deployment)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(title="Restart a pod", annotations=DESTRUCTIVE)
async def restart_pod(
    pod_name: str,
    approval: Annotated[ElicitationResult[Approval], Resolve(confirm_restart)],
    namespace: str = "default",
) -> RestartResult:
    """Restart a pod by deleting it, once a human approves.

    Kubernetes has no restart verb for a pod. Deleting one that a controller
    owns is the restart: the ReplicaSet notices the shortfall and creates a
    replacement. The replacement is a new pod with a new name and a new IP.

    The tool refuses if the pod has no controlling owner, because deleting a
    bare pod means it is simply gone. That case is not covered by the approval
    the human gave, so it is not something this tool will do on their behalf.

    Args:
        pod_name: Exact pod name, for example "checkout-7d8f9-abc12".
        namespace: Kubernetes namespace. Defaults to "default".
    """
    preview = preview_restart(pod_name, namespace)

    refused = _refusal(approval, f"deleting pod {pod_name}")
    if refused is not None:
        return RestartResult(
            approved=False,
            applied=False,
            pod=pod_name,
            namespace=namespace,
            controller="",
            outcome=refused,
            preview=preview,
            warnings=[],
        )

    conn = await current()
    try:
        pod = await call(
            conn.core_v1.read_namespaced_pod, name=pod_name, namespace=namespace
        )
    except ClusterError as err:
        if err.status == 404:
            return RestartResult(
                approved=True,
                applied=False,
                pod=pod_name,
                namespace=namespace,
                controller="",
                outcome=(
                    f"Nothing done. No pod named {pod_name!r} exists in namespace "
                    f"{namespace!r}."
                ),
                preview=preview,
                warnings=[],
            )
        raise

    controller = ""
    for ref in (pod.metadata.owner_references or []):
        if getattr(ref, "controller", False):
            controller = f"{ref.kind}/{ref.name}"
            break

    if not controller:
        return RestartResult(
            approved=True,
            applied=False,
            pod=pod_name,
            namespace=namespace,
            controller="",
            outcome=(
                f"Refused. Pod {pod_name!r} has no controlling owner, so deleting it "
                "would not restart it, it would remove it permanently. Delete it "
                "with kubectl if that is genuinely what you want."
            ),
            preview=preview,
            warnings=[
                "A pod with no controller is usually either created by hand or "
                "left behind by a deleted controller."
            ],
        )

    await call(conn.core_v1.delete_namespaced_pod, name=pod_name, namespace=namespace)

    note = _note(approval)
    log.info(
        "deleted pod %s/%s owned by %s, reason=%s",
        namespace,
        pod_name,
        controller,
        note or "(none)",
    )

    # Verify rather than assume. Kubernetes accepting a delete means the object
    # is marked for deletion, not that it is gone or that a replacement exists.
    warnings: list[str] = []
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
        if err.status != 404:
            warnings.append("Could not read the pod back to confirm the delete.")

    outcome = (
        f"Deleted pod {pod_name!r} in namespace {namespace!r}. It is {state}. "
        f"{controller} is responsible for creating the replacement; call list_pods "
        "to see it appear."
    )
    if note:
        outcome += f" Reason given: {note}"

    return RestartResult(
        approved=True,
        applied=True,
        pod=pod_name,
        namespace=namespace,
        controller=controller,
        outcome=outcome,
        preview=preview,
        warnings=warnings,
    )


@mcp.tool(title="Scale a deployment", annotations=SCALING)
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    approval: Annotated[ElicitationResult[Approval], Resolve(confirm_scale)],
    namespace: str = "default",
) -> ScaleResult:
    """Set a deployment's replica count, once a human approves.

    The count is checked against a configured maximum before the human is asked,
    so an obviously wrong number is refused rather than put to a vote. Scaling
    to zero is allowed: stopping a service that is melting a database is a
    legitimate first response, and the preview says plainly that it will serve
    no traffic.

    Args:
        deployment_name: Name of the deployment, for example "checkout".
        replicas: Desired replica count. Must be between 0 and the configured
            maximum, which defaults to 20.
        namespace: Kubernetes namespace. Defaults to "default".
    """
    preview = preview_scale(deployment_name, replicas, namespace)

    refused = _refusal(
        approval, f"scaling {deployment_name} to {replicas} replica(s)"
    )
    if refused is not None:
        return ScaleResult(
            approved=False,
            applied=False,
            deployment=deployment_name,
            namespace=namespace,
            previous_replicas=-1,
            requested_replicas=replicas,
            observed_replicas=-1,
            outcome=refused,
            preview=preview,
            warnings=[],
        )

    conn = await current()
    deployment = await fetch_deployment(deployment_name, namespace)
    previous = (
        deployment.spec.replicas
        if deployment.spec and deployment.spec.replicas is not None
        else 0
    )

    if previous == replicas:
        return ScaleResult(
            approved=True,
            applied=False,
            deployment=deployment_name,
            namespace=namespace,
            previous_replicas=previous,
            requested_replicas=replicas,
            observed_replicas=previous,
            outcome=(
                f"Nothing to do. {deployment_name!r} is already set to {replicas} "
                "replica(s)."
            ),
            preview=preview,
            warnings=[],
        )

    await call(
        conn.apps_v1.patch_namespaced_deployment_scale,
        name=deployment_name,
        namespace=namespace,
        body={"spec": {"replicas": replicas}},
        _content_type="application/merge-patch+json",
    )

    note = _note(approval)
    log.info(
        "scaled deployment %s/%s from %s to %s, reason=%s",
        namespace,
        deployment_name,
        previous,
        replicas,
        note or "(none)",
    )

    warnings: list[str] = []
    try:
        after = await fetch_deployment(deployment_name, namespace)
        observed = (
            after.spec.replicas
            if after.spec and after.spec.replicas is not None
            else replicas
        )
    except ClusterError:
        observed = replicas
        warnings.append("Could not read the deployment back to confirm the new count.")

    if observed != replicas:
        warnings.append(
            f"The deployment reads back as {observed} replica(s), not {replicas}. "
            "A HorizontalPodAutoscaler or an admission webhook may be overriding "
            "this setting."
        )

    direction = "up" if replicas > previous else "down"
    outcome = (
        f"Scaled {deployment_name!r} {direction} from {previous} to {replicas} "
        f"replica(s) in namespace {namespace!r}. spec.replicas now reads "
        f"{observed}. The pods themselves take time to follow; call list_pods to "
        "watch them converge."
    )
    if note:
        outcome += f" Reason given: {note}"

    return ScaleResult(
        approved=True,
        applied=True,
        deployment=deployment_name,
        namespace=namespace,
        previous_replicas=previous,
        requested_replicas=replicas,
        observed_replicas=observed,
        outcome=outcome,
        preview=preview,
        warnings=warnings,
    )


@mcp.tool(title="Show rollout history", annotations=READ_ONLY)
async def get_rollout_history(
    deployment_name: str, namespace: str = "default"
) -> RolloutHistory:
    """List a deployment's revisions, newest first.

    Each revision is a ReplicaSet the Deployment controller kept. Call this
    before `rollback_deployment` to see which revision you are going back to and
    what image it was running.

    The listing is filtered by controller owner reference, not only by label
    selector. Two deployments that select the same labels, which is what a
    canary looks like, would otherwise appear in each other's history.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace. Defaults to "default".
    """
    deployment = await fetch_deployment(deployment_name, namespace)
    current_revision = revision_of(deployment)
    replica_sets = await fetch_owned_replica_sets(deployment, namespace)

    revisions = [
        RolloutRevision(
            revision=revision_of(rs),
            images=images_of(rs.spec.template if rs.spec else None),
            replicas=(rs.status.replicas or 0) if rs.status else 0,
            ready_replicas=(rs.status.ready_replicas or 0) if rs.status else 0,
            created=timestamp(rs.metadata.creation_timestamp),
            current=revision_of(rs) == current_revision,
            replica_set=rs.metadata.name,
        )
        for rs in replica_sets
    ]
    revisions.sort(key=lambda r: r.revision, reverse=True)

    return RolloutHistory(
        deployment=deployment_name,
        namespace=namespace,
        current_revision=current_revision,
        revisions=revisions,
    )


@mcp.tool(title="Roll back a deployment", annotations=DESTRUCTIVE)
async def rollback_deployment(
    deployment_name: str,
    approval: Annotated[ElicitationResult[Approval], Resolve(confirm_rollback)],
    namespace: str = "default",
    revision: int = 0,
) -> RollbackResult:
    """Roll a deployment back to a previous revision, once a human approves.

    **What this restores.** The whole pod template. The patch is a JSON Patch
    that replaces `/spec/template` outright with the template from the target
    revision's ReplicaSet, which is what `kubectl rollout undo` does. Images,
    command, args, environment variables, resource requests and limits, probes,
    volumes, and securityContext all go back together. A rollback that restored
    only the container image would report success while leaving the environment
    variable that actually broke the service in place, and the incident would
    continue with everyone believing it was over.

    **What this does not restore.** `spec.replicas`, which lives outside the pod
    template and is deliberately left alone. Deployment-level fields such as
    `spec.strategy` and `minReadySeconds`. Labels and annotations on the
    Deployment object. And everything the template merely references: ConfigMaps,
    Secrets, PersistentVolumeClaims, Services, and any HorizontalPodAutoscaler.
    The same lists are returned in `restored` and `not_restored` so the answer
    travels with the result.

    Args:
        deployment_name: Name of the deployment.
        namespace: Kubernetes namespace. Defaults to "default".
        revision: Revision number to return to. 0, the default, means the
            revision immediately before the current one. Use
            `get_rollout_history` to see what is available.
    """
    preview = preview_rollback(deployment_name, namespace, revision)
    target_text = f"revision {revision}" if revision > 0 else "the previous revision"

    refused = _refusal(
        approval, f"rolling {deployment_name} back to {target_text}"
    )
    if refused is not None:
        return _rollback_not_applied(
            deployment_name, namespace, preview, refused, approved=False
        )

    conn = await current()
    deployment = await fetch_deployment(deployment_name, namespace)
    current_revision = revision_of(deployment)
    previous_images = images_of(
        deployment.spec.template if deployment.spec else None
    )

    replica_sets = await fetch_owned_replica_sets(deployment, namespace)
    if not replica_sets:
        return _rollback_not_applied(
            deployment_name,
            namespace,
            preview,
            (
                f"Refused. No ReplicaSet is owned by deployment {deployment_name!r}, "
                "so there is no recorded history to roll back to."
            ),
            approved=True,
            from_revision=current_revision,
            previous_images=previous_images,
        )

    ordered = sorted(replica_sets, key=revision_of, reverse=True)

    if revision > 0:
        target = next((rs for rs in ordered if revision_of(rs) == revision), None)
        if target is None:
            available = ", ".join(str(revision_of(rs)) for rs in ordered) or "none"
            return _rollback_not_applied(
                deployment_name,
                namespace,
                preview,
                (
                    f"Refused. Revision {revision} is not in the history of "
                    f"{deployment_name!r}. Available revisions: {available}."
                ),
                approved=True,
                from_revision=current_revision,
                previous_images=previous_images,
            )
    else:
        target = next(
            (rs for rs in ordered if revision_of(rs) < current_revision), None
        )
        if target is None:
            return _rollback_not_applied(
                deployment_name,
                namespace,
                preview,
                (
                    f"Refused. {deployment_name!r} is at revision {current_revision} "
                    "and there is no earlier revision to go back to."
                ),
                approved=True,
                from_revision=current_revision,
                previous_images=previous_images,
            )

    target_revision = revision_of(target)
    if target_revision == current_revision:
        return _rollback_not_applied(
            deployment_name,
            namespace,
            preview,
            (
                f"Nothing to do. {deployment_name!r} is already at revision "
                f"{current_revision}."
            ),
            approved=True,
            from_revision=current_revision,
            previous_images=previous_images,
        )

    template = template_as_dict(target.spec.template if target.spec else None)
    if not template:
        return _rollback_not_applied(
            deployment_name,
            namespace,
            preview,
            (
                f"Refused. Revision {target_revision} has no readable pod template, "
                "so there is nothing to restore."
            ),
            approved=True,
            from_revision=current_revision,
            previous_images=previous_images,
        )

    note = _note(approval)

    # Record why the template changed, the way `kubectl rollout undo` does. This
    # is the one field we add to the restored template rather than restore.
    change_cause = (
        f"rollback to revision {target_revision} via k8s-responder"
        + (f": {note}" if note else "")
    )
    template.setdefault("metadata", {}).setdefault("annotations", {})[
        CHANGE_CAUSE_ANNOTATION
    ] = change_cause

    # A JSON Patch `replace`, not a strategic merge. A strategic merge patch
    # merges lists by key, so a container's environment variable that exists in
    # the current template and not in the target would survive the rollback.
    # `replace` swaps the whole subtree, which is the only thing that means
    # "put it back the way it was".
    patch = [{"op": "replace", "path": "/spec/template", "value": template}]

    await call(
        conn.apps_v1.patch_namespaced_deployment,
        name=deployment_name,
        namespace=namespace,
        body=patch,
        _content_type="application/json-patch+json",
    )

    log.info(
        "rolled back deployment %s/%s from revision %s to %s, reason=%s",
        namespace,
        deployment_name,
        current_revision,
        target_revision,
        note or "(none)",
    )

    restored_images = images_of(target.spec.template if target.spec else None)
    warnings: list[str] = []
    try:
        after = await fetch_deployment(deployment_name, namespace)
        observed = images_of(after.spec.template if after.spec else None)
        if observed and observed != restored_images:
            warnings.append(
                f"The deployment reads back with images {observed}, not "
                f"{restored_images}. Something else may have written to it."
            )
    except ClusterError:
        warnings.append("Could not read the deployment back to confirm the rollback.")

    replicas_now = (
        deployment.spec.replicas
        if deployment.spec and deployment.spec.replicas is not None
        else 0
    )
    warnings.append(
        f"spec.replicas was left at {replicas_now}. A rollback does not restore "
        "the replica count."
    )

    outcome = (
        f"Rolled {deployment_name!r} back from revision {current_revision} to "
        f"{target_revision} in namespace {namespace!r}, by replacing the entire pod "
        "template. Kubernetes now rolls the pods according to the deployment's "
        "strategy; call list_pods and list_deployments to watch it finish."
    )
    if note:
        outcome += f" Reason given: {note}"

    return RollbackResult(
        approved=True,
        applied=True,
        deployment=deployment_name,
        namespace=namespace,
        from_revision=current_revision,
        to_revision=target_revision,
        previous_images=previous_images,
        restored_images=restored_images,
        restored=list(RESTORED_BY_ROLLBACK),
        not_restored=list(NOT_RESTORED_BY_ROLLBACK),
        outcome=outcome,
        preview=preview,
        warnings=warnings,
    )


def _rollback_not_applied(
    deployment_name: str,
    namespace: str,
    preview: ChangePreview,
    outcome: str,
    approved: bool,
    from_revision: int = 0,
    previous_images: list[str] | None = None,
) -> RollbackResult:
    """A rollback that did not happen, still carrying its preview and scope."""
    return RollbackResult(
        approved=approved,
        applied=False,
        deployment=deployment_name,
        namespace=namespace,
        from_revision=from_revision,
        to_revision=0,
        previous_images=previous_images or [],
        restored_images=[],
        restored=list(RESTORED_BY_ROLLBACK),
        not_restored=list(NOT_RESTORED_BY_ROLLBACK),
        outcome=outcome,
        preview=preview,
        warnings=[],
    )
