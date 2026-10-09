"""Post 15, sections 3 and 10: what the model reads when a tool cannot answer.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/refusals.py

Prints the class chain of the project's ClusterError, then
calls the real tools over an in-memory client in four situations: a pod that
does not exist, a cluster that forbids reading events, no credentials at all
(KUBECONFIG pointed at a file that does not exist, so a real kubeconfig is
never read and nothing is contacted), and, on a throwaway server, the same
refusal raised as a plain Exception subclass instead. For each it prints what
came back and the log records the server wrote at INFO or above.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from conftest import populated  # noqa: E402
from kubernetes.config import kube_config  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402


class Records(logging.Handler):
    """Collects the server's log records instead of printing them to stderr."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        traceback = " (with a traceback)" if record.exc_info else ""
        first = record.getMessage().splitlines()[0]
        first = first if len(first) <= 70 else first[:70] + " ..."
        self.lines.append(f"{record.levelname} {record.name}: {first}{traceback}")


records = Records()
root_logger = logging.getLogger()
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)
root_logger.addHandler(records)
root_logger.setLevel(logging.INFO)


def report(label: str, result) -> None:
    print(f"== {label} ==")
    print(f"isError: {str(bool(result.is_error)).lower()}")
    if result.is_error:
        print("text:   " + result.content[0].text)
    else:
        s = result.structured_content
        print(f"events: {s.get('events')!r}, containers: {len(s.get('containers', []))}")
    wanted = [line for line in records.lines if "mcp.server.lowlevel" not in line]
    print("logged: " + ("\n        ".join(wanted) or "(nothing at INFO or above)"))
    records.lines.clear()
    print()


class OldClusterError(Exception):
    """The project's ClusterError as it was before it subclassed ToolError."""


old = MCPServer("old-cluster-error")


@old.tool()
async def describe_pod(pod_name: str, namespace: str = "default") -> str:
    raise OldClusterError(f"No pod named {pod_name!r} in namespace {namespace!r}.")


async def main() -> None:
    print("ClusterError's classes: "
          + " -> ".join(f"{c.__module__}.{c.__qualname__}" for c in cluster.ClusterError.__mro__[:3]))
    print()

    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    async with Client(k8s_responder.mcp) as client:
        records.lines.clear()
        report("describe_pod on a pod that does not exist",
               await client.call_tool("describe_pod", {"pod_name": "does-not-exist"}))
        state.events_forbidden = True
        report("describe_pod while the cluster forbids reading events (403)",
               await client.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"}))
    cluster.use(None)

    with tempfile.TemporaryDirectory() as home:
        # The client reads KUBECONFIG once, at import, into this module global,
        # so point the global itself at a file that does not exist. A real
        # kubeconfig on this machine is then never read and no cluster is called.
        missing = str(Path(home) / "no-such-kubeconfig")
        os.environ["KUBECONFIG"] = missing
        kube_config.KUBE_CONFIG_DEFAULT_LOCATION = missing
        os.environ.pop("KUBERNETES_SERVICE_HOST", None)
        async with Client(k8s_responder.mcp) as client:
            records.lines.clear()
            report("list_pods with no credentials anywhere",
                   await client.call_tool("list_pods", {}))
    cluster.use(None)

    async with Client(old) as client:
        records.lines.clear()
        report("the same refusal raised as a plain Exception subclass",
               await client.call_tool("describe_pod", {"pod_name": "does-not-exist"}))


if __name__ == "__main__":
    asyncio.run(main())
