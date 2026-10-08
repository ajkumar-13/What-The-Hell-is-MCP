"""Post 09, section 10: the tasks extension, hand-built on the SDK's seams.

Run from the series root:
    uv run python posts/09-tasks/snippets/tasks_extension.py

This is not the tasks extension. It is the smallest program that shows the
mcp 2.3.0 SDK can carry one: a server that decides per request whether to hand
back a task, `tasks/get` and `tasks/cancel` methods that did not exist until
this file defined them, and a client that resolves `resultType: "task"` back
into an ordinary `CallToolResult` without its caller learning a task happened.

The slow work is the real `watch_cpu` from code/05-first-server/, registered a
second time on a demonstration server that carries the extension. Everything
runs in memory in one process, as that project's tests do: no subprocess, no
socket. tasks_by_hand.py imports this module and drives the same server one
request at a time.

Two shortcuts keep the file readable, and a real server can afford neither.
The store is a dict in this process, and the work runs as an asyncio task in
the same process. The specification forbids returning a task before a
`tasks/get` for it would resolve, which a dict satisfies only because nothing
else ever reads it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp import Client  # noqa: E402
from mcp.client import ClientExtension  # noqa: E402
from mcp.client.extension import ClaimContext, ResultClaim  # noqa: E402
from mcp.server.extension import Extension, MethodBinding  # noqa: E402
from mcp.server.mcpserver import MCPServer, require_client_extension  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import CallToolResult, Request, RequestParams, Result  # noqa: E402

from system_info.progress import watch_cpu  # noqa: E402

TASKS = "io.modelcontextprotocol/tasks"

# Stand-in for durable storage, keyed by taskId: the only handle there is. No
# session, no connection, no per-caller scope.
STORE: dict[str, dict[str, Any]] = {}
RUNNING: dict[str, asyncio.Task[None]] = {}


# --- wire shapes the core schema does not define ---------------------------


class TaskIdParams(RequestParams):
    task_id: str


class GetTaskRequest(Request[TaskIdParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: TaskIdParams

    # The SDK mirrors this params key into the Mcp-Name header on every send,
    # which is what lets an intermediary route the poll to the instance holding
    # the state. Its own docstring credits SEP-2663 for the requirement.
    name_param = "taskId"


class CancelTaskRequest(Request[TaskIdParams, Literal["tasks/cancel"]]):
    method: Literal["tasks/cancel"] = "tasks/cancel"
    params: TaskIdParams
    name_param = "taskId"


class GetTaskResult(Result):
    # Literal["complete"]: a tasks/get result that said "task" would fail here.
    result_type: Literal["complete"]
    task_id: str
    status: str
    created_at: str
    last_updated_at: str
    ttl_ms: int | None
    poll_interval_ms: int
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AckResult(Result):
    """The empty acknowledgment of tasks/cancel (and tasks/update)."""

    result_type: Literal["complete"]


class CreateTaskResult(Result):
    result_type: Literal["task"]
    task_id: str
    status: str
    created_at: str
    last_updated_at: str
    ttl_ms: int | None
    poll_interval_ms: int


# --- server side -----------------------------------------------------------


def declared_tasks(ctx: Any) -> bool:
    """Did the client put the extension in the capabilities of THIS request?"""
    caps = ctx.session.client_capabilities
    return bool(caps and caps.extensions and TASKS in caps.extensions)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def lookup(task_id: str) -> dict[str, Any]:
    if task_id not in STORE:
        raise MCPError(code=-32602, message="Failed to retrieve task: Task not found")
    return STORE[task_id]


def public(task: dict[str, Any]) -> dict[str, Any]:
    """The task's wire fields, minus the final result."""
    return {k: v for k, v in task.items() if k != "result"}


