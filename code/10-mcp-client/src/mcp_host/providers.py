"""Talking to a model, without picking one.

Post 11. The loop in loop.py does not know or care which model it is driving.
It knows one small protocol: turn a catalog into whatever shape this provider
wants, send messages, and append things to a history the provider owns.

Two reasons that indirection earns its keep. The obvious one is that swapping
providers becomes a one-line change. The less obvious one is that it makes the
loop *testable without a network*: `ScriptedProvider` below implements the
same protocol with a fixed plan, so every test of the tool loop, the catalog
and the permission gate runs offline, deterministically, with no API key.

**On model ids.** There is not one in this file. Model ids are retired on a
schedule -- anything hardcoded in a tutorial is a 404 waiting to happen, and
this series has already outlived one -- so a real adapter reads its id from
the environment and fails with a readable message when it is missing.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol, Sequence, runtime_checkable

from .catalog import ToolCatalog


class ProviderUnavailable(RuntimeError):
    """The provider cannot run: no package, no key, or no model configured."""


@dataclass(frozen=True)
class ToolCall:
    """A model's request to run one tool, in provider-neutral form."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """What came back, ready to hand to the provider."""

    call: ToolCall
    text: str
    is_error: bool = False


@dataclass
class LLMReply:
    """One model turn: some text, some tool calls, or both."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The provider's own representation, kept so it can be appended to its own
    # history verbatim. The loop never looks inside it.
    raw: Any = None


@runtime_checkable
class LLMProvider(Protocol):
    """The whole surface the tool loop depends on."""

    name: str

    def format_tools(self, catalog: ToolCatalog) -> Any:
        """Translate MCP tool schemas into this provider's tool format."""

    async def send(self, messages: list[Any], tools: Any, system: str) -> LLMReply:
        """One request. Must not block the event loop."""

    def append_user(self, messages: list[Any], text: str) -> None: ...

    def append_assistant(self, messages: list[Any], reply: LLMReply) -> None: ...

    def append_tool_results(
        self, messages: list[Any], results: Sequence[ToolResult]
    ) -> None: ...


# --------------------------------------------------------------------------
# The reference provider: no key, no network, fully deterministic
# --------------------------------------------------------------------------

# One step of a plan is either a final answer (a string) or the tool calls the
# model wants to make (a list of `(tool_name, arguments)` pairs).
Step = "str | Sequence[tuple[str, dict[str, Any]]]"


class ScriptedProvider:
    """A model stand-in that follows a written plan.

    This is not a mock in the throwaway sense. It implements `LLMProvider`
    exactly, keeps a real message history, and exercises every branch of the
    loop: parallel calls, several rounds, tool failures fed back as text. What
    it does not do is choose. That makes it useless for a demo of model
    behaviour and ideal for a test of everything around the model.
    """

    name = "scripted"

    def __init__(self, plan: Iterable[Any], *, fallback: str = "Done.") -> None:
        self._plan = list(plan)
        self._step = 0
        self._fallback = fallback
        # Recorded so tests can assert on what the loop actually sent.
        self.sent_tools: Any = None
        self.rounds = 0

    def format_tools(self, catalog: ToolCatalog) -> list[dict[str, Any]]:
        return [
            {
                "name": entry.name,
                "description": entry.description,
                "input_schema": entry.tool.input_schema,
            }
            for entry in catalog
        ]

    async def send(self, messages: list[Any], tools: Any, system: str) -> LLMReply:
        self.sent_tools = tools
        self.rounds += 1
        if self._step >= len(self._plan):
            return LLMReply(text=self._fallback, raw={"text": self._fallback})

        step = self._plan[self._step]
        self._step += 1

        if isinstance(step, str):
            return LLMReply(text=step, raw={"text": step})

        calls = [
            ToolCall(id=f"call_{self._step}_{i}", name=name, arguments=dict(args))
            for i, (name, args) in enumerate(step)
        ]
        return LLMReply(tool_calls=calls, raw={"tool_calls": calls})

    def append_user(self, messages: list[Any], text: str) -> None:
        messages.append({"role": "user", "content": text})

    def append_assistant(self, messages: list[Any], reply: LLMReply) -> None:
        messages.append({"role": "assistant", "content": reply.raw})

    def append_tool_results(
        self, messages: list[Any], results: Sequence[ToolResult]
    ) -> None:
        # All results in one turn. Splitting them across several messages is
        # the mistake that quietly teaches a model to stop asking for parallel
        # calls, on every provider that supports them.
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.text,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )


