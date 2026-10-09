"""Post 16, sections 2 and 12: what the writing tools publish, and the approval on the wire.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/approval_wire.py

First publishes, on a throwaway server, a restart tool that takes its approval
as an ordinary argument, and prints what tools/list shows for it. Then imports
the package under code/15-devops-responder/src with the fake cluster of
tests/conftest.py installed, and prints the input properties and annotations
tools/list publishes for the three writing tools. Then it calls
scale_deployment(checkout, 5) through a client whose user approves, over the
SDK's in-memory stream transport wrapped in a tap, and prints the JSON-RPC
messages of the two tools/call legs as they crossed the streams. Three things
are shortened for printing, and each is marked where it was cut: the sealed
requestState (different on every run), the question text (printed in full by
preview_answers.py), and the repeated _meta of the second request. Last, what
a client with no elicitation callback gets. Nothing outside the process is
contacted.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import logging  # noqa: E402
from importlib.metadata import version  # noqa: E402

from conftest import populated  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client._memory import InMemoryTransport  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402

logging.getLogger().setLevel(logging.WARNING)

WRITE_TOOLS = ("restart_pod", "scale_deployment", "rollback_deployment")
ARGS = {"deployment_name": "checkout", "replicas": 5}

naive = MCPServer("approval-as-an-argument")


@naive.tool()
async def restart_pod(pod_name: str, approved: bool) -> str:
    if not approved:
        return "Not approved."
    return f"Deleted pod {pod_name}."


def install():
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    return state


def shortened(message: dict, leg: int) -> dict:
    """Cut what is long or different on every run, and say where it was cut."""
    text = json.loads(json.dumps(message))
    for holder in (text.get("params"), text.get("result")):
        if holder and isinstance(holder.get("requestState"), str):
            holder["requestState"] = holder["requestState"][:3] + "<sealed, shortened here>"
    for entry in (text.get("result") or {}).get("inputRequests", {}).values():
        entry["params"]["message"] = "<the question, printed by preview_answers.py>"
    if leg == 2 and "params" in text:
        text["params"]["_meta"] = "<the same three keys as leg 1>"
    return text


class Tap:
    """The SDK's in-memory transport, with every message on its two streams recorded."""

    def __init__(self, server) -> None:
        self.inner = InMemoryTransport(server)
        self.messages: list[dict] = []

    def record(self, item) -> None:
        message = getattr(item, "message", None)
        if message is not None:
            self.messages.append(message.model_dump(mode="json", by_alias=True, exclude_none=True))

    async def __aenter__(self):
        read, write = await self.inner.__aenter__()
        tap = self

        class Read:
            def __getattr__(self, name):
                return getattr(read, name)

            async def __aenter__(self):
                await read.__aenter__()
                return self

            async def __aexit__(self, *exc):
                return await read.__aexit__(*exc)

            def __aiter__(self):
                return self

            async def __anext__(self):
                item = await read.__anext__()
                tap.record(item)
                return item

        class Write:
            def __getattr__(self, name):
                return getattr(write, name)

            async def __aenter__(self):
                await write.__aenter__()
                return self

            async def __aexit__(self, *exc):
                return await write.__aexit__(*exc)

            async def send(self, item):
                tap.record(item)
                await write.send(item)

        return Read(), Write()

    async def __aexit__(self, *exc):
        return await self.inner.__aexit__(*exc)


STATE = None
CALLS_WHEN_ASKED: list[list[str]] = []


async def approve(context, params):
    """The user: records what the cluster had seen when the question arrived, then says yes."""
    CALLS_WHEN_ASKED.append(list(STATE.recorder.names()) if STATE else [])
    return ElicitResult(action="accept", content={"approve": True, "reason": "paged at 02:00"})


async def published() -> None:
    async with Client(naive) as client:
        tool = (await client.list_tools()).tools[0]
    print("== a restart tool that takes its approval as an argument ==")
    print(f"{tool.name:<20} properties={list(tool.input_schema['properties'])} "
          f"required={tool.input_schema.get('required', [])}")

    async with Client(k8s_responder.mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    print("\n== what tools/list publishes for the project's writing tools ==")
    for name in WRITE_TOOLS:
        t = tools[name]
        a = t.annotations
        print(f"{name:<20} properties={list(t.input_schema['properties'])} "
              f"required={t.input_schema.get('required', [])}")
        print(f"{'':<20} readOnly={a.read_only_hint} destructive={a.destructive_hint} "
              f"idempotent={a.idempotent_hint} openWorld={a.open_world_hint}")


async def on_the_wire() -> None:
    global STATE
    state = STATE = install()
    tap = Tap(k8s_responder.mcp)
    async with Client(tap, elicitation_callback=approve) as client:
        result = await client.call_tool("scale_deployment", ARGS)

    call_ids = [m["id"] for m in tap.messages if m.get("method") == "tools/call"]
    for n, request_id in enumerate(call_ids, start=1):
        request = next(m for m in tap.messages if m.get("id") == request_id and "method" in m)
        reply = next(m for m in tap.messages if m.get("id") == request_id and "method" not in m)
        print(f"\n== leg {n}, client to server ==")
        print(json.dumps(shortened(request, n), indent=2))
        print(f"\n== leg {n}, server to client ==")
        body = shortened(reply, n)
        if n == 2:  # the complete result is long; keep the fields the post reads
            r = body["result"]
            s = r["structuredContent"]
            body["result"] = {
                "resultType": r["resultType"],
                "isError": r["isError"],
                "structuredContent": {k: s[k] for k in (
                    "approved", "applied", "previous_replicas",
                    "requested_replicas", "observed_replicas")},
            }
        print(json.dumps(body, indent=2))
    writes = [n for n in state.recorder.names() if n.startswith(("patch_", "delete_"))]
    print(f"\nkubernetes calls made before the human was asked: {CALLS_WHEN_ASKED[0]}")
    print(f"kubernetes writes made by the whole call: {writes}")
    print(f"preview.change in the result: {result.structured_content['preview']['change']!r}")
    cluster.use(None)


async def no_callback() -> None:
    state = install()
    print("\n== a client with no elicitation_callback ==")
    async with Client(k8s_responder.mcp) as client:
        try:
            await client.call_tool("scale_deployment", ARGS)
        except MCPError as exc:
            print(f"MCPError {exc.code}: {exc}")
            print(f"data: {json.dumps(exc.error.data)}")
    print(f"kubernetes calls: {state.recorder.names()}")
    cluster.use(None)


async def main() -> None:
    print(f"mcp {version('mcp')}, kubernetes {version('kubernetes')}, Python {sys.version.split()[0]}\n")
    await published()
    await on_the_wire()
    await no_callback()


if __name__ == "__main__":
    asyncio.run(main())
