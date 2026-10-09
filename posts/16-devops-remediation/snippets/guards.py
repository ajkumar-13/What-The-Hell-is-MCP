"""Post 16, section 8: blast-radius limits, checked before anyone is asked.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/guards.py

Imports the package under code/15-devops-responder/src with the fake cluster of
tests/conftest.py installed, and calls the writing tools over in-memory
clients whose user always approves. Prints the class chain of PolicyError; for
each refused call, what the model reads, how many questions the user was shown,
what reached the cluster, and the line the server logged; that reads in a
protected namespace still work; what the two environment variables do to the
limits; and, on a throwaway server, the same replica check placed in the tool
body instead of the resolver. Nothing outside the process is contacted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from pathlib import Path
from typing import Annotated

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from conftest import populated  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.mcpserver import Elicit, MCPServer, Resolve  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster, remediate  # noqa: E402


class Records(logging.Handler):
    """Collects the SDK's log records about failed tools instead of printing them."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        first = record.getMessage().splitlines()[0]
        head, sep, _ = first.partition(" failed: ")
        self.lines.append(f"{record.levelname} {head}{sep}<the text above>" if sep else f"{record.levelname} {first}")


records = Records()
root_logger = logging.getLogger()
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)
sdk_logger = logging.getLogger("mcp.server.mcpserver")
sdk_logger.addHandler(records)
sdk_logger.setLevel(logging.INFO)
sdk_logger.propagate = False  # collected above, not printed a second time on stderr


def install():
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    return state


def always_yes(seen: list):
    async def callback(context, params):
        seen.append(params.message)
        return ElicitResult(action="accept", content={"approve": True})

    return callback


TEXTS: dict[tuple, str] = {}


async def refused(server, name: str, arguments: dict, state=None, same_as=None) -> None:
    seen: list[str] = []
    records.lines.clear()
    async with Client(server, elicitation_callback=always_yes(seen)) as client:
        result = await client.call_tool(name, arguments)
    text = result.content[0].text
    TEXTS[(name, str(arguments))] = text
    print(f"-- {name} {arguments}")
    print(f"   isError: {str(bool(result.is_error)).lower()}")
    if same_as is None:
        print(f"   text:    {text}")
    else:
        print(f"   text identical to the project's refusal above: {text == TEXTS[same_as]}")
    print(f"   questions shown: {len(seen)}")
    if state is not None:
        print(f"   kubernetes calls: {state.recorder.names()}")
    for line in records.lines:
        print(f"   logged:  {line}")


late = MCPServer("guard-in-the-body")


def confirm(deployment_name: str, replicas: int) -> Elicit[remediate.Approval]:
    return Elicit(f"Scale {deployment_name} to {replicas}?", remediate.Approval)


@late.tool()
async def scale_deployment(
    deployment_name: str,
    replicas: int,
    approval: Annotated[remediate.Approval, Resolve(confirm)],
) -> str:
    remediate.guard_replicas(replicas)  # after the human has already said yes
    return f"scaled {deployment_name} to {replicas}"


async def main() -> None:
    print("PolicyError's classes: " + " -> ".join(
        f"{c.__module__}.{c.__qualname__}" for c in cluster.PolicyError.__mro__[:3]))
    print(f"limits in force: max_replicas={cluster.limits().max_replicas} "
          f"protected={sorted(cluster.limits().protected_namespaces)}")

    print("\n== refused in the resolver, before any question ==")
    for name, arguments in (
        ("scale_deployment", {"deployment_name": "checkout", "replicas": 500}),
        ("scale_deployment", {"deployment_name": "checkout", "replicas": -1}),
        ("restart_pod", {"pod_name": "coredns-abc", "namespace": "kube-system"}),
        ("rollback_deployment", {"deployment_name": "coredns", "namespace": "kube-system"}),
    ):
        state = install()
        await refused(k8s_responder.mcp, name, arguments, state)
        cluster.use(None)

    state = install()
    async with Client(k8s_responder.mcp) as client:
        result = await client.call_tool("list_pods", {"namespace": "kube-system"})
    print(f"\nlist_pods in kube-system: isError={str(bool(result.is_error)).lower()} "
          f"total={result.structured_content['total']}")
    cluster.use(None)

    print("\n== the limits come from the environment, read at import ==")
    saved = {k: os.environ.get(k) for k in ("K8S_RESPONDER_MAX_REPLICAS", "K8S_RESPONDER_PROTECTED_NAMESPACES")}
    os.environ["K8S_RESPONDER_MAX_REPLICAS"] = "50"
    os.environ["K8S_RESPONDER_PROTECTED_NAMESPACES"] = "payments"
    env_limits = cluster._limits_from_env()
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    print("K8S_RESPONDER_MAX_REPLICAS=50 K8S_RESPONDER_PROTECTED_NAMESPACES=payments gives")
    print(f"   max_replicas={env_limits.max_replicas} protected={sorted(env_limits.protected_namespaces)}")

    original = cluster.limits()
    cluster.set_limits(dataclasses.replace(original, max_replicas=2))
    state = install()
    print("with max_replicas set to 2:")
    await refused(k8s_responder.mcp, "scale_deployment", {"deployment_name": "checkout", "replicas": 3}, state)
    cluster.use(None)
    cluster.set_limits(original)

    print("\n== on a throwaway server: the same check in the tool body ==")
    big = {"deployment_name": "checkout", "replicas": 500}
    await refused(late, "scale_deployment", big, same_as=("scale_deployment", str(big)))


if __name__ == "__main__":
    asyncio.run(main())
