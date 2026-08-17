"""The tasks extension, hand-built on the seams the SDK actually gives you.

This is not the tasks extension. It is the smallest program that proves the
2.0.0b2 SDK can carry one: a server that decides per request whether to hand
back a task, a `tasks/get` method that did not exist until this file defined
it, and a client that resolves `resultType: "task"` back into an ordinary
`CallToolResult` without its caller ever learning a task happened.

    uv run --with 'mcp==2.0.0b2' python tasks_extension.py

Expected output, from an in-memory client and server in one process:

    server advertises: {'io.modelcontextprotocol/tasks': {}}
    poll 1 -> working
    poll 2 -> completed
    tasks client got: done 3
    plain client got: done 3   (resultType complete, no task involved)

Two shortcuts, so the file stays readable. The work runs eagerly inside the
interceptor rather than out of band, and the store is a dict rather than
anything durable. A real server must do neither: the specification forbids
returning a task before a `tasks/get` for it would resolve.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from mcp.client import Client, ClientExtension
from mcp.client.extension import ClaimContext, ResultClaim
from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver import MCPServer, require_client_extension
from mcp_types import CallToolResult, Request, RequestParams, Result

TASKS = "io.modelcontextprotocol/tasks"

# Stand-in for durable storage. Keyed by taskId, which is the only handle there
# is: no session, no connection, no per-caller scope.
STORE: dict[str, dict[str, Any]] = {}


# --- wire shapes the core schema does not define ---------------------------


class GetTaskParams(RequestParams):
    task_id: str


class GetTaskRequest(Request[GetTaskParams, Literal["tasks/get"]]):
    method: Literal["tasks/get"] = "tasks/get"
    params: GetTaskParams

    # The SDK mirrors this params key into the Mcp-Name header on every send,
    # which is what lets an intermediary route the poll to the instance holding
    # the state. Its own docstring credits SEP-2663 for the requirement.
    name_param = "taskId"


class GetTaskResult(Result):
    task_id: str
    status: str
    poll_interval_ms: int
    result: dict[str, Any] | None = None


class CreateTaskResult(Result):
    result_type: Literal["task"]
    task_id: str
    status: str
    poll_interval_ms: int


# --- server side -----------------------------------------------------------


class TasksExtension(Extension):
    """Adds one method and one interceptor. Adds no tools."""

    identifier = TASKS

    def methods(self) -> tuple[MethodBinding, ...]:
        async def get_task(ctx: Any, params: GetTaskParams) -> dict[str, Any]:
            # Re-checked here, not just on the tools/call. A capability the
            # client did not assert on *this* request does not exist.
            require_client_extension(ctx, TASKS)
            task = STORE[params.task_id]
            if task["polls_left"] > 0:  # stands in for the work still running
                task["polls_left"] -= 1
                return {"resultType": "complete", **_public(task)}
            task["status"] = "completed"
            # resultType is "complete", not "task". "task" appears exactly once
            # per task, on the CreateTaskResult that minted it.
            return {"resultType": "complete", **_public(task), "result": task["result"]}

        return (
            MethodBinding(
                method="tasks/get", params_type=GetTaskParams, handler=get_task
            ),
        )

    async def intercept_tool_call(self, params: Any, ctx: Any, call_next: Any) -> Any:
        client = ctx.session.client_params
        declared = client.capabilities.extensions if client else None
        if not declared or TASKS not in declared:
            return await call_next(ctx)  # core behavior, per SEP-2133

        finished = await call_next(ctx)  # shortcut: see the module docstring
        task_id = str(uuid.uuid4())  # uuid4, because the id is the access boundary
        STORE[task_id] = {
            "taskId": task_id,
            "status": "working",
            "pollIntervalMs": 20,
            "polls_left": 1,
            "result": _wire(finished),
        }
        return {"resultType": "task", **_public(STORE[task_id])}


def _public(task: dict[str, Any]) -> dict[str, Any]:
    """The task minus its result and minus this file's bookkeeping."""
    return {k: v for k, v in task.items() if k not in ("result", "polls_left")}


def _wire(result: Any) -> dict[str, Any]:
    """A handler result as JSON. The chain hands back a model or a plain dict."""
    if hasattr(result, "model_dump"):
        return result.model_dump(by_alias=True, mode="json", exclude_none=True)
    return dict(result)


# --- client side -----------------------------------------------------------


async def resolve(created: CreateTaskResult, ctx: ClaimContext) -> CallToolResult:
    """Turn a task back into the result the caller asked for."""
    attempt = 0
    while True:
        attempt += 1
        got = await ctx.session.send_request(
            GetTaskRequest(params=GetTaskParams(task_id=created.task_id)),
            GetTaskResult,
        )
        print(f"poll {attempt} -> {got.status}")
        if got.status == "completed":
            return CallToolResult.model_validate(got.result)
        if got.status in ("failed", "cancelled"):
            raise RuntimeError(f"task {created.task_id} ended {got.status}")
        await asyncio.sleep(got.poll_interval_ms / 1000)


class TasksClientExtension(ClientExtension):
    identifier = TASKS

    def claims(self) -> tuple[ResultClaim[CreateTaskResult], ...]:
        return (
            ResultClaim(result_type="task", model=CreateTaskResult, resolve=resolve),
        )


# --- the demonstration -----------------------------------------------------

mcp = MCPServer("tasks-demo", extensions=[TasksExtension()])


@mcp.tool()
def render(n: int = 1) -> str:
    """Pretend to be slow."""
    return f"done {n}"


async def main() -> None:
    async with Client(mcp, extensions=[TasksClientExtension()]) as tasks_client:
        print("server advertises:", tasks_client.server_capabilities.extensions)
        result = await tasks_client.call_tool("render", {"n": 3})
        print("tasks client got:", result.content[0].text)

    # Same server, same process, same registered tool. This client never
    # declares the extension, so the interceptor falls through to core.
    async with Client(mcp) as plain_client:
        result = await plain_client.call_tool("render", {"n": 3})
        print(
            f"plain client got: {result.content[0].text}   "
            f"(resultType {result.result_type}, no task involved)"
        )


if __name__ == "__main__":
    asyncio.run(main())
