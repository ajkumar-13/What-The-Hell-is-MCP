"""A fake Kubernetes API, so the suite needs no cluster.

The seam is `cluster.use()`. Everything in the package reaches the API through
`cluster.current()`, which returns a `Connection` holding a CoreV1Api and an
AppsV1Api. Nothing type-checks those two objects, so a pair of fakes that answer
the same method names is indistinguishable from a real cluster to every line of
code under test, including `cluster.call()` and its `asyncio.to_thread` hop.

Two decisions make these fakes worth having.

**They return real `kubernetes.client` model objects.** `V1Pod`, `V1ContainerStatus`,
`CoreV1Event`. A dict-shaped fake would let the code under test read
`pod["status"]["phase"]` and pass, and then fail against a real cluster where the
attribute is `pod.status.phase` and a missing status is `None` rather than a
KeyError. The nested-Optional shape of these models is most of what the
production code has to survive.

**They raise real `ApiException`s.** The 404 and 400 branches in `inspect` and
`remediate` are the ones that decide whether a missing pod is a sentence or a
stack trace, so they are exercised with the exception the real client raises.

Each fake also records the thread every call ran on. The kubernetes client is
synchronous, and a call that lands on the event loop stalls the transport; the
recording turns that into an assertion instead of a code review comment.
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from kubernetes.client import (
    CoreV1Event,
    CoreV1EventList,
    V1Container,
    V1ContainerState,
    V1ContainerStateRunning,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1Deployment,
    V1DeploymentList,
    V1DeploymentSpec,
    V1DeploymentStatus,
    V1EnvVar,
    V1LabelSelector,
    V1ObjectMeta,
    V1ObjectReference,
    V1OwnerReference,
    V1Pod,
    V1PodCondition,
    V1PodList,
    V1PodSpec,
    V1PodStatus,
    V1PodTemplateSpec,
    V1ReplicaSet,
    V1ReplicaSetList,
    V1ReplicaSetSpec,
    V1ReplicaSetStatus,
    V1ResourceRequirements,
)
from kubernetes.client.rest import ApiException

from k8s_responder import cluster

NOW = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Builders for synthetic objects
# --------------------------------------------------------------------------


def waiting(reason: str, message: str = "") -> V1ContainerState:
    return V1ContainerState(
        waiting=V1ContainerStateWaiting(reason=reason, message=message)
    )


def running(started: datetime | None = None) -> V1ContainerState:
    return V1ContainerState(
        running=V1ContainerStateRunning(started_at=started or NOW - timedelta(hours=2))
    )


def terminated(
    exit_code: int, reason: str = "Error", message: str = ""
) -> V1ContainerState:
    return V1ContainerState(
        terminated=V1ContainerStateTerminated(
            exit_code=exit_code,
            reason=reason,
            message=message,
            started_at=NOW - timedelta(minutes=5),
            finished_at=NOW - timedelta(minutes=4),
        )
    )


def container_status(
    name: str = "app",
    image: str = "registry.example.com/app:1.4.0",
    ready: bool = True,
    restarts: int = 0,
    state: V1ContainerState | None = None,
    last_state: V1ContainerState | None = None,
) -> V1ContainerStatus:
    return V1ContainerStatus(
        name=name,
        image=image,
        image_id=f"docker://{name}",
        ready=ready,
        restart_count=restarts,
        state=state if state is not None else running(),
        last_state=last_state,
    )


def make_pod(
    name: str,
    namespace: str = "default",
    phase: str = "Running",
    statuses: list[V1ContainerStatus] | None = None,
    container_names: list[str] | None = None,
    node: str = "node-1",
    labels: dict[str, str] | None = None,
    owner: str = "ReplicaSet/checkout-5f9",
    age_minutes: int = 90,
    conditions: list[V1PodCondition] | None = None,
    deleting: bool = False,
    reason: str = "",
) -> V1Pod:
    """One synthetic pod.

    `owner` is a "Kind/name" string, or an empty string for a pod nobody
    controls, which is the case `restart_pod` has to refuse.
    """
    statuses = statuses if statuses is not None else [container_status()]
    names = container_names or [cs.name for cs in statuses]

    owner_refs = []
    if owner:
        kind, _, owner_name = owner.partition("/")
        owner_refs.append(
            V1OwnerReference(
                api_version="apps/v1",
                kind=kind,
                name=owner_name,
                uid=f"uid-{owner_name}",
                controller=True,
            )
        )

    return V1Pod(
        metadata=V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels or {"app": "checkout"},
            creation_timestamp=NOW - timedelta(minutes=age_minutes),
            owner_references=owner_refs,
            deletion_timestamp=NOW if deleting else None,
        ),
        spec=V1PodSpec(
            node_name=node,
            containers=[V1Container(name=n, image="app:1.4.0") for n in names],
        ),
        status=V1PodStatus(
            phase=phase,
            reason=reason or None,
            container_statuses=statuses,
            conditions=conditions,
        ),
    )


def make_event(
    reason: str,
    message: str,
    pod: str = "",
    kind: str = "Pod",
    event_type: str = "Warning",
    count: int = 1,
    minutes_ago: int = 2,
    namespace: str = "default",
) -> CoreV1Event:
    seen = NOW - timedelta(minutes=minutes_ago)
    return CoreV1Event(
        metadata=V1ObjectMeta(name=f"{pod or kind}.{reason}", namespace=namespace),
        involved_object=V1ObjectReference(kind=kind, name=pod, namespace=namespace),
        reason=reason,
        message=message,
        type=event_type,
        count=count,
        first_timestamp=seen,
        last_timestamp=seen,
    )


def make_template(
    images: dict[str, str],
    env: dict[str, str] | None = None,
    memory_limit: str = "512Mi",
    labels: dict[str, str] | None = None,
    pod_template_hash: str = "",
) -> V1PodTemplateSpec:
    """A pod template with more than images in it.

    Environment and resource limits are here on purpose: a rollback that only
    restores images leaves these behind, and the test that proves it does not
    needs them to exist in the first place.
    """
    all_labels = dict(labels or {"app": "checkout"})
    if pod_template_hash:
        all_labels["pod-template-hash"] = pod_template_hash

    return V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels=all_labels),
        spec=V1PodSpec(
            containers=[
                V1Container(
                    name=name,
                    image=image,
                    env=[V1EnvVar(name=k, value=v) for k, v in (env or {}).items()],
                    resources=V1ResourceRequirements(
                        limits={"memory": memory_limit},
                        requests={"memory": "256Mi"},
                    ),
                )
                for name, image in images.items()
            ]
        ),
    )


def make_deployment(
    name: str,
    namespace: str = "default",
    replicas: int = 3,
    ready: int = 3,
    revision: int = 3,
    uid: str = "",
    template: V1PodTemplateSpec | None = None,
    match_labels: dict[str, str] | None = None,
) -> V1Deployment:
    labels = match_labels or {"app": "checkout"}
    return V1Deployment(
        metadata=V1ObjectMeta(
            name=name,
            namespace=namespace,
            uid=uid or f"uid-{name}",
            annotations={"deployment.kubernetes.io/revision": str(revision)},
            creation_timestamp=NOW - timedelta(days=9),
        ),
        spec=V1DeploymentSpec(
            replicas=replicas,
            selector=V1LabelSelector(match_labels=labels),
            template=template
            or make_template({"app": "registry.example.com/app:1.4.0"}, labels=labels),
        ),
        status=V1DeploymentStatus(
            replicas=replicas,
            ready_replicas=ready,
            available_replicas=ready,
            updated_replicas=replicas,
        ),
    )


def make_replica_set(
    name: str,
    owner: V1Deployment | None = None,
    owner_name: str = "",
    owner_uid: str = "",
    namespace: str = "default",
    revision: int = 1,
    template: V1PodTemplateSpec | None = None,
    replicas: int = 0,
    ready: int = 0,
    match_labels: dict[str, str] | None = None,
) -> V1ReplicaSet:
    if owner is not None:
        owner_name = owner.metadata.name
        owner_uid = owner.metadata.uid

    labels = match_labels or {"app": "checkout"}
    return V1ReplicaSet(
        metadata=V1ObjectMeta(
            name=name,
            namespace=namespace,
            labels=labels,
            uid=f"uid-{name}",
            annotations={"deployment.kubernetes.io/revision": str(revision)},
            creation_timestamp=NOW - timedelta(days=10 - revision),
            owner_references=[
                V1OwnerReference(
                    api_version="apps/v1",
                    kind="Deployment",
                    name=owner_name,
                    uid=owner_uid,
                    controller=True,
                )
            ]
            if owner_name
            else [],
        ),
        spec=V1ReplicaSetSpec(
            replicas=replicas,
            selector=V1LabelSelector(match_labels=labels),
            template=template
            or make_template(
                {"app": f"registry.example.com/app:1.{revision}.0"},
                labels=labels,
                pod_template_hash=f"hash{revision}",
            ),
        ),
        status=V1ReplicaSetStatus(replicas=replicas, ready_replicas=ready),
    )


# --------------------------------------------------------------------------
# The fake API objects
# --------------------------------------------------------------------------


@dataclass
class Recorder:
    """Every fake API call: name, kwargs, and the thread it ran on."""

    calls: list[tuple[str, dict, str]] = field(default_factory=list)

    def record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs, threading.current_thread().name))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def threads(self) -> set[str]:
        return {c[2] for c in self.calls}

    def kwargs_for(self, name: str) -> list[dict]:
        return [c[1] for c in self.calls if c[0] == name]


def _matches(labels: dict[str, str], selector: str) -> bool:
    if not selector:
        return True
    for clause in selector.split(","):
        key, _, value = clause.partition("=")
        if labels.get(key.strip()) != value.strip():
            return False
    return True


class FakeCoreV1:
    """The CoreV1Api surface this server uses, and nothing else."""

    def __init__(self, state: FakeCluster) -> None:
        self.state = state

    def list_namespaced_pod(self, namespace: str, **kw) -> V1PodList:
        self.state.recorder.record("list_namespaced_pod", {"namespace": namespace, **kw})
        return V1PodList(
            items=[p for p in self.state.pods if p.metadata.namespace == namespace]
        )

    def read_namespaced_pod(self, name: str, namespace: str, **kw) -> V1Pod:
        self.state.recorder.record(
            "read_namespaced_pod", {"name": name, "namespace": namespace, **kw}
        )
        for pod in self.state.pods:
            if pod.metadata.name == name and pod.metadata.namespace == namespace:
                return pod
        raise ApiException(status=404, reason="Not Found")

    def delete_namespaced_pod(self, name: str, namespace: str, **kw):
        self.state.recorder.record(
            "delete_namespaced_pod", {"name": name, "namespace": namespace, **kw}
        )
        for pod in list(self.state.pods):
            if pod.metadata.name == name and pod.metadata.namespace == namespace:
                self.state.pods.remove(pod)
                self.state.deleted.append(f"{namespace}/{name}")
                return pod
        raise ApiException(status=404, reason="Not Found")

    def list_namespaced_event(
        self, namespace: str, field_selector: str = "", **kw
    ) -> CoreV1EventList:
        self.state.recorder.record(
            "list_namespaced_event",
            {"namespace": namespace, "field_selector": field_selector, **kw},
        )
        if self.state.events_forbidden:
            raise ApiException(status=403, reason="Forbidden")

        items = [e for e in self.state.events if e.metadata.namespace == namespace]
        if field_selector.startswith("involvedObject.name="):
            wanted = field_selector.split("=", 1)[1]
            items = [e for e in items if e.involved_object.name == wanted]
        return CoreV1EventList(items=items)

    def read_namespaced_pod_log(
        self,
        name: str,
        namespace: str,
        container: str = "",
        tail_lines: int = 0,
        previous: bool = False,
        **kw,
    ) -> str:
        self.state.recorder.record(
            "read_namespaced_pod_log",
            {
                "name": name,
                "namespace": namespace,
                "container": container,
                "tail_lines": tail_lines,
                "previous": previous,
                **kw,
            },
        )
        key = (name, container, previous)
        if key not in self.state.logs:
            key = (name, "", previous)
        if key not in self.state.logs:
            if previous:
                raise ApiException(
                    status=400, reason="Bad Request: previous terminated container"
                )
            if not any(p.metadata.name == name for p in self.state.pods):
                raise ApiException(status=404, reason="Not Found")
            return ""

        lines = self.state.logs[key].splitlines()
        if tail_lines:
            lines = lines[-tail_lines:]
        return "\n".join(lines)


class FakeAppsV1:
    """The AppsV1Api surface this server uses, and nothing else."""

    def __init__(self, state: FakeCluster) -> None:
        self.state = state

    def list_namespaced_deployment(self, namespace: str, **kw) -> V1DeploymentList:
        self.state.recorder.record(
            "list_namespaced_deployment", {"namespace": namespace, **kw}
        )
        return V1DeploymentList(
            items=[
                d for d in self.state.deployments if d.metadata.namespace == namespace
            ]
        )

    def read_namespaced_deployment(self, name: str, namespace: str, **kw) -> V1Deployment:
        self.state.recorder.record(
            "read_namespaced_deployment", {"name": name, "namespace": namespace, **kw}
        )
        for dep in self.state.deployments:
            if dep.metadata.name == name and dep.metadata.namespace == namespace:
                return dep
        raise ApiException(status=404, reason="Not Found")

    def list_namespaced_replica_set(
        self, namespace: str, label_selector: str = "", **kw
    ) -> V1ReplicaSetList:
        self.state.recorder.record(
            "list_namespaced_replica_set",
            {"namespace": namespace, "label_selector": label_selector, **kw},
        )
        # Deliberately selector-only, exactly like the real API. Any ReplicaSet
        # whose labels match comes back, including ones belonging to a different
        # deployment. Filtering by ownership is the caller's job, and the test
        # suite checks that the caller does it.
        return V1ReplicaSetList(
            items=[
                rs
                for rs in self.state.replica_sets
                if rs.metadata.namespace == namespace
                and _matches(rs.metadata.labels or {}, label_selector)
            ]
        )

    def patch_namespaced_deployment_scale(
        self, name: str, namespace: str, body, **kw
    ):
        self.state.recorder.record(
            "patch_namespaced_deployment_scale",
            {"name": name, "namespace": namespace, "body": body, **kw},
        )
        dep = self.read_namespaced_deployment(name, namespace)
        dep.spec.replicas = body["spec"]["replicas"]
        return dep

    def patch_namespaced_deployment(self, name: str, namespace: str, body, **kw):
        self.state.recorder.record(
            "patch_namespaced_deployment",
            {"name": name, "namespace": namespace, "body": body, **kw},
        )
        dep = self.read_namespaced_deployment(name, namespace)
        self.state.patches.append(copy.deepcopy(body))

        # Apply enough of the JSON Patch for a read-back to be meaningful.
        for op in body if isinstance(body, list) else []:
            if op.get("op") == "replace" and op.get("path") == "/spec/template":
                dep.spec.template = _template_from_dict(op["value"])
        return dep


def _template_from_dict(value: dict) -> V1PodTemplateSpec:
    """Rebuild just enough of a pod template for the read-back assertions."""
    spec = value.get("spec") or {}
    containers = [
        V1Container(
            name=c.get("name", ""),
            image=c.get("image", ""),
            env=[
                V1EnvVar(name=e.get("name", ""), value=e.get("value"))
                for e in (c.get("env") or [])
            ],
        )
        for c in (spec.get("containers") or [])
    ]
    return V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels=(value.get("metadata") or {}).get("labels") or {}),
        spec=V1PodSpec(containers=containers),
    )


@dataclass
class FakeCluster:
    """Mutable cluster state the fakes read and write."""

    pods: list = field(default_factory=list)
    deployments: list = field(default_factory=list)
    replica_sets: list = field(default_factory=list)
    events: list = field(default_factory=list)
    logs: dict = field(default_factory=dict)
    deleted: list = field(default_factory=list)
    patches: list = field(default_factory=list)
    events_forbidden: bool = False
    recorder: Recorder = field(default_factory=Recorder)

    def __post_init__(self) -> None:
        self.core = FakeCoreV1(self)
        self.apps = FakeAppsV1(self)

    def json_patch(self) -> list:
        """The most recent JSON Patch body, decoded from whatever was sent."""
        body = self.patches[-1]
        return json.loads(json.dumps(body))


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def populated() -> FakeCluster:
    """A small namespace with one healthy service and one that is broken.

    `checkout` is at revision 3 and crash-looping. `payments` is healthy. A
    second deployment, `checkout-canary`, exists specifically so that it selects
    the same labels as `checkout` and owns its own ReplicaSet: a rollback that
    picked ReplicaSets by label alone would find that one.
    """
    state = FakeCluster()

    checkout = make_deployment(
        "checkout",
        replicas=3,
        ready=1,
        revision=3,
        template=make_template(
            {"app": "registry.example.com/app:1.4.0"},
            env={"FEATURE_NEW_CART": "true", "DB_HOST": "db-primary"},
            memory_limit="256Mi",
        ),
    )
    canary = make_deployment(
        "checkout-canary", replicas=1, ready=1, revision=9, uid="uid-checkout-canary"
    )
    payments = make_deployment(
        "payments", replicas=2, ready=2, revision=1, match_labels={"app": "payments"}
    )
    state.deployments = [checkout, canary, payments]

    state.replica_sets = [
        # First in the list and revision 2, which is exactly the position a
        # label-selector-only rollback would pick as "the previous revision" of
        # `checkout`. It belongs to `checkout-canary`.
        make_replica_set(
            "checkout-canary-2x",
            owner=canary,
            revision=2,
            replicas=1,
            ready=1,
            template=make_template(
                {"app": "registry.example.com/canary:9.9.9"},
                pod_template_hash="hashc2",
            ),
        ),
        make_replica_set(
            "checkout-1a",
            owner=checkout,
            revision=1,
            replicas=0,
            template=make_template(
                {"app": "registry.example.com/app:1.2.0"},
                env={"DB_HOST": "db-primary"},
                memory_limit="512Mi",
                pod_template_hash="hash1",
            ),
        ),
        make_replica_set(
            "checkout-2b",
            owner=checkout,
            revision=2,
            replicas=0,
            template=make_template(
                {"app": "registry.example.com/app:1.3.0"},
                env={"DB_HOST": "db-primary"},
                memory_limit="512Mi",
                pod_template_hash="hash2",
            ),
        ),
        make_replica_set(
            "checkout-3c",
            owner=checkout,
            revision=3,
            replicas=3,
            ready=1,
            template=make_template(
                {"app": "registry.example.com/app:1.4.0"},
                env={"FEATURE_NEW_CART": "true", "DB_HOST": "db-primary"},
                memory_limit="256Mi",
                pod_template_hash="hash3",
            ),
        ),
        # Same labels, different owner. The trap.
        make_replica_set(
            "checkout-canary-9z",
            owner=canary,
            revision=9,
            replicas=1,
            ready=1,
            template=make_template(
                {"app": "registry.example.com/app:2.0.0-rc1"},
                pod_template_hash="hash9",
            ),
        ),
    ]

    state.pods = [
        make_pod(
            "checkout-3c-aaa",
            phase="Running",
            statuses=[
                container_status(
                    ready=False,
                    restarts=7,
                    state=waiting("CrashLoopBackOff", "back-off 5m0s restarting"),
                    last_state=terminated(1, "Error"),
                )
            ],
            owner="ReplicaSet/checkout-3c",
        ),
        make_pod(
            "checkout-3c-bbb",
            phase="Running",
            statuses=[container_status(ready=True, restarts=0)],
            owner="ReplicaSet/checkout-3c",
        ),
        make_pod(
            "payments-1-ccc",
            phase="Running",
            statuses=[container_status(name="payments", ready=True)],
            labels={"app": "payments"},
            owner="ReplicaSet/payments-1",
        ),
        make_pod("orphan-pod", phase="Running", owner=""),
    ]

    state.events = [
        make_event(
            "BackOff",
            "Back-off restarting failed container app in pod checkout-3c-aaa",
            pod="checkout-3c-aaa",
            count=42,
        ),
        make_event(
            "Pulled",
            "Container image already present on machine",
            pod="checkout-3c-bbb",
            event_type="Normal",
            minutes_ago=30,
        ),
    ]

    state.logs = {
        ("checkout-3c-aaa", "", True): "\n".join(
            [f"line {i}" for i in range(1, 40)]
            + ["Traceback (most recent call last):", "KeyError: 'DB_PASSWORD'"]
        ),
        ("checkout-3c-aaa", "", False): "starting up",
        ("checkout-3c-bbb", "", False): "serving on :8080",
    }
    return state


@pytest.fixture
def fake():
    """Install a fake cluster for the duration of one test.

    This is a plain fixture, not an async one, and it does not own an anyio task
    group. The MCP client does, which is why every test below opens it with
    `async with` in the test body rather than taking it from a fixture: a task
    group has to be exited by the task that entered it, and a yield fixture
    tears down somewhere else.
    """
    state = populated()
    cluster.use(
        cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake")
    )
    original = cluster.limits()
    try:
        yield state
    finally:
        cluster.use(None)
        cluster.set_limits(original)


@pytest.fixture
def empty():
    """A fake cluster with nothing in it."""
    state = FakeCluster()
    cluster.use(
        cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake")
    )
    original = cluster.limits()
    try:
        yield state
    finally:
        cluster.use(None)
        cluster.set_limits(original)
