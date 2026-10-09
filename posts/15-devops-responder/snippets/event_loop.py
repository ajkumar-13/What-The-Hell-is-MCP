"""Post 15, section 3: a blocking client on the event loop, and the one function that avoids it.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/event_loop.py

Imports the project's package and test fixture, then:

1. Registers a deliberately wrong `list_pods` on a throwaway server, one that
   calls a slow synchronous fake API straight from the `async def` body, and
   the project's real `list_pods`, which goes through `cluster.call()`, against
   the same slow fake. Each server gets two concurrent calls over an in-memory
   client while a heartbeat task tries to run every 20 ms; the script prints the
   wall time and the longest the heartbeat had to wait. The in-memory client
   and the server share one event loop, as a stdio server and its transport do.
2. Calls a function with a keyword argument through `asyncio.to_thread` and
   through `loop.run_in_executor`.
3. Runs three tools against the fixture of tests/conftest.py and prints the
   names of the threads the fake API saw.

Timings vary by machine; the shape does not.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402

from kubernetes.client import V1PodList  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402
from k8s_responder.inspect import PodList  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

API_SECONDS = 0.5  # how long the fake API server takes to answer


class SlowCoreV1:
    """A synchronous API whose every call blocks, like the real client's HTTP request."""

    def list_namespaced_pod(self, namespace: str, **kw) -> V1PodList:
        time.sleep(API_SECONDS)
        return V1PodList(items=[])


core_v1 = SlowCoreV1()
mcp = MCPServer("blocking-demo")


@mcp.tool()
async def list_pods(namespace: str = "default") -> PodList:
    result = core_v1.list_namespaced_pod(namespace=namespace)   # blocks the event loop
    return PodList(namespace=namespace, total=len(result.items), running=0, unhealthy=0, pods=[])


async def two_calls(server) -> tuple[float, float]:
    """Two concurrent list_pods calls; returns (wall seconds, longest heartbeat gap)."""
    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    async with Client(server) as client:
        beat = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.1)
        start = time.perf_counter()
        await asyncio.gather(client.call_tool("list_pods", {}), client.call_tool("list_pods", {}))
        wall = time.perf_counter() - start
        stop.set()
        await beat
    return wall, max(gaps)


def add(a: int, b: int = 0) -> int:
    return a + b


async def main() -> None:
    print(f"1. two concurrent list_pods calls, fake API takes {API_SECONDS:.1f} s per call")
    wall, gap = await two_calls(mcp)
    print(f"   called on the event loop:       {wall:.1f} s in total, "
          f"heartbeat waited up to {gap * 1000:.0f} ms")
    cluster.use(cluster.Connection(core_v1=SlowCoreV1(), apps_v1=None, describe="slow fake"))
    wall, gap = await two_calls(k8s_responder.mcp)
    print(f"   through cluster.call (a thread): {wall:.1f} s in total, "
          f"heartbeat waited up to {gap * 1000:.0f} ms")
    cluster.use(None)
    print()

    print("2. a keyword argument through each helper")
    print("   asyncio.to_thread(add, 1, b=2) ->", await asyncio.to_thread(add, 1, b=2))
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, add, 1, b=2)
    except TypeError as exc:
        print("   loop.run_in_executor(None, add, 1, b=2) -> TypeError:", exc)
    print("   inside a coroutine, asyncio.get_event_loop() is asyncio.get_running_loop():",
          asyncio.get_event_loop() is loop)
    print()

    from conftest import populated

    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    async with Client(k8s_responder.mcp) as client:
        await client.call_tool("list_pods", {})
        await client.call_tool("describe_pod", {"pod_name": "checkout-3c-aaa"})
        await client.call_tool("list_deployments", {})
    cluster.use(None)
    print("3. the fixture's recorder after list_pods, describe_pod and list_deployments")
    print(f"   {len(state.recorder.calls)} API calls: {', '.join(state.recorder.names())}")
    print(f"   threads they ran on: {sorted(state.recorder.threads())}")
    print(f"   'MainThread' among them: {'MainThread' in state.recorder.threads()}")


if __name__ == "__main__":
    asyncio.run(main())
