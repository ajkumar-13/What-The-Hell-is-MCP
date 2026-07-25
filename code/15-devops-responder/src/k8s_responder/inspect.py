"""Read-only tools. Post 15.

The read/write split in this server is structural, not a matter of discipline.
This module imports `cluster` for the connection and the `list_namespaced_*` /
`read_namespaced_*` calls, and nothing else. There is no `delete_`, no `patch_`,
and no `create_` anywhere below, so there is no code path from any tool here to
a change in the cluster.

That is the enforcement. The `read_only_hint` annotation on each tool is a
*hint* to the host, and hints are advisory: nothing in the protocol stops a tool
from claiming to be read-only and then deleting a namespace, and no host is
obliged to do anything in particular with the flag. It does not suppress an
approval dialog and it is not a permission. The reason these tools cannot write
is that they contain no writing code.

The other theme of post 15 is size. A pod that has been restarting for two days
can produce hundreds of megabytes of logs, and the naive tool hands all of it to
the model. `get_logs` caps lines, caps bytes, keeps the *newest* end of the
stream, and reports precisely how much it threw away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mcp_types import ToolAnnotations

from .app import mcp
from .cluster import (
    ClusterError,
    call,
    clamp,
    current,
    format_age,
    limits,
    timestamp,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


# --------------------------------------------------------------------------
# Result shapes
#
# Every one of these uses class-body annotations. A class that only assigns
# attributes in __init__ has no type hints for the SDK to read, and you get
# `outputSchema: null` with no warning at all.
# --------------------------------------------------------------------------


@dataclass
class PodSummary:
    """One row of a pod listing."""

    name: str
    namespace: str
    phase: str
    ready: str
    restarts: int
    age: str
    node: str
    reason: str


@dataclass
class PodList:
    """Every pod in a namespace, with the counts worth noticing up front."""

    namespace: str
    total: int
    running: int
    unhealthy: int
    pods: list[PodSummary]


@dataclass
class ContainerState:
    """One container inside a pod, current state and the state before it.

    `last_*` is where a crash loop actually shows up. A container that is
    restarting is *waiting* right now; the exit code that explains why lives in
    the terminated record of the previous instance.
    """

    name: str
    image: str
    ready: bool
    restart_count: int
    state: str
    reason: str
    message: str
    exit_code: int | None
    started_at: str
    last_state: str
    last_reason: str
    last_exit_code: int | None


@dataclass
class PodCondition:
    """One entry of `status.conditions`."""

    type: str
    status: str
    reason: str
    message: str


@dataclass
class ClusterEvent:
    """One Kubernetes event, flattened."""

    type: str
    reason: str
    object: str
    message: str
    count: int
    first_seen: str
    last_seen: str


@dataclass
class PodDetail:
    """Everything about one pod that helps explain why it is unhealthy."""

    name: str
    namespace: str
    phase: str
    node: str
    age: str
    labels: dict[str, str]
    containers: list[ContainerState]
    conditions: list[PodCondition]
    events: list[ClusterEvent]


@dataclass
class EventList:
    """Recent events in a namespace, newest first."""

    namespace: str
    returned: int
    warnings: int
    events: list[ClusterEvent]


@dataclass
class LogChunk:
    """A bounded slice of a container's log, and an honest account of the rest.

    The truncation fields are not decoration. A model that is handed a silently
    shortened log will confidently reason about a stack trace that was cut in
    half. Telling it how many bytes and lines were dropped, and from which end,
    lets it ask for more or say it cannot tell.
    """

    pod: str
    namespace: str
    container: str
    previous: bool
    lines_requested: int
    lines_returned: int
    bytes_returned: int
    truncated: bool
    dropped_bytes: int
    dropped_lines: int
    note: str
    logs: str


@dataclass
class DeploymentSummary:
    """One deployment and whether its replicas have caught up."""

    name: str
    namespace: str
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    updated_replicas: int
    images: list[str]
    degraded: bool


@dataclass
class DeploymentList:
    """Every deployment in a namespace."""

    namespace: str
    total: int
    degraded: int
    deployments: list[DeploymentSummary]


# --------------------------------------------------------------------------
# Flattening helpers, shared with diagnose.py
# --------------------------------------------------------------------------


def container_states(pod) -> list[ContainerState]:
    """Flatten `status.containerStatuses` into something a model can read.

    Init containers are included after the main ones. A pod that never gets past
    its init container looks identical to a healthy pod if you only inspect
    `containerStatuses`.
    """
    statuses = list(pod.status.container_statuses or []) if pod.status else []
    statuses += list(pod.status.init_container_statuses or []) if pod.status else []

    out: list[ContainerState] = []
    for cs in statuses:
        state_name, reason, message, exit_code, started = _read_state(cs.state)
        last_name, last_reason, _, last_exit, _ = _read_state(cs.last_state)
        out.append(
            ContainerState(
                name=cs.name,
                image=cs.image or "",
                ready=bool(cs.ready),
                restart_count=cs.restart_count or 0,
                state=state_name,
                reason=reason,
                message=message,
                exit_code=exit_code,
                started_at=started,
                last_state=last_name,
                last_reason=last_reason,
                last_exit_code=last_exit,
            )
        )
    return out


def _read_state(state) -> tuple[str, str, str, int | None, str]:
    """Reduce a V1ContainerState union to (name, reason, message, exit_code, started)."""
    if state is None:
        return ("none", "", "", None, "")
    if state.running:
        return ("running", "", "", None, timestamp(state.running.started_at))
    if state.waiting:
        return (
            "waiting",
            state.waiting.reason or "",
            state.waiting.message or "",
            None,
            "",
        )
    if state.terminated:
        term = state.terminated
        return (
            "terminated",
            term.reason or "",
            term.message or "",
            term.exit_code,
            timestamp(term.started_at),
        )
    return ("none", "", "", None, "")


def flatten_event(event) -> ClusterEvent:
    """One V1 event object as a flat record."""
    involved = event.involved_object
    return ClusterEvent(
        type=event.type or "",
        reason=event.reason or "",
        object=f"{involved.kind}/{involved.name}" if involved else "",
        message=(event.message or "").strip(),
        count=event.count or 1,
        first_seen=timestamp(event.first_timestamp),
        last_seen=timestamp(event.last_timestamp or event.first_timestamp),
    )


def summarise_pod(pod, now: datetime | None = None) -> PodSummary:
    """One pod as a listing row, including the reason it is not happy."""
    now = now or datetime.now(timezone.utc)
    containers = list(pod.spec.containers or []) if pod.spec else []
    statuses = list(pod.status.container_statuses or []) if pod.status else []

    ready = sum(1 for cs in statuses if cs.ready)
    restarts = sum(cs.restart_count or 0 for cs in statuses)

    reason = ""
    if pod.status is not None and pod.status.reason:
        reason = pod.status.reason
    for cs in statuses:
        if cs.state is not None and cs.state.waiting and cs.state.waiting.reason:
            reason = cs.state.waiting.reason
            break

    return PodSummary(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace or "",
        phase=(pod.status.phase if pod.status else "") or "Unknown",
        ready=f"{ready}/{len(containers)}",
        restarts=restarts,
        age=format_age(pod.metadata.creation_timestamp, now),
        node=(pod.spec.node_name if pod.spec else "") or "unscheduled",
        reason=reason,
    )


async def fetch_pod(pod_name: str, namespace: str):
    """Read one pod, turning a 404 into a sentence rather than a stack trace."""
    conn = await current()
    try:
        return await call(
            conn.core_v1.read_namespaced_pod, name=pod_name, namespace=namespace
        )
    except ClusterError as err:
        if err.status == 404:
            raise ClusterError(
                f"No pod named {pod_name!r} in namespace {namespace!r}.", status=404
            ) from err
        raise


async def fetch_pod_events(pod_name: str, namespace: str) -> list[ClusterEvent]:
    """Events whose involvedObject is this pod, oldest first.

    Events expire (one hour by default), so an empty list means "nothing
    recently", not "nothing ever".
    """
    conn = await current()
    try:
        result = await call(
            conn.core_v1.list_namespaced_event,
            namespace=namespace,
            field_selector=f"involvedObject.name={pod_name}",
        )
    except ClusterError:
        # Event access is a separate RBAC verb from pod access. Losing events is
        # a degraded diagnosis, not a failed one.
        return []

    events = [flatten_event(e) for e in result.items]
    events.sort(key=lambda e: e.last_seen)
    return events


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(title="List pods", annotations=READ_ONLY)
async def list_pods(namespace: str = "default") -> PodList:
    """List the pods in a namespace with status, readiness, and restart counts.

    This is the first call in almost every investigation. The `unhealthy` count
    is the number of pods that are either not Running or have restarted more
    than the configured threshold.

    Args:
        namespace: Kubernetes namespace to list. Defaults to "default".
    """
    conn = await current()
    result = await call(conn.core_v1.list_namespaced_pod, namespace=namespace)

    now = datetime.now(timezone.utc)
    pods = [summarise_pod(p, now) for p in result.items]
    threshold = limits().high_restart_count

    return PodList(
        namespace=namespace,
        total=len(pods),
        running=sum(1 for p in pods if p.phase == "Running"),
        unhealthy=sum(
            1 for p in pods if p.phase != "Running" or p.restarts > threshold
        ),
        pods=pods,
    )


@mcp.tool(title="Describe a pod", annotations=READ_ONLY)
async def describe_pod(pod_name: str, namespace: str = "default") -> PodDetail:
    """Read one pod in full: container states, conditions, and its events.

    Use this once `list_pods` has shown you which pod is unhappy. Three sources
    have to be read together to explain a failure, and this tool returns all
    three: the pod phase, the per-container state (including the *previous*
    state, where a crash loop's exit code lives), and the events the kubelet and
    scheduler recorded against the pod.

    Args:
        pod_name: Exact pod name, for example "checkout-7d8f9-abc12".
        namespace: Kubernetes namespace. Defaults to "default".
    """
    pod = await fetch_pod(pod_name, namespace)
    events = await fetch_pod_events(pod_name, namespace)

    conditions = [
        PodCondition(
            type=c.type or "",
            status=c.status or "",
            reason=c.reason or "",
            message=(c.message or "").strip(),
        )
        for c in ((pod.status.conditions if pod.status else None) or [])
    ]

    return PodDetail(
        name=pod.metadata.name,
        namespace=pod.metadata.namespace or namespace,
        phase=(pod.status.phase if pod.status else "") or "Unknown",
        node=(pod.spec.node_name if pod.spec else "") or "unscheduled",
        age=format_age(pod.metadata.creation_timestamp),
        labels=dict(pod.metadata.labels or {}),
        containers=container_states(pod),
        conditions=conditions,
        events=events,
    )


@mcp.tool(title="Get namespace events", annotations=READ_ONLY)
async def get_events(
    namespace: str = "default",
    event_type: str = "",
    limit: int = 30,
) -> EventList:
    """List recent events in a namespace, newest first.

    Events are the cluster narrating itself: scheduling decisions, image pulls,
    probe failures, evictions, back-off timers. They expire after an hour by
    default, so an empty result means nothing happened recently rather than
    nothing ever happened.

    Args:
        namespace: Kubernetes namespace. Defaults to "default".
        event_type: Filter to "Warning" or "Normal". Empty string means both.
        limit: Maximum events to return, clamped to 100.
    """
    limit = clamp(limit, 1, 100)
    conn = await current()
    result = await call(conn.core_v1.list_namespaced_event, namespace=namespace)

    events = [flatten_event(e) for e in result.items]
    wanted = event_type.strip()
    if wanted:
        events = [e for e in events if e.type == wanted]

    events.sort(key=lambda e: e.last_seen, reverse=True)
    events = events[:limit]

    return EventList(
        namespace=namespace,
        returned=len(events),
        warnings=sum(1 for e in events if e.type == "Warning"),
        events=events,
    )


@mcp.tool(title="Get container logs", annotations=READ_ONLY)
async def get_logs(
    pod_name: str,
    namespace: str = "default",
    container: str = "",
    tail_lines: int = 100,
    previous: bool = False,
    max_bytes: int = 20_000,
) -> LogChunk:
    """Read the tail of a container's log, bounded in both lines and bytes.

    Two bounds, because one is not enough. `tail_lines` bounds what crosses the
    wire from the API server. `max_bytes` bounds what reaches the model, which
    matters because a single line of structured JSON logging can be kilobytes on
    its own. The newest end of the log is kept, since that is where the failure
    is, and the result says exactly how much was dropped.

    Set `previous=True` to read the log of the *crashed* instance rather than
    the one currently starting up. For a pod in CrashLoopBackOff that is nearly
    always the log you want; the current instance has usually produced nothing.

    Args:
        pod_name: Exact pod name.
        namespace: Kubernetes namespace. Defaults to "default".
        container: Container name. Required for multi-container pods; empty
            string picks the pod's default container.
        tail_lines: Lines to request from the API server, clamped to the
            configured maximum.
        previous: Read the previous, terminated instance of the container.
        max_bytes: Byte ceiling on the returned text, clamped to the configured
            maximum.
    """
    cfg = limits()
    tail_lines = clamp(tail_lines, 1, cfg.max_tail_lines)
    max_bytes = clamp(max_bytes, 1_000, cfg.max_log_bytes)

    conn = await current()
    kwargs = {
        "name": pod_name,
        "namespace": namespace,
        "tail_lines": tail_lines,
        "previous": previous,
    }
    if container:
        kwargs["container"] = container

    try:
        raw = await call(conn.core_v1.read_namespaced_pod_log, **kwargs)
    except ClusterError as err:
        if err.status == 404:
            return _empty_log(
                pod_name,
                namespace,
                container,
                previous,
                tail_lines,
                f"No pod named {pod_name!r} in namespace {namespace!r}.",
            )
        if err.status == 400 and previous:
            return _empty_log(
                pod_name,
                namespace,
                container,
                previous,
                tail_lines,
                "No previous instance of this container has logs. The container "
                "has either never restarted, or its previous log was already "
                "rotated away.",
            )
        raise

    raw = raw or ""
    kept, dropped_bytes, dropped_lines = _tail_bytes(raw, max_bytes)

    if not kept:
        note = "The container produced no output for this instance."
    elif dropped_bytes:
        note = (
            f"Truncated to the most recent {max_bytes} bytes. "
            f"{dropped_lines} older line(s) and {dropped_bytes} byte(s) were "
            "dropped from the start of this slice. Raise max_bytes or lower "
            "tail_lines to see a different window."
        )
    else:
        note = f"Complete tail of the last {tail_lines} line(s)."

    return LogChunk(
        pod=pod_name,
        namespace=namespace,
        container=container,
        previous=previous,
        lines_requested=tail_lines,
        lines_returned=kept.count("\n") + (1 if kept and not kept.endswith("\n") else 0),
        bytes_returned=len(kept.encode("utf-8")),
        truncated=dropped_bytes > 0,
        dropped_bytes=dropped_bytes,
        dropped_lines=dropped_lines,
        note=note,
        logs=kept,
    )


def _empty_log(
    pod_name: str,
    namespace: str,
    container: str,
    previous: bool,
    tail_lines: int,
    note: str,
) -> LogChunk:
    return LogChunk(
        pod=pod_name,
        namespace=namespace,
        container=container,
        previous=previous,
        lines_requested=tail_lines,
        lines_returned=0,
        bytes_returned=0,
        truncated=False,
        dropped_bytes=0,
        dropped_lines=0,
        note=note,
        logs="",
    )


def _tail_bytes(text: str, max_bytes: int) -> tuple[str, int, int]:
    """Keep the last `max_bytes` bytes of `text`, snapped to a line boundary.

    Returns (kept, dropped_bytes, dropped_lines). Splitting on a byte boundary
    inside a multi-byte character would produce mojibake, and splitting mid-line
    produces a fragment that reads like a different error than it is, so the cut
    lands on a newline.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, 0, 0

    window = encoded[-max_bytes:]
    newline = window.find(b"\n")
    if newline != -1 and newline + 1 < len(window):
        window = window[newline + 1 :]

    kept = window.decode("utf-8", errors="replace")
    dropped_bytes = len(encoded) - len(window)
    dropped_lines = text.count("\n") - kept.count("\n")
    return kept, dropped_bytes, max(dropped_lines, 0)


@mcp.tool(title="List deployments", annotations=READ_ONLY)
async def list_deployments(namespace: str = "default") -> DeploymentList:
    """List deployments in a namespace with their replica and image status.

    A deployment is `degraded` here when fewer replicas are ready than desired.
    That is the signal that a rollout is stuck or that pods are crashing faster
    than they come up.

    Args:
        namespace: Kubernetes namespace. Defaults to "default".
    """
    conn = await current()
    result = await call(conn.apps_v1.list_namespaced_deployment, namespace=namespace)

    deployments: list[DeploymentSummary] = []
    for dep in result.items:
        desired = dep.spec.replicas if dep.spec and dep.spec.replicas is not None else 0
        ready = (dep.status.ready_replicas or 0) if dep.status else 0
        containers = []
        if dep.spec and dep.spec.template and dep.spec.template.spec:
            containers = dep.spec.template.spec.containers or []

        deployments.append(
            DeploymentSummary(
                name=dep.metadata.name,
                namespace=dep.metadata.namespace or namespace,
                desired_replicas=desired,
                ready_replicas=ready,
                available_replicas=(dep.status.available_replicas or 0)
                if dep.status
                else 0,
                updated_replicas=(dep.status.updated_replicas or 0) if dep.status else 0,
                images=[c.image or "" for c in containers],
                degraded=ready < desired,
            )
        )

    return DeploymentList(
        namespace=namespace,
        total=len(deployments),
        degraded=sum(1 for d in deployments if d.degraded),
        deployments=deployments,
    )