class TasksExtension(Extension):
    """Adds two methods and one interceptor. Adds no tools."""

    identifier = TASKS

    def methods(self) -> tuple[MethodBinding, ...]:
        async def get_task(ctx: Any, params: TaskIdParams) -> dict[str, Any]:
            # Re-checked here, not only on the tools/call. A capability the
            # client did not assert on *this* request does not exist.
            require_client_extension(ctx, TASKS)
            task = lookup(params.task_id)
            # resultType is "complete", not "task": "task" appears exactly once
            # per task, on the CreateTaskResult that minted it.
            body = {"resultType": "complete", **public(task)}
            if task["status"] == "completed":
                body["result"] = task["result"]
            return body

        async def cancel_task(ctx: Any, params: TaskIdParams) -> dict[str, Any]:
            require_client_extension(ctx, TASKS)
            lookup(params.task_id)
            running = RUNNING.get(params.task_id)
            if running is not None and not running.done():
                running.cancel()  # this server chooses to stop; the spec only asks it to acknowledge
            return {"resultType": "complete"}

        return (
            MethodBinding(method="tasks/get", params_type=TaskIdParams, handler=get_task),
            MethodBinding(method="tasks/cancel", params_type=TaskIdParams, handler=cancel_task),
        )

    async def intercept_tool_call(self, params: Any, ctx: Any, call_next: Any) -> Any:
        if not declared_tasks(ctx):
            return await call_next(ctx)  # core behavior, as SEP-2133 requires
        if params.name != "watch_cpu":
            return await call_next(ctx)  # only the slow tool becomes a task
        arguments = dict(params.arguments or {})
        if arguments.get("seconds", 5) < 2:
            return await call_next(ctx)  # the server's choice: short work is answered directly

        task_id = str(uuid.uuid4())  # random, because the id is the access boundary
        stamp = now()
        STORE[task_id] = {
            "taskId": task_id,
            "status": "working",
            "createdAt": stamp,
            "lastUpdatedAt": stamp,
            "ttlMs": 60000,
            "pollIntervalMs": 500,
        }
        RUNNING[task_id] = asyncio.create_task(run(task_id, arguments))
        return {"resultType": "task", **STORE[task_id]}


async def run(task_id: str, arguments: dict[str, Any]) -> None:
    """The work, out of band. There is no request to report progress on, so
    watch_cpu runs with ctx=None, the branch that skips report_progress."""
    task = STORE[task_id]
    try:
        watch = await watch_cpu(**arguments)
        data = asdict(watch)
        task["result"] = {
            "content": [{"type": "text", "text": json.dumps(data)}],
            "structuredContent": data,
            "isError": False,
        }
        task["status"] = "completed"
    except asyncio.CancelledError:
        task["status"] = "cancelled"
    except Exception as exc:  # a fault in the machinery, not a tool error
        task["error"] = {"code": -32603, "message": str(exc)}
        task["status"] = "failed"
    task["lastUpdatedAt"] = now()


server = MCPServer("tasks-demo", extensions=[TasksExtension()])
server.add_tool(watch_cpu, title="Watch CPU over time")


# --- client side -----------------------------------------------------------


async def resolve(created: CreateTaskResult, ctx: ClaimContext) -> CallToolResult:
    """Turn a task back into the result the caller asked for."""
    seen: list[str] = []
    while True:
        got = await ctx.session.send_request(
            GetTaskRequest(params=TaskIdParams(task_id=created.task_id)), GetTaskResult
        )
        if not seen or seen[-1] != got.status:
            seen.append(got.status)
        if got.status == "completed":
            print("  statuses seen while polling:", " -> ".join(seen))
            return CallToolResult.model_validate(got.result)
        if got.status in ("failed", "cancelled"):
            raise RuntimeError(f"task {created.task_id} ended {got.status}")
        await asyncio.sleep(got.poll_interval_ms / 1000)


class TasksClientExtension(ClientExtension):
    identifier = TASKS

    def claims(self) -> tuple[ResultClaim[CreateTaskResult], ...]:
        return (ResultClaim(result_type="task", model=CreateTaskResult, resolve=resolve),)


# --- the demonstration -----------------------------------------------------


def summary(result: CallToolResult) -> str:
    data = result.structured_content or {}
    return f"resultType {result.result_type}, {len(data.get('samples', []))} samples"


async def main() -> None:
    async with Client(server, extensions=[TasksClientExtension()]) as tasks_client:
        print("server advertises:", tasks_client.server_capabilities.extensions)
        print("tasks client, watch_cpu seconds=1:")
        result = await tasks_client.call_tool("watch_cpu", {"seconds": 1})
        print("  got", summary(result), "(answered directly)")
        print("tasks client, watch_cpu seconds=3:")
        result = await tasks_client.call_tool("watch_cpu", {"seconds": 3})
        print("  got", summary(result))

    # Same server, same process, same registered tool. This client never
    # declares the extension, so the interceptor falls through to core.
    async with Client(server) as plain_client:
        print("plain client, watch_cpu seconds=3:")
        result = await plain_client.call_tool("watch_cpu", {"seconds": 3})
        print("  got", summary(result), "(no task involved)")


if __name__ == "__main__":
    asyncio.run(main())
