"""Post 16, section 3: the preview as a pure function, and the four answers a user can give.

Run from the series root:
    uv run python posts/16-devops-remediation/snippets/preview_answers.py

Imports the package under code/15-devops-responder/src with the fake cluster of
tests/conftest.py installed, and calls scale_deployment over in-memory clients
whose user always answers the same way. Prints the question the server asks;
the four answers (decline, cancel, accept with approve false, accept with
approve true) with what each call did to the cluster; the questions of two
identical calls with the cluster changed in between; and the preview a declined
call still returns. Then, on a throwaway server, counts how often a resolver
runs for one approved call, and shows a resolver whose question carries a live
reading that moves between rounds.
"""

from __future__ import annotations

import asyncio
import json
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
from pydantic import BaseModel  # noqa: E402

import k8s_responder  # noqa: E402
from k8s_responder import cluster  # noqa: E402

logging.getLogger().setLevel(logging.CRITICAL)


def install():
    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    return state


def user(action: str, content: dict | None = None, seen: list | None = None):
    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return callback


async def scale(callback, replicas: int):
    async with Client(k8s_responder.mcp, elicitation_callback=callback) as client:
        return await client.call_tool(
            "scale_deployment", {"deployment_name": "checkout", "replicas": replicas}
        )


async def the_question() -> None:
    install()
    seen: list[str] = []
    await scale(user("decline", seen=seen), 5)
    print("== the question for scale_deployment(checkout, 5) ==")
    print(seen[0])
    cluster.use(None)


async def four_answers() -> None:
    print("\n== four answers to scale_deployment(checkout, 6) ==")
    for action, content in (
        ("decline", None),
        ("cancel", None),
        ("accept", {"approve": False}),
        ("accept", {"approve": True}),
    ):
        state = install()
        seen: list[str] = []
        result = await scale(user(action, content, seen), 6)
        data = result.structured_content
        label = f"{action} {json.dumps(content)}" if content else action
        writes = [n for n in state.recorder.names() if n.startswith(("patch_", "delete_"))]
        print(f"{label:<26} asked={len(seen)} isError={json.dumps(result.is_error)} "
              f"approved={json.dumps(data['approved'])} applied={json.dumps(data['applied'])} writes={writes}")
        print(f"{'':<26} outcome={data['outcome']!r}")
        cluster.use(None)


async def same_arguments() -> None:
    state = install()
    first: list[str] = []
    second: list[str] = []
    await scale(user("decline", seen=first), 5)
    state.deployments[0].spec.replicas = 11
    await scale(user("decline", seen=second), 5)
    print("\n== the same arguments, with spec.replicas changed from 3 to 11 in between ==")
    print(f"questions byte-identical: {first == second}")
    cluster.use(None)


async def declined_preview() -> None:
    install()
    result = await scale(user("decline"), 6)
    print("\n== the preview a declined call returns ==")
    print(json.dumps(result.structured_content["preview"], indent=2))
    cluster.use(None)


class Approval(BaseModel):
    approve: bool


RUNS = {"stable": 0, "live": 0}
LIVE_READING = {"replicas": 3}


def stable_question(replicas: int) -> Elicit[Approval]:
    RUNS["stable"] += 1
    return Elicit(f"Scale checkout to {replicas}?", Approval)


def live_question(replicas: int) -> Elicit[Approval]:
    RUNS["live"] += 1
    LIVE_READING["replicas"] += 1  # a live reading that moves between rounds
    return Elicit(f"Scale checkout from {LIVE_READING['replicas']} to {replicas}?", Approval)


throwaway = MCPServer("preview-experiment")


@throwaway.tool()
async def stable(replicas: int, approval: Annotated[Approval, Resolve(stable_question)]) -> str:
    return f"scaled to {replicas}"


@throwaway.tool()
async def live(replicas: int, approval: Annotated[Approval, Resolve(live_question)]) -> str:
    return f"scaled to {replicas}"


async def resolver_rounds() -> None:
    print("\n== on a throwaway server: how often a resolver runs ==")
    for name in ("stable", "live"):
        seen: list[str] = []
        async with Client(throwaway, elicitation_callback=user("accept", {"approve": True}, seen)) as client:
            try:
                result = await client.call_tool(name, {"replicas": 5})
                ended = f"{result.content[0].text!r}"
            except Exception as exc:  # noqa: BLE001  the point is to print what ends the call
                ended = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        print(f"{name:<7} resolver runs={RUNS[name]} questions shown={len(seen)} "
              f"distinct={len(set(seen))} ended: {ended}")


async def main() -> None:
    await the_question()
    await four_answers()
    await same_arguments()
    await declined_preview()
    await resolver_rounds()


if __name__ == "__main__":
    asyncio.run(main())
