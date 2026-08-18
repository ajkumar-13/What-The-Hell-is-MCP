"""Answering a server that asks a question mid-call.

Post 10, and the client-side view of post 08. Under revision 2026-07-28 a
server has no back-channel: it cannot call into the client and block. So when
a tool needs an answer it does not *ask*, it *returns* -- an
`InputRequiredResult` carrying the questions and an opaque `request_state`.
The client answers and calls the same tool again, same name, same arguments,
with `input_responses` and the echoed `request_state` attached.

There are two ways to be that client, and this module has both.

**The automatic one.** Hand `Client(...)` an `elicitation_callback` and it
drives the whole loop for you: it dispatches each request, retries with the
answers, and hands back a plain `CallToolResult`. Bounded by
`input_required_max_rounds` (10 by default). This is what you want in a real
host. `as_elicitation_callback` adapts a responder to that signature.

**The manual one.** `call_with_input` drives the loop by hand through
`client.session.call_tool(..., allow_input_required=True)`, which returns the
`InputRequiredResult` instead of resolving it. Slower to write and identical
in effect, but it is the only way to *see* the mechanism, and a host that
wants to batch several servers' questions into one dialog needs it.

One thing that is not optional either way: the client must still declare the
elicitation capability, which it does by having an `elicitation_callback` at
all. Connect without one and a server whose tool needs an answer fails the
call with `-32021` before it ever gets to ask.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from mcp_types import (
    CallToolResult,
    CreateMessageRequest,
    ElicitRequest,
    ElicitResult,
    InputRequiredResult,
    ListRootsRequest,
    ListRootsResult,
)


@runtime_checkable
class InputResponder(Protocol):
    """How this host answers a server's question.

    One method, because form elicitation is the only request type a host is
    obliged to support. Sampling and roots are both deprecated as of
    2026-07-28; the default handling below declines them politely rather than
    pretending.
    """

    async def elicit(self, params: Any) -> ElicitResult: ...


class ScriptedResponder:
    """A responder that always answers the same way. For tests and demos."""

    def __init__(
        self,
        action: str = "decline",
        content: dict[str, Any] | None = None,
        *,
        seen: list[str] | None = None,
    ) -> None:
        self.action = action
        self.content = content
        # Recording the questions is how a test asserts that a server asks
        # once per call rather than once per round.
        self.seen = seen if seen is not None else []

    async def elicit(self, params: Any) -> ElicitResult:
        self.seen.append(getattr(params, "message", ""))
        return ElicitResult(action=self.action, content=self.content)


class ConsoleResponder:
    """Asks the human at the terminal.

    Every read goes through `asyncio.to_thread`. A bare `input()` inside a
    coroutine blocks the event loop: nothing else progresses, no other
    server's call completes, and any read timeout you configured fires while
    the loop sits waiting on stdin. It is the single easiest way to make an
    async host feel broken.

    Output is ASCII only. A checkmark or an arrow written to a Windows console
    under cp1252 raises `UnicodeEncodeError` and takes the host down with it.
    """

    async def elicit(self, params: Any) -> ElicitResult:
        mode = getattr(params, "mode", "form")
        message = getattr(params, "message", "")

        if mode == "url":
            url = getattr(params, "url", "")
            print(f"\n[server asks] {message}")
            print(f"  open: {url}")
            answer = await _ask("  done? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                return ElicitResult(action="decline")
            return ElicitResult(action="accept")

        schema = getattr(params, "requested_schema", None) or {}
        properties: dict[str, Any] = schema.get("properties") or {}
        required = set(schema.get("required") or [])

        print(f"\n[server asks] {message}")
        answer = await _ask("  answer? [y/N/c to cancel] ")
        choice = answer.strip().lower()
        if choice in {"c", "cancel"}:
            return ElicitResult(action="cancel")
        if choice not in {"y", "yes"}:
            return ElicitResult(action="decline")

        content: dict[str, Any] = {}
        for key, prop in properties.items():
            label = prop.get("description") or key
            raw = (await _ask(f"  {label}: ")).strip()
            if not raw and key not in required:
                continue
            content[key] = _coerce(raw, prop.get("type"))
        return ElicitResult(action="accept", content=content)


async def _ask(prompt: str) -> str:
    """Read one line without stalling the event loop.

    An EOF (piped input that ran out, a closed terminal) reads as an empty
    answer, which every caller below treats as "no". Crashing the host because
    stdin closed would be the wrong failure.
    """
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return ""


def _coerce(raw: str, kind: str | None) -> Any:
    """Turn a console string into what the requested schema asked for."""
    if kind == "boolean":
        return raw.strip().lower() in {"y", "yes", "true", "1"}
    if kind == "integer":
        try:
            return int(raw)
        except ValueError:
            return 0
    if kind == "number":
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return raw


def as_elicitation_callback(responder: InputResponder) -> Any:
    """Adapt a responder to the callback `Client(...)` expects.

    The SDK's callback takes `(context, params)`; `context` is request
    plumbing a host rarely needs, so it is dropped here.
    """

    async def callback(context: Any, params: Any) -> ElicitResult:
        return await responder.elicit(params)

    return callback


async def call_with_input(
    client: Any,
    name: str,
    arguments: dict[str, Any] | None,
    responder: InputResponder,
    *,
    max_rounds: int = 8,
) -> CallToolResult:
    """Drive the multi-round trip by hand.

    Three rules hold this together, and breaking any of them makes the call
    loop until it hits the round cap:

    1. Same tool name and the *same arguments* on every leg. The arguments are
       not "already sent"; each leg is a complete, independent call.
    2. Echo `request_state` back verbatim. It is sealed by the server and is
       how it recognizes the continuation.
    3. Key the responses by the same keys the server used in `input_requests`.
    """
    session = getattr(client, "client", client)
    session = getattr(session, "session", session)
    arguments = arguments or {}

    result = await session.call_tool(name, arguments, allow_input_required=True)

    for _ in range(max_rounds):
        if not isinstance(result, InputRequiredResult):
            return result

        responses: dict[str, Any] = {}
        for key, request in (result.input_requests or {}).items():
            responses[key] = await _fulfil(request, responder)

        result = await session.call_tool(
            name,
            arguments,
            allow_input_required=True,
            input_responses=responses,
            request_state=result.request_state,
        )

    raise RuntimeError(
        f"tool {name!r} still asking for input after {max_rounds} rounds; "
        "the server is most likely rewording its question between rounds, "
        "so the recorded answer never matches its digest"
    )


async def _fulfil(request: Any, responder: InputResponder) -> Any:
    """Answer one entry of `input_requests`."""
    if isinstance(request, ElicitRequest):
        return await responder.elicit(request.params)
    if isinstance(request, ListRootsRequest):
        # Roots are deprecated as of 2026-07-28. An empty list is an honest
        # answer: this host exposes no filesystem roots.
        return ListRootsResult(roots=[])
    if isinstance(request, CreateMessageRequest):
        raise NotImplementedError(
            "this host does not offer sampling; the server asked for a model "
            "completion, which the 2026-07-28 revision deprecates"
        )
    raise NotImplementedError(f"unsupported input request: {type(request).__name__}")
