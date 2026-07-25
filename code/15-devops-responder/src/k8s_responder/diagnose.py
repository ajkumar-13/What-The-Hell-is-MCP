"""Crash-loop diagnosis. Post 15.

A crash loop is not visible in any single place. The pod phase says `Running`.
The container state says `waiting`, reason `CrashLoopBackOff`, which tells you
there is a loop but not why. The reason lives in the *previous* container state,
as a terminated record with an exit code, and often only the events explain how
the kubelet got there. Reading one of those three and stopping is how you end up
telling someone their pod is "restarting" when it is out of memory.

`analyse()` below is a pure function of (pod object, events). It touches no
network, which is why the test suite can throw two dozen synthetic pods at it
without a cluster anywhere in sight. The tools are thin wrappers that fetch and
then call it.

Like `inspect`, this module imports nothing that can change the cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

from .app import mcp
from .cluster import call, clamp, current, limits
from .inspect import (
    READ_ONLY,
    ClusterEvent,
    ContainerState,
    container_states,
    fetch_pod,
    fetch_pod_events,
    flatten_event,
    summarise_pod,
)

# Container waiting reasons, grouped by what they actually mean.
_IMAGE_REASONS = {
    "ImagePullBackOff",
    "ErrImagePull",
    "ErrImageNeverPull",
    "InvalidImageName",
    "RegistryUnavailable",
}
_CONFIG_REASONS = {
    "CreateContainerConfigError",
    "CreateContainerError",
    "ConfigError",
    "InvalidImageName",
    "RunContainerError",
}
_CRASH_REASONS = {"CrashLoopBackOff"}

# Exit codes worth translating. The 128+N codes are a signal number in disguise.
_EXIT_CODES: dict[int, str] = {
    0: (
        "the process exited successfully, which under restartPolicy Always is "
        "still a restart; a container whose main process finishes is a crash "
        "loop as far as Kubernetes is concerned"
    ),
    1: "a generic application error; the log tail almost always names it",
    2: "shell misuse, usually a bad flag or a missing argument in the command",
    126: "the entrypoint was found but is not executable",
    127: "the entrypoint was not found in the image, or a library it needs is missing",
    128: "an invalid exit argument",
    137: "SIGKILL, code 128+9; the OOM killer or a liveness probe that gave up waiting",
    139: "SIGSEGV, code 128+11; a segmentation fault inside the process",
    143: "SIGTERM, code 128+15; a shutdown request the process did not handle in time",
}


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


@dataclass
class Diagnosis:
    """What is wrong with one pod, why we think so, and what to do next."""

    pod: str
    namespace: str
    healthy: bool
    verdict: str
    confidence: str
    summary: str
    likely_cause: str
    restart_count: int
    findings: list[Finding]
    next_steps: list[str]


@dataclass
class CrashLoopReport:
    """Every unhealthy pod in a namespace, each with its own diagnosis."""

    namespace: str
    scanned: int
    unhealthy: int
    verdicts: dict[str, int]
    diagnoses: list[Diagnosis]


class Verdict(NamedTuple):
    """What one check concluded. Internal to this module."""

    verdict: str
    summary: str
    likely_cause: str
    confidence: str
    next_steps: list[str]


# --------------------------------------------------------------------------
# The analysis, as a pure function
# --------------------------------------------------------------------------


def analyse(pod, events: list[ClusterEvent] | None = None) -> Diagnosis:
    """Correlate pod status, container statuses, and events into one verdict.

    The order of the checks below is the order of specificity, not the order of
    severity. `OOMKilled` is checked before `CrashLoopBackOff` because a pod
    being OOM-killed in a loop reports *both*, and "out of memory" is the
    actionable one. Reporting "CrashLoopBackOff" when the kernel told you the
    exact reason is how a diagnosis becomes useless.
    """
    events = events or []
    name = pod.metadata.name
    namespace = pod.metadata.namespace or ""
    phase = (pod.status.phase if pod.status else "") or "Unknown"
    containers = container_states(pod)
    restarts = sum(c.restart_count for c in containers)

    findings: list[Finding] = [
        Finding(source="status.phase", signal="phase", detail=phase)
    ]
    if pod.metadata.deletion_timestamp:
        findings.append(
            Finding(
                source="metadata.deletionTimestamp",
                signal="terminating",
                detail="the pod has been asked to terminate",
            )
        )

    for c in containers:
        findings.extend(_container_findings(c))
    findings.extend(_event_findings(events))

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
    for check in checks:
        found = check(pod, phase, containers, events, restarts)
        if found is not None:
            return Diagnosis(
                pod=name,
                namespace=namespace,
                healthy=False,
                verdict=found.verdict,
                confidence=found.confidence,
                summary=found.summary,
                likely_cause=found.likely_cause,
                restart_count=restarts,
                findings=findings,
                next_steps=found.next_steps,
            )

    healthy = phase in {"Running", "Succeeded"} and all(
        c.ready or c.state == "terminated" for c in containers
    )
    if healthy:
        return Diagnosis(
            pod=name,
            namespace=namespace,
            healthy=True,
            verdict="healthy",
            confidence="high",
            summary=f"Pod {name} is {phase.lower()} with all containers ready.",
            likely_cause="",
            restart_count=restarts,
            findings=findings,
            next_steps=[],
        )

    not_ready = [c.name for c in containers if not c.ready]
    return Diagnosis(
        pod=name,
        namespace=namespace,
        healthy=False,
        verdict="unknown",
        confidence="low",
        summary=(
            f"Pod {name} is in phase {phase} and container(s) "
            f"{', '.join(not_ready) or 'unknown'} are not ready, but none of the "
            "usual failure signatures matched."
        ),
        likely_cause="Not determined from pod status, container status, or events.",
        restart_count=restarts,
        findings=findings,
        next_steps=[
            f"get_logs(pod_name={name!r}, namespace={namespace!r})",
            f"describe_pod(pod_name={name!r}, namespace={namespace!r})",
            f"get_events(namespace={namespace!r}, event_type='Warning')",
        ],
    )


def _container_findings(c: ContainerState) -> list[Finding]:
    out: list[Finding] = []
    where = f"status.containerStatuses[{c.name}]"
    if c.state == "waiting" and c.reason:
        out.append(
            Finding(
                source=f"{where}.state.waiting",
                signal=c.reason,
                detail=c.message or "no message",
            )
        )
    if c.state == "terminated" and c.reason:
        out.append(
            Finding(
                source=f"{where}.state.terminated",
                signal=c.reason,
                detail=f"exit code {c.exit_code}",
            )
        )
    if c.last_state == "terminated" and (c.last_reason or c.last_exit_code is not None):
        out.append(
            Finding(
                source=f"{where}.lastState.terminated",
                signal=c.last_reason or "Terminated",
                detail=f"previous instance exited with code {c.last_exit_code}",
            )
        )
    if c.restart_count:
        out.append(
            Finding(
                source=f"{where}.restartCount",
                signal="restarts",
                detail=str(c.restart_count),
            )
        )
    return out


def _event_findings(events: list[ClusterEvent]) -> list[Finding]:
    return [
        Finding(
            source="events",
            signal=e.reason,
            detail=f"{e.message} (seen {e.count} time(s))",
        )
        for e in events
        if e.type == "Warning"
    ]


# Each check returns a `Verdict` or None. Splitting them out keeps `analyse`
# readable and lets a test aim at exactly one signature at a time.


def _check_terminating(pod, phase, containers, events, restarts):
    if not pod.metadata.deletion_timestamp:
        return None
    return Verdict(
        verdict="terminating",
        summary=f"Pod {pod.metadata.name} is being deleted.",
        likely_cause=(
            "A deletion is in progress. This is not a failure unless it never "
            "finishes, which usually means a finalizer is stuck."
        ),
        confidence="high",
        next_steps=["Wait, then list_pods to confirm the replacement is scheduled."],
    )


def _check_oom(pod, phase, containers, events, restarts):
    hit = [
        c
        for c in containers
        if "OOMKilled" in (c.reason, c.last_reason)
        or (c.last_exit_code == 137 and _event_reason(events, "OOMKilling"))
    ]
    if not hit:
        return None
    names = ", ".join(c.name for c in hit)
    return Verdict(
        verdict="oom_killed",
        summary=f"Container(s) {names} were killed for exceeding their memory limit.",
        likely_cause=(
            "The container's working set went past resources.limits.memory and the "
            "kernel OOM killer ended it. Restarting will not help; either the limit "
            "is too low for real traffic or the process is leaking."
        ),
        confidence="high",
        next_steps=[
            f"get_logs(pod_name={pod.metadata.name!r}, previous=True) to see what it "
            "was doing when it died",
            "Compare resources.requests.memory and resources.limits.memory against "
            "observed usage before raising the limit",
        ],
    )


def _check_image(pod, phase, containers, events, restarts):
    hit = [c for c in containers if c.reason in _IMAGE_REASONS]
    if not hit:
        return None
    c = hit[0]
    return Verdict(
        verdict="image_pull_failure",
        summary=f"Container {c.name} cannot pull its image.",
        likely_cause=(
            f"Kubernetes reported {c.reason} for image {c.image!r}. The usual causes "
            "are a tag that was never pushed, a typo in the tag, a private registry "
            "with no imagePullSecret, or a registry that is down."
        ),
        confidence="high",
        next_steps=[
            "Confirm the exact image tag exists in the registry",
            "Check that the pod's serviceAccount has an imagePullSecret for a "
            "private registry",
            "get_rollout_history to find the last revision whose image did pull",
        ],
    )


def _check_config(pod, phase, containers, events, restarts):
    hit = [c for c in containers if c.reason in _CONFIG_REASONS]
    if not hit:
        return None
    c = hit[0]
    return Verdict(
        verdict="config_error",
        summary=f"Container {c.name} cannot be created: {c.reason}.",
        likely_cause=(
            "The container spec references something that does not exist. A missing "
            "ConfigMap or Secret key is by far the most common cause, followed by a "
            "malformed command. Kubernetes said: "
            f"{c.message or 'no detail given'}"
        ),
        confidence="high",
        next_steps=[
            f"describe_pod(pod_name={pod.metadata.name!r}) and read the events for "
            "the missing object's name",
            "Confirm every ConfigMap and Secret named in the pod spec exists in "
            "this namespace",
        ],
    )


def _check_unschedulable(pod, phase, containers, events, restarts):
    failed = _event_reason(events, "FailedScheduling")
    if phase != "Pending" or failed is None:
        return None
    return Verdict(
        verdict="unschedulable",
        summary=f"Pod {pod.metadata.name} cannot be scheduled onto any node.",
        likely_cause=(
            "The scheduler found no node that satisfies the pod's requirements. "
            f"It said: {failed.message}"
        ),
        confidence="high",
        next_steps=[
            "Read the scheduler message above; it names the constraint that failed "
            "on each node",
            "Check resources.requests against node capacity, and any nodeSelector, "
            "affinity, or taint the pod has to satisfy",
        ],
    )


def _check_crash_loop(pod, phase, containers, events, restarts):
    hit = [c for c in containers if c.reason in _CRASH_REASONS]
    if not hit:
        # A container can be crash-looping between back-off windows, when its
        # state is briefly `running` again. Restarts plus a non-zero previous
        # exit code is the same condition seen a moment earlier.
        hit = [
            c
            for c in containers
            if c.restart_count >= limits().high_restart_count
            and c.last_state == "terminated"
            and (c.last_exit_code or 0) != 0
        ]
        if not hit:
            return None
        confidence = "medium"
    else:
        confidence = "high"

    c = hit[0]
    code = c.last_exit_code if c.last_exit_code is not None else c.exit_code
    # `_EXIT_CODES.get(code or -1)` would be a bug: exit code 0 is falsy, and 0
    # is the one code most worth explaining, because "it exited successfully"
    # reads like good news right up until you notice it is in a restart loop.
    meaning = _EXIT_CODES.get(code, "") if code is not None else ""
    cause = (
        f"Container {c.name} has restarted {c.restart_count} time(s). Its previous "
        f"instance exited with code {code}"
    )
    cause += f", which is {meaning}." if meaning else "."
    if not meaning:
        cause += " The exit code is application-defined; the log tail is the only source."

    return Verdict(
        verdict="crash_loop",
        summary=f"Container {c.name} is in a crash loop.",
        likely_cause=cause,
        confidence=confidence,
        next_steps=[
            f"get_logs(pod_name={pod.metadata.name!r}, container={c.name!r}, "
            "previous=True) is the log that matters; the current instance has "
            "usually produced nothing yet",
            f"get_events(namespace={pod.metadata.namespace!r}, event_type='Warning')",
            "If the exit code appeared right after a deploy, compare against "
            "get_rollout_history",
        ],
    )


def _check_probe_failure(pod, phase, containers, events, restarts):
    unhealthy = _event_reason(events, "Unhealthy")
    if unhealthy is None:
        return None
    if all(c.ready for c in containers):
        return None
    return Verdict(
        verdict="probe_failure",
        summary=f"A health probe on pod {pod.metadata.name} is failing.",
        likely_cause=(
            "The kubelet reported an Unhealthy event, so a liveness or readiness "
            f"probe is not passing: {unhealthy.message}. A failing liveness probe "
            "restarts the container; a failing readiness probe only removes it from "
            "Service endpoints."
        ),
        confidence="medium",
        next_steps=[
            "Check the probe path, port, and initialDelaySeconds against how long "
            "the process actually takes to start",
            f"get_logs(pod_name={pod.metadata.name!r}) to see whether the process is "
            "up but slow, or genuinely broken",
        ],
    )


def _check_failed_phase(pod, phase, containers, events, restarts):
    if phase != "Failed":
        return None
    reason = (pod.status.reason if pod.status else "") or "no reason recorded"
    return Verdict(
        verdict="failed",
        summary=f"Pod {pod.metadata.name} is in phase Failed.",
        likely_cause=(
            f"Kubernetes recorded reason {reason!r}. Eviction and node pressure are "
            "the usual causes when no container reports an error of its own."
        ),
        confidence="high",
        next_steps=[
            f"describe_pod(pod_name={pod.metadata.name!r})",
            f"get_events(namespace={pod.metadata.namespace!r}, event_type='Warning')",
        ],
    )


def _check_restarting(pod, phase, containers, events, restarts):
    threshold = limits().high_restart_count
    if restarts <= threshold:
        return None
    if not any(c.ready for c in containers):
        return None
    return Verdict(
        verdict="restarting",
        summary=f"Pod {pod.metadata.name} is up but has restarted {restarts} times.",
        likely_cause=(
            "The pod is serving now, but it has restarted more than the threshold of "
            f"{threshold}. That is a crash loop that happens to be in its healthy "
            "phase at the moment you looked."
        ),
        confidence="medium",
        next_steps=[
            f"get_logs(pod_name={pod.metadata.name!r}, previous=True)",
            "Watch it: list_pods again in a minute and compare the restart count",
        ],
    )


def _check_pending(pod, phase, containers, events, restarts):
    if phase != "Pending":
        return None
    return Verdict(
        verdict="pending",
        summary=f"Pod {pod.metadata.name} is still Pending.",
        likely_cause=(
            "The pod exists but no container has started. With no FailedScheduling "
            "event this is usually an image still downloading or a volume still "
            "attaching."
        ),
        confidence="low",
        next_steps=[
            f"get_events(namespace={pod.metadata.namespace!r}) to see how far the "
            "kubelet has got",
            "Wait and call list_pods again before treating this as a failure",
        ],
    )


def _event_reason(events: list[ClusterEvent], reason: str) -> ClusterEvent | None:
    for e in events:
        if e.reason == reason:
            return e
    return None


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool(title="Diagnose a pod", annotations=READ_ONLY)
async def diagnose_pod(pod_name: str, namespace: str = "default") -> Diagnosis:
    """Explain why one pod is unhealthy, correlating status, containers, and events.

    This is `describe_pod` with the reasoning already done. It reads the same
    three sources and returns a verdict, the field each observation came from,
    and the next call worth making. Use it when you know which pod is broken;
    use `find_crash_loops` when you do not.

    Args:
        pod_name: Exact pod name.
        namespace: Kubernetes namespace. Defaults to "default".
    """
    pod = await fetch_pod(pod_name, namespace)
    events = await fetch_pod_events(pod_name, namespace)
    return analyse(pod, events)


@mcp.tool(title="Find crash loops", annotations=READ_ONLY)
async def find_crash_loops(namespace: str = "default", limit: int = 10) -> CrashLoopReport:
    """Scan a namespace and diagnose every pod that is not healthy.

    Two API calls for the whole namespace, not two per pod: the events are
    fetched once and matched to pods locally. Healthy pods are counted but not
    described, because a report that lists forty working pods buries the one
    that is broken.

    Args:
        namespace: Kubernetes namespace. Defaults to "default".
        limit: Maximum number of diagnoses to return, clamped to 25.
    """
    limit = clamp(limit, 1, 25)
    conn = await current()
    pods = await call(conn.core_v1.list_namespaced_pod, namespace=namespace)

    try:
        raw_events = await call(
            conn.core_v1.list_namespaced_event, namespace=namespace
        )
        by_pod: dict[str, list[ClusterEvent]] = {}
        for item in raw_events.items:
            involved = item.involved_object
            if involved is None or involved.kind != "Pod":
                continue
            by_pod.setdefault(involved.name, []).append(flatten_event(item))
        for bucket in by_pod.values():
            bucket.sort(key=lambda e: e.last_seen)
    except Exception:  # noqa: BLE001 - events are optional context, never fatal
        by_pod = {}

    now = datetime.now(timezone.utc)
    threshold = limits().high_restart_count

    interesting = []
    for pod in pods.items:
        row = summarise_pod(pod, now)
        if row.phase != "Running" or row.restarts > threshold or "0/" in row.ready:
            interesting.append(pod)

    diagnoses = [analyse(p, by_pod.get(p.metadata.name, [])) for p in interesting]
    diagnoses = [d for d in diagnoses if not d.healthy][:limit]

    verdicts: dict[str, int] = {}
    for d in diagnoses:
        verdicts[d.verdict] = verdicts.get(d.verdict, 0) + 1

    return CrashLoopReport(
        namespace=namespace,
        scanned=len(pods.items),
        unhealthy=len(diagnoses),
        verdicts=verdicts,
        diagnoses=diagnoses,
    )


# Re-exported so `remediate` can describe a deployment's pods without importing
# `inspect` twice. Keeping the name here documents that diagnosis is read-only.
__all__ = [
    "Diagnosis",
    "CrashLoopReport",
    "Finding",
    "analyse",
    "diagnose_pod",
    "find_crash_loops",
]
