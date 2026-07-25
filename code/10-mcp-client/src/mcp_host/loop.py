"""The tool-execution loop.

Post 11. The hero diagram of that post is this file: send, decide, gate,
execute, feed back, repeat. Everything interesting is in the ordering.

    user text
        |
        v
    +-> send to the model ---- no tool calls ----> final answer
    |       |
    |       | tool calls
    |       v
    |   PERMISSION GATE   <-- the only place a call can be stopped
    |       |
    |       v
    |   execute approved calls, all at once
    |       |
    |       v
    +-- feed every result back, in one turn

Three properties are load-bearing.

**The gate is on the critical path, not beside it.** There is no code path
from a model's tool call to a server that does not pass through
`gate.check`. That is the difference between a permission system and a
permission-shaped log line.

**Gating is sequential, execution is parallel.** Two prompts racing for the
same terminal is unusable, so the gate runs one call at a time; the approved
calls then run together under `asyncio.gather`, because a host that waits for
four servers in sequence feels four times slower than it is.

**Every requested call gets a result.** Denied, unknown, failed: each one
comes back with an entry, in the order the model asked, in a single turn.
Dropping the result of a denied call leaves a dangling tool-use id that most
providers reject outright, and returning them in separate turns teaches the
model to stop asking for parallel calls.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .catalog import ServerPool, UnknownTool
from .permissions import PermissionGate, request_for
from .providers import LLMProvider, ToolCall, ToolResult
from .results import ToolOutcome

DEFAULT_SYSTEM = (
    "You are a command-line assistant with access to local tools over MCP.\n"
    "Rules:\n"
    "- Use the tools for anything about this machine. Do not guess.\n"
    "- A tool result beginning with TOOL FAILED means the call did not "
    "succeed. Say so and suggest a next step; never present it as an answer.\n"
    "- Some calls need the user's permission and may be refused. A refusal is "
    "the user's decision: report it, do not retry the same call.\n"
    "- Keep answers short unless asked for detail."
)


@dataclass
class Turn:
    """What one user message produced."""

    text: str
    rounds: int = 0
    results: list[ToolResult] = field(default_factory=list)
    stopped_early: bool = False


class ToolLoop:
    """A model, a catalog, and a gate between them."""

    def __init__(
        self,
        pool: ServerPool,
        gate: PermissionGate,
        provider: LLMProvider,
        *,
        system: str = DEFAULT_SYSTEM,
        max_rounds: int = 8,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.pool = pool
        self.gate = gate
        self.provider = provider
        self.system = system
        # A model that keeps calling tools forever is a real failure mode, and
        # the bound is cheaper than the incident.
        self.max_rounds = max_rounds
        self._on_event = on_event or (lambda kind, data: None)
        # Owned by the provider; the loop only passes it along.
        self.messages: list[Any] = []

    def _emit(self, kind: str, **data: Any) -> None:
        self._on_event(kind, data)

    async def run(self, user_text: str) -> Turn:
        """Drive one user message to a final answer."""
        provider = self.provider
        tools = provider.format_tools(self.pool.catalog)
        provider.append_user(self.messages, user_text)

        turn = Turn(text="")
        for round_index in range(self.max_rounds):
            turn.rounds = round_index + 1
            reply = await provider.send(self.messages, tools, self.system)
            provider.append_assistant(self.messages, reply)

            if not reply.tool_calls:
                turn.text = reply.text or "(no output)"
                return turn

            self._emit("round", index=turn.rounds, calls=len(reply.tool_calls))
            results = await self._run_calls(reply.tool_calls)
            turn.results.extend(results)
            provider.append_tool_results(self.messages, results)

        # Out of rounds. Say so rather than returning the last tool result as
        # if it were an answer.
        turn.stopped_early = True
        turn.text = (
            f"Stopped after {self.max_rounds} tool rounds without a final answer."
        )
        return turn

    async def _run_calls(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
        """Gate every call, then run the approved ones together."""
        approved: list[ToolCall] = []
        outcomes: dict[str, ToolOutcome] = {}

        for call in calls:
            try:
                entry = self.pool.catalog.resolve(call.name)
            except UnknownTool:
                # Nothing to gate: the tool does not exist. Tell the model.
                outcomes[call.id] = ToolOutcome.failed(
                    call.name,
                    "no such tool. Check the tool list and try again.",
                )
                self._emit("unknown", tool=call.name)
                continue

            verdict = await self.gate.check(request_for(entry, call.arguments))
            self._emit(
                "gate",
                tool=call.name,
                allowed=verdict.allowed,
                reason=verdict.reason,
            )
            if verdict.allowed:
                approved.append(call)
            else:
                outcomes[call.id] = ToolOutcome.failed(
                    call.name, f"permission denied by the user ({verdict.reason})"
                )

        if approved:
            self._emit("execute", tools=[c.name for c in approved])
            gathered = await asyncio.gather(
                *(self.pool.call(c.name, c.arguments) for c in approved)
            )
            for call, outcome in zip(approved, gathered):
                outcomes[call.id] = outcome

        # Rebuilt in the order the model asked, so ids line up on the way back.
        return [
            ToolResult(
                call=call,
                text=outcomes[call.id].for_model(),
                is_error=not outcomes[call.id].ok,
            )
            for call in calls
        ]
