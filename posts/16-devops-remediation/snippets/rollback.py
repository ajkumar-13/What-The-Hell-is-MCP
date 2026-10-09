"""Post 16, sections 6 and 7: what a rollback puts back, and whose history it reads.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/rollback.py

Imports the package under code/15-devops-responder/src with the fake cluster of
tests/conftest.py installed. First applies an image-only patch to the
fixture's current pod template, merging containers by name the way a
strategic merge patch merges that list, and prints what survives. Then calls
rollback_deployment(checkout) over an in-memory client whose user approves,
and prints the JSON Patch the fake API server received, its content type, the
scope lists and the warnings of the result. Then the
ReplicaSets the label selector returns against the ones the deployment owns,
a rollback to a revision that belongs to another deployment, and the same
rollback with the ownership filter replaced by a pass-through. Nothing outside
the process is contacted.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from conftest import populated  # noqa: E402
from kubernetes import client as k8s  # noqa: E402
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster, remediate  # noqa: E402

logging.getLogger().setLevel(logging.CRITICAL)
APPROVE = {"approve": True, "reason": "paged at 02:00"}


def install():
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    return state


async def approve(context, params):
    return ElicitResult(action="accept", content=APPROVE)


async def call(name: str, arguments: dict):
    async with Client(k8s_responder.mcp, elicitation_callback=approve) as c:
        return await c.call_tool(name, arguments)


def merge_containers_by_name(current: dict, patch: dict) -> dict:
    """Merge a patch's containers into a template's by `name`, as a strategic merge does."""
    merged = copy.deepcopy(current)
    by_name = {c["name"]: c for c in merged["spec"]["containers"]}
    for c in patch["spec"]["template"]["spec"]["containers"]:
        by_name[c["name"]].update(c)
    return merged


def summary(template: dict) -> str:
    c = template["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in c.get("env") or []}
    return f"image={c['image']} env={env} memory limit={c['resources']['limits']['memory']}"


def image_only() -> None:
    state = populated()
    checkout = state.deployments[0]
    target_rs = next(rs for rs in state.replica_sets if rs.metadata.name == "checkout-2b")

    # An image-only "rollback". This is not a rollback.
    patch_body = {"spec": {"template": {"spec": {"containers": [
        {"name": c.name, "image": c.image} for c in target_rs.spec.template.spec.containers
    ]}}}}

    serialize = k8s.ApiClient().sanitize_for_serialization
    current = serialize(checkout.spec.template)
    print("== an image-only patch, merged into checkout's template by container name ==")
    print(f"revision 3, current:   {summary(current)}")
    print(f"revision 2, target:    {summary(serialize(target_rs.spec.template))}")
    print(f"after the merge:       {summary(merge_containers_by_name(current, patch_body))}")


async def the_rollback() -> None:
    state = install()
    data = (await call("rollback_deployment", {"deployment_name": "checkout"})).structured_content
    sent = state.recorder.kwargs_for("patch_namespaced_deployment")[-1]
    print("\n== rollback_deployment(checkout), approved: the patch the API server received ==")
    print(json.dumps(state.json_patch(), indent=2))
    print(f"_content_type: {sent['_content_type']}")
    print(f"\nfrom_revision={data['from_revision']} to_revision={data['to_revision']} "
          f"restored_images={data['restored_images']}")
    print("restored:")
    for line in data["restored"]:
        print(f"  - {line}")
    print("not_restored:")
    for line in data["not_restored"]:
        print(f"  - {line}")
    print(f"warnings: {data['warnings']}")
    print(f"outcome: {data['outcome']!r}")
    cluster.use(None)


async def ownership() -> None:
    state = install()
    checkout = state.deployments[0]
    listed = state.apps.list_namespaced_replica_set("default", label_selector="app=checkout").items
    owned = remediate.owned_replica_sets(listed, checkout)
    show = lambda items: [f"{rs.metadata.name} (rev {remediate.revision_of(rs)})" for rs in items]  # noqa: E731
    print("\n== ReplicaSets for checkout ==")
    print(f"label selector app=checkout returns: {show(listed)}")
    print(f"owned by checkout (controller uid):  {show(owned)}")

    history = (await call("get_rollout_history", {"deployment_name": "checkout"})).structured_content
    print(f"get_rollout_history revisions: {[r['revision'] for r in history['revisions']]}")

    data = (await call("rollback_deployment", {"deployment_name": "checkout", "revision": 9})).structured_content
    print(f"rollback to revision 9: applied={json.dumps(data['applied'])} outcome={data['outcome']!r}")
    cluster.use(None)

    state = install()
    original = remediate.owned_replica_sets
    remediate.owned_replica_sets = lambda replica_sets, deployment: list(replica_sets)
    try:
        data = (await call("rollback_deployment", {"deployment_name": "checkout"})).structured_content
    finally:
        remediate.owned_replica_sets = original
    print("\n== the same rollback with the ownership filter replaced by a pass-through ==")
    print(f"applied={json.dumps(data['applied'])} from_revision={data['from_revision']} "
          f"to_revision={data['to_revision']} restored_images={data['restored_images']}")
    print(f"patched template image: {state.json_patch()[0]['value']['spec']['containers'][0]['image']}")
    cluster.use(None)


async def main() -> None:
    image_only()
    await the_rollback()
    await ownership()


if __name__ == "__main__":
    asyncio.run(main())
