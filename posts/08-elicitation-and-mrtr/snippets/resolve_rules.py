"""Post 08, sections 2, 6 and 9: the rules Resolve enforces, each one tried.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/resolve_rules.py

Registers deliberately wrong tools on throwaway MCPServer instances and prints
the InvalidSignature each one raises at registration. Then it calls a few
correct ones through an in-memory client and prints what happens: one resolver
behind two parameters, the thread a plain resolver runs on, the bare-model
annotation on a decline, a tool that tries the old back channel, a client with
no elicitation capability, Resolve on a prompt, and a tool that raises the
SDK's UrlElicitationRequiredError.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Annotated

from pydantic import BaseModel
from mcp import Client
from mcp.server.mcpserver import Context, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.shared.exceptions import UrlElicitationRequiredError
from mcp_types import ElicitRequestURLParams, ElicitResult, InputRequiredResult


class Ok(BaseModel):
    confirm: bool


def ask_confirm(pid: int) -> Elicit[Ok]:
    threads.append(threading.current_thread().name)
    return Elicit(f"Terminate process {pid}?", Ok)


threads: list[str] = []


# ---- registration: each of these is refused when the decorator runs --------

def in_a_union(pid: int, approval: Annotated[Ok, Resolve(ask_confirm)] | None = None) -> str: ...


def with_input_required(pid: int, approval: Annotated[Ok, Resolve(ask_confirm)]) -> str | InputRequiredResult: ...


def needs_user(user: str) -> Elicit[Ok]: ...


def unknown_parameter(pid: int, approval: Annotated[Ok, Resolve(needs_user)]) -> str: ...


def cycle_a(b: Annotated[Ok, Resolve(lambda: None)]) -> Elicit[Ok]: ...


def cycle_b(a: Annotated[Ok, Resolve(cycle_a)]) -> Elicit[Ok]: ...


cycle_a.__annotations__["b"] = Annotated[Ok, Resolve(cycle_b)]  # close the loop: a needs b, b needs a


def cyclic(pid: int, approval: Annotated[Ok, Resolve(cycle_a)]) -> str: ...


def two_arms(pid: int) -> Elicit[Ok] | Elicit[BaseModel]: ...


def asks_twice(pid: int, approval: Annotated[Ok, Resolve(two_arms)]) -> str: ...


def registration() -> None:
    print("== registration ==")
    for fn in (in_a_union, with_input_required, unknown_parameter, cyclic, asks_twice):
        try:
            MCPServer("rules").tool()(fn)
            print(f"{fn.__name__}: registered")
        except Exception as exc:  # noqa: BLE001 - printing what the SDK raises
            print(f"{fn.__name__}: {type(exc).__name__}: {exc}")


# ---- calls ------------------------------------------------------------------

mcp = MCPServer("rules")


@mcp.tool()
def both(
    pid: int,
    a: Annotated[ElicitationResult[Ok], Resolve(ask_confirm)],
    b: Annotated[ElicitationResult[Ok], Resolve(ask_confirm)],
) -> str:
    return f"a={a.action} b={b.action}"


@mcp.tool()
def bare(pid: int, approval: Annotated[Ok, Resolve(ask_confirm)]) -> str:
    return f"confirm={approval.confirm}"


@mcp.tool()
async def old_style(pid: int, ctx: Context) -> str:
    answer = await ctx.elicit(f"Terminate process {pid}?", Ok)
    return str(answer)


@mcp.tool()
def needs_url(pid: int) -> str:
    raise UrlElicitationRequiredError(
        [ElicitRequestURLParams(message="Sign in first.", url="https://mcp.example.com/connect")]
    )


@mcp.prompt()
def confirm_prompt(pid: int, approval: Annotated[Ok, Resolve(ask_confirm)]) -> str:
    return f"pid {pid}"


async def calls() -> None:
    shown: list[str] = []

    def user(action: str, content: dict | None = None):
        async def callback(context, params):
            shown.append(params.message)
            return ElicitResult(action=action, content=content)

        return callback

    print("\n== calls ==")
    async with Client(mcp, elicitation_callback=user("accept", {"confirm": True})) as client:
        result = await client.call_tool("both", {"pid": 7})
        print(f"one resolver behind two parameters: {result.content[0].text}; questions shown: {len(shown)}")
        print(f"thread the plain resolver ran on: {threads[-1]!r} (the call itself: {threading.current_thread().name!r})")

    async with Client(mcp, elicitation_callback=user("decline")) as client:
        result = await client.call_tool("bare", {"pid": 7})
        print(f"bare-model annotation, user declines: isError={result.is_error}: {result.content[0].text}")
        try:
            await client.call_tool("old_style", {"pid": 7})
        except Exception as exc:  # noqa: BLE001 - printing what comes back
            print(f"ctx.elicit() called in the tool body: {type(exc).__name__} {exc.error.code}: {exc}")

    async with Client(mcp) as client:  # no elicitation_callback, so no elicitation capability
        try:
            await client.call_tool("bare", {"pid": 7})
        except Exception as exc:  # noqa: BLE001 - printing what comes back
            print(f"client with no elicitation capability: {type(exc).__name__} {exc.error.code}: {exc}")

        prompt = next(p for p in (await client.list_prompts()).prompts if p.name == "confirm_prompt")
        print(f"Resolve on a prompt: published arguments {[a.name for a in prompt.arguments]}")
        try:
            await client.get_prompt("confirm_prompt", {"pid": "7"})
        except Exception as exc:  # noqa: BLE001
            print(f"prompts/get confirm_prompt: {type(exc).__name__} {exc.error.code}: {exc}")

        try:
            await client.call_tool("needs_url", {"pid": 7})
        except Exception as exc:  # noqa: BLE001
            print(f"a tool raising UrlElicitationRequiredError: {type(exc).__name__} {exc.error.code}: {exc}")


if __name__ == "__main__":
    import logging

    logging.disable(logging.CRITICAL)  # the prompt failure logs a traceback; this prints only the outcome
    registration()
    asyncio.run(calls())
