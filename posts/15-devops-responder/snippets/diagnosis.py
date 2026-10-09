"""Post 15, sections 5 and 7: correlating three sources into one verdict.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/diagnosis.py

Calls the real diagnose_pod and find_crash_loops tools
over an in-memory client against the fixture of tests/conftest.py, then hands
synthetic pods built with that file's helpers straight to the pure function
`analyze()`: a loop that is also out of memory, exit code 0, a loop caught
between back-offs, two Pending pods, a pod stuck in its init container (read
with and without `initContainerStatuses`), and one that matches nothing.
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

from conftest import (  # noqa: E402
    container_status,
    make_pod,
    populated,
    running,
    terminated,
    waiting,
)
from mcp import Client  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402
from k8s_responder.diagnose import analyze  # noqa: E402
from k8s_responder.inspect import ClusterEvent  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)


def event(reason: str, message: str) -> ClusterEvent:
    return ClusterEvent(type="Warning", reason=reason, object="Pod/x", message=message,
                        count=1, first_seen="2026-07-26T11:00:00+00:00",
                        last_seen="2026-07-26T11:58:00+00:00")


def show(label: str, pod, events=None):
    d = analyze(pod, events or [])
    print(f"   {label:<38}{d.verdict} ({d.confidence})")
    return d


def layout(result: dict) -> str:
    """Two-space JSON, with each finding on one line so the result fits on a screen."""
    lines = ["{"]
    items = list(result.items())
    for n, (key, value) in enumerate(items):
        comma = "," if n < len(items) - 1 else ""
        if isinstance(value, list) and value:
            lines.append(f"  {json.dumps(key)}: [")
            for m, entry in enumerate(value):
                inner = json.dumps(entry).replace('{"', '{ "').replace('"}', '" }')
                lines.append(f"    {inner}{',' if m < len(value) - 1 else ''}")
            lines.append(f"  ]{comma}")
        else:
            lines.append(f"  {json.dumps(key)}: {json.dumps(value)}{comma}")
    lines.append("}")
    return "\n".join(lines)


async def main() -> None:
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    async with Client(k8s_responder.mcp) as client:
        r = await client.call_tool("diagnose_pod", {"pod_name": "checkout-3c-aaa"})
        print("1. diagnose_pod(pod_name='checkout-3c-aaa') against the fixture, structuredContent:")
        print(layout(r.structured_content))
        assert json.loads(layout(r.structured_content)) == r.structured_content
        print()

        before = len(state.recorder.calls)
        r = await client.call_tool("find_crash_loops", {})
        names = state.recorder.names()[before:]
        d = r.structured_content
        print("2. find_crash_loops() against the fixture")
        print(f"   scanned={d['scanned']} unhealthy={d['unhealthy']} verdicts={d['verdicts']} "
              f"first={d['diagnoses'][0]['pod']}")
        print(f"   API calls: {len(names)} ({', '.join(names)})")
    cluster.use(None)
    print()

    print("3. synthetic pods handed straight to analyze()")
    oom = make_pod("api-1", statuses=[container_status(
        ready=False, restarts=6, state=waiting("CrashLoopBackOff", "back-off 2m40s"),
        last_state=terminated(137, "OOMKilled"))])
    d = show("CrashLoopBackOff and OOMKilled", oom)
    print("     signals in its findings: " + ", ".join(f.signal for f in d.findings))

    zero = make_pod("job-1", statuses=[container_status(
        ready=False, restarts=5, state=waiting("CrashLoopBackOff"),
        last_state=terminated(0, "Completed"))])
    d = show("previous exit code 0", zero)
    print(f"     likely_cause: {d.likely_cause}")

    between = make_pod("web-2", statuses=[container_status(
        ready=False, restarts=5, state=running(), last_state=terminated(2))])
    show("running again between back-offs", between)

    show("Pending, no events", make_pod("web-3", phase="Pending", statuses=[]))
    show("Pending plus FailedScheduling", make_pod("web-4", phase="Pending", statuses=[]),
         [event("FailedScheduling", "0/3 nodes are available: 3 Insufficient memory.")])

    stuck = make_pod("web-5", phase="Pending", container_names=["app"], statuses=[
        container_status(ready=False, state=waiting("PodInitializing"))])
    stuck.status.init_container_statuses = [container_status(
        name="wait-for-db", ready=False, restarts=8, state=waiting("CrashLoopBackOff"),
        last_state=terminated(1))]
    blind = copy.deepcopy(stuck)
    blind.status.init_container_statuses = None
    show("init container looping, read fully", stuck)
    show("the same pod, init statuses unread", blind)

    odd = make_pod("web-6", statuses=[container_status(ready=False, state=running())])
    d = show("not ready, no known signature", odd)
    print(f"     likely_cause: {d.likely_cause}")


if __name__ == "__main__":
    asyncio.run(main())
