"""Post 09, sections 5 to 9: drive one task at a time, by hand.

Run from the series root:
    uv run python posts/09-tasks/snippets/tasks_by_hand.py

Uses the demonstration server of tasks_extension.py (the real watch_cpu behind
a hand-built tasks extension) and sends every request itself through the
client's session, so nothing is resolved behind its back. It prints each
result in its wire spelling: the CreateTaskResult, the polls to a terminal
status, a run cancelled mid-flight, a poll for a task id the server never
minted, and a poll from a client that did not declare the extension.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True  # leave no __pycache__ behind in snippets/ or code/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tasks_extension import (  # noqa: E402
    AckResult,
    CancelTaskRequest,
    CreateTaskResult,
    GetTaskRequest,
    GetTaskResult,
    TaskIdParams,
    TasksClientExtension,
    server,
)

from mcp import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import CallToolRequest, CallToolRequestParams, Result  # noqa: E402


def wire(result: Result, hide: tuple[str, ...] = ("_meta",)) -> dict[str, Any]:
    """A result as the wire carries it, minus keys that vary or do not matter here."""
    body = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    return {k: v for k, v in body.items() if k not in hide}


def masked(body: dict[str, Any]) -> str:
    """One line of JSON with the random id and the clock replaced by their kinds."""
    shown = dict(body)
    if "taskId" in shown:
        assert uuid.UUID(shown["taskId"]).version == 4
        shown["taskId"] = "<uuid4>"
    for key in ("createdAt", "lastUpdatedAt"):
        if key in shown:
            shown[key] = "<UTC timestamp>"
    return json.dumps(shown)


async def start(session: Any, seconds: int) -> CreateTaskResult:
    request = CallToolRequest(
        params=CallToolRequestParams(name="watch_cpu", arguments={"seconds": seconds})
    )
    return await session.send_request(request, CreateTaskResult)


async def poll(session: Any, task_id: str) -> GetTaskResult:
    return await session.send_request(
        GetTaskRequest(params=TaskIdParams(task_id=task_id)), GetTaskResult
    )


async def main() -> None:
    async with Client(server, extensions=[TasksClientExtension()]) as client:
        session = client.session

        print("== tools/call watch_cpu seconds=2, as a task ==")
        created = await start(session, 2)
        print("result:", masked(wire(created)))
        statuses: list[str] = []
        while True:
            got = await poll(session, created.task_id)
            if not statuses or statuses[-1] != got.status:
                statuses.append(got.status)
            if got.status not in ("working", "input_required"):
                break
            await asyncio.sleep(got.poll_interval_ms / 1000)
        print("statuses:", " -> ".join(statuses))
        final = wire(got, hide=("_meta", "result"))
        print("final tasks/get:", masked(final))
        inner = got.result or {}
        print(
            "result inside it: isError", json.dumps(inner.get("isError")),
            "with", len(inner["structuredContent"]["samples"]), "samples",
        )

        print("\n== tools/call watch_cpu seconds=30, cancelled mid-flight ==")
        created = await start(session, 30)
        await asyncio.sleep(1)
        print("poll before cancel:", (await poll(session, created.task_id)).status)
        ack = await session.send_request(
            CancelTaskRequest(params=TaskIdParams(task_id=created.task_id)), AckResult
        )
        print("tasks/cancel result:", json.dumps(wire(ack)))
        await asyncio.sleep(0.1)
        print("poll after cancel:", (await poll(session, created.task_id)).status)

        print("\n== tasks/get for a taskId the server never minted ==")
        try:
            await poll(session, str(uuid.uuid4()))
        except MCPError as err:
            print(f"error {err.error.code}: {err.error.message}")

    print("\n== tasks/get from a client that did not declare the extension ==")
    async with Client(server) as plain:
        try:
            await poll(plain.session, created.task_id)
        except MCPError as err:
            print(f"error {err.error.code}: {err.error.message}")
            print("data:", json.dumps(err.error.data))


asyncio.run(main())
