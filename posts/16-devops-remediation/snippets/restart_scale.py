"""Post 16, sections 4 and 5: restart and scale, and reading back rather than assuming.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/restart_scale.py

Imports the package under code/15-devops-responder/src with the fake cluster of
tests/conftest.py installed, and calls restart_pod and scale_deployment over
in-memory clients whose user approves. The fake removes a deleted pod at once,
so to show the other two read-back states it is altered for one call each: a
delete that only marks the pod for deletion, as a real API server does during
the grace period, and a read-back the cluster refuses. For scaling, one call
runs against a fake that keeps the old count, as an autoscaler that resets it
would. Nothing outside the process is contacted.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from conftest import populated  # noqa: E402
from kubernetes.client.rest import ApiException  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402

logging.getLogger().setLevel(logging.CRITICAL)


def install():
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    return state


async def approve(context, params):
    return ElicitResult(action="accept", content={"approve": True})


async def call(name: str, arguments: dict):
    async with Client(k8s_responder.mcp, elicitation_callback=approve) as client:
        return await client.call_tool(name, arguments)


def state_sentence(outcome: str) -> str:
    """The sentence of a restart outcome that reports the read-back: 'It is ...'."""
    return next(s for s in outcome.split(". ") if s.startswith("It is ")) + "."


def writes(state) -> list[str]:
    return [n for n in state.recorder.names() if n.startswith(("patch_", "delete_"))]


async def restarts() -> None:
    print("== restart_pod, approved ==")

    state = install()
    data = (await call("restart_pod", {"pod_name": "orphan-pod"})).structured_content
    print("orphan-pod, which has no controlling owner:")
    print(json.dumps({k: data[k] for k in ("approved", "applied", "outcome", "warnings")}, indent=2))
    print(f"writes: {writes(state)}")
    cluster.use(None)

    state = install()
    data = (await call("restart_pod", {"pod_name": "ghost"})).structured_content
    print(f"\nghost, which does not exist: applied={json.dumps(data['applied'])} outcome={data['outcome']!r}")
    cluster.use(None)

    print("\ncheckout-3c-aaa, owned by a ReplicaSet, under three read-backs:")

    state = install()
    data = (await call("restart_pod", {"pod_name": "checkout-3c-aaa"})).structured_content
    print(f"  pod removed at once:     controller={data['controller']!r} writes={writes(state)}")
    print(f"                           outcome={data['outcome']!r}")
    cluster.use(None)

    state = install()

    def mark_for_deletion(name, namespace, **kw):
        state.recorder.record("delete_namespaced_pod", {"name": name, "namespace": namespace})
        pod = next(p for p in state.pods if p.metadata.name == name)
        pod.metadata.deletion_timestamp = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        return pod

    state.core.delete_namespaced_pod = mark_for_deletion
    data = (await call("restart_pod", {"pod_name": "checkout-3c-aaa"})).structured_content
    print(f"  pod marked for deletion: {state_sentence(data['outcome'])}")
    cluster.use(None)

    state = install()
    real_read = state.core.read_namespaced_pod
    reads = {"n": 0}

    def forbid_second_read(name, namespace, **kw):
        reads["n"] += 1
        if reads["n"] > 1:
            raise ApiException(status=403, reason="Forbidden")
        return real_read(name, namespace, **kw)

    state.core.read_namespaced_pod = forbid_second_read
    data = (await call("restart_pod", {"pod_name": "checkout-3c-aaa"})).structured_content
    print(f"  read-back forbidden:     {state_sentence(data['outcome'])} warnings={data['warnings']}")
    cluster.use(None)


async def scaling() -> None:
    print("\n== scale_deployment(checkout, ...), approved; the fixture's checkout has 3 replicas ==")

    state = install()
    data = (await call("scale_deployment", {"deployment_name": "checkout", "replicas": 6})).structured_content
    print(f"to 6:  previous={data['previous_replicas']} requested={data['requested_replicas']} "
          f"observed={data['observed_replicas']} warnings={data['warnings']}")
    cluster.use(None)

    state = install()

    def keep_old_count(name, namespace, body, **kw):
        state.recorder.record("patch_namespaced_deployment_scale", {"name": name, "body": body})
        return state.apps.read_namespaced_deployment(name, namespace)

    state.apps.patch_namespaced_deployment_scale = keep_old_count
    data = (await call("scale_deployment", {"deployment_name": "checkout", "replicas": 6})).structured_content
    print("to 6, against a cluster that keeps the old count:")
    print(f"       previous={data['previous_replicas']} requested={data['requested_replicas']} "
          f"observed={data['observed_replicas']}")
    print(f"       warnings={data['warnings']}")
    cluster.use(None)

    state = install()
    data = (await call("scale_deployment", {"deployment_name": "checkout", "replicas": 3})).structured_content
    print(f"to 3:  applied={json.dumps(data['applied'])} outcome={data['outcome']!r} writes={writes(state)}")
    cluster.use(None)

    state = install()
    data = (await call("scale_deployment", {"deployment_name": "checkout", "replicas": 0})).structured_content
    print(f"to 0:  applied={json.dumps(data['applied'])} blast_radius={data['preview']['blast_radius']!r}")
    cluster.use(None)


async def main() -> None:
    await restarts()
    await scaling()


if __name__ == "__main__":
    asyncio.run(main())