# --------------------------------------------------------------------------
# A real adapter, which degrades cleanly when it cannot run
# --------------------------------------------------------------------------


class AnthropicProvider:
    """Adapter for the Anthropic Messages API.

    Optional in every sense: the package is an extra, the key is read from the
    environment, and the model id is configuration. `available()` answers
    without raising so a CLI can offer the scripted provider instead of
    crashing on import.
    """

    name = "anthropic"

    # Env vars rather than constants. Model ids are retired on a published
    # schedule; a literal id checked into a repository has a shelf life.
    MODEL_ENV = "MCP_HOST_MODEL"
    KEY_ENV = "ANTHROPIC_API_KEY"

    def __init__(self, model: str | None = None, *, max_tokens: int = 4096) -> None:
        model = model or os.environ.get(self.MODEL_ENV)
        if not model:
            raise ProviderUnavailable(
                f"set {self.MODEL_ENV} to the model id you want to use. "
                "Model ids change; this project deliberately does not ship one."
            )
        if not os.environ.get(self.KEY_ENV):
            raise ProviderUnavailable(f"{self.KEY_ENV} is not set")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise ProviderUnavailable(
                "the 'anthropic' package is not installed; "
                "install this project with the [anthropic] extra"
            ) from exc

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    @classmethod
    def available(cls) -> bool:
        """True if a call would work, without making one."""
        if not os.environ.get(cls.MODEL_ENV) or not os.environ.get(cls.KEY_ENV):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def format_tools(self, catalog: ToolCatalog) -> list[dict[str, Any]]:
        # MCP's tool shape is close to this one already: name, description,
        # and a JSON Schema. The key is `input_schema`, and note that reading
        # it off the model is `tool.input_schema`, snake_case -- the camelCase
        # `inputSchema` exists only on the wire.
        return [
            {
                "name": entry.name,
                "description": entry.description,
                "input_schema": entry.tool.input_schema,
            }
            for entry in catalog
        ]

    async def send(self, messages: list[Any], tools: Any, system: str) -> LLMReply:
        # The SDK client is synchronous. Calling it directly from a coroutine
        # blocks the event loop for the whole round trip, which on a slow
        # response is seconds during which no other server can be reached and
        # no read timeout can fire. `to_thread` costs one line.
        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]
        text = "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        return LLMReply(text=text or None, tool_calls=calls, raw=response.content)

    def append_user(self, messages: list[Any], text: str) -> None:
        messages.append({"role": "user", "content": text})

    def append_assistant(self, messages: list[Any], reply: LLMReply) -> None:
        messages.append({"role": "assistant", "content": reply.raw})

    def append_tool_results(
        self, messages: list[Any], results: Sequence[ToolResult]
    ) -> None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call.id,
                        "content": r.text,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )


#: Providers the CLI knows about. The scripted one is first because it is the
#: only one guaranteed to work.
PROVIDERS: dict[str, Callable[[], Any]] = {
    "scripted": lambda: ScriptedProvider([]),
    "anthropic": AnthropicProvider,
}


def get_provider(name: str, **kwargs: Any) -> Any:
    """Build a provider by name, or explain why it cannot be built."""
    factory = PROVIDERS.get(name)
    if factory is None:
        known = ", ".join(sorted(PROVIDERS))
        raise ProviderUnavailable(f"unknown provider {name!r}; known: {known}")
    if name == "scripted":
        return ScriptedProvider(kwargs.get("plan") or [])
    return factory(**kwargs)


def parse_json_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Tolerate a provider that hands back arguments as a JSON string."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
