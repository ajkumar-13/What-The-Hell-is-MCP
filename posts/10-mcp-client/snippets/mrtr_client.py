"""Post 10, section 7: answering input_required from the client side.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/mrtr_client.py

Drives terminate_process on the server under code/05-first-server/, always
for pid 999999 (a pid that is not running, so nothing can be terminated).
Part one does it by hand over stdio through client.session.call_tool(...,
allow_input_required=True), recording the frames the client writes: it prints
the first leg's InputRequiredResult, the retry's params, and the second leg's
result. Part two lets the SDK drive the loop with a callback, in memory. Part
three connects without a callback. Part four uses call_with_input from
code/10-mcp-client/src/mcp_host/interactive.py and counts the questions.
Part five builds a throwaway server whose question changes every round and
shows where the call ends. The stdio subprocess is stopped before it exits.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from typing import Annotated  # noqa: E402

from mcp import Client, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.server.mcpserver import Elicit, ElicitationResult, MCPServer, Resolve  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from mcp_host.connection import ServerSpec, open_connection  # noqa: E402
from mcp_host.interactive import ScriptedResponder, as_elicitation_callback, call_with_input  # noqa: E402
from system_info import mcp  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed
QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}
params = StdioServerParameters(command=sys.executable, args=["-m", "system_info"], cwd=str(SRC), env=QUIET)
PID = {"pid": 999999}


def shorten(state: str) -> str:
    # The sealed part differs on every run; keep only the format prefix.
    return state.split(".", 1)[0] + ".<sealed>"


def answering(action: str, content: dict | None = None):
    return as_elicitation_callback(ScriptedResponder(action, content))


class Tap:
    def __init__(self, stream, frames: list[dict]) -> None:
        self._stream, self._frames = stream, frames

    async def send(self, message) -> None:
        self._frames.append(json.loads(message.message.model_dump_json(by_alias=True, exclude_unset=True)))
        await self._stream.send(message)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)


@asynccontextmanager
async def recorded(transport, frames: list[dict]):
    async with transport as (read, write):
        yield read, Tap(write, frames)


async def by_hand() -> None:
    print("== by hand, over stdio ==")
    frames: list[dict] = []
    async with Client(recorded(stdio_client(params), frames), elicitation_callback=answering("decline")) as client:
        first = await client.session.call_tool("terminate_process", PID, allow_input_required=True)
        wire = first.model_dump(mode="json", by_alias=True, exclude_none=True)
        state = wire["requestState"]
        wire["requestState"] = shorten(state)
        print(f"first leg, {type(first).__name__} (requestState shortened; {len(state)} characters in full):")
        print(json.dumps(wire, indent=2))

        responses = {key: ElicitResult(action="accept", content={"confirm": False}) for key in first.input_requests}
        second = await client.session.call_tool(
            "terminate_process", PID,
            allow_input_required=True,
            input_responses=responses,
            request_state=first.request_state,
        )

        tampered = state[:-2] + ("A" if state[-2] != "A" else "B") + state[-1]
        refusals = []
        for label, arguments, request_state in [
            ("requestState with one character changed", PID, tampered),
            ("the same requestState with other arguments", {"pid": 4242}, state),
        ]:
            try:
                await client.session.call_tool(
                    "terminate_process", arguments,
                    allow_input_required=True,
                    input_responses=responses,
                    request_state=request_state,
                )
                refusals.append(f"{label}: accepted")
            except Exception as exc:  # noqa: BLE001 - the point is to see what arrives
                refusals.append(f"{label}: {exc!r}")
    calls = [f for f in frames if f.get("method") == "tools/call"]
    retry = {k: v for k, v in calls[1]["params"].items() if k != "_meta"}
    echoed = retry["requestState"] == state
    retry["requestState"] = shorten(retry["requestState"])
    print("\nthe retry's params (requestState shortened, _meta left out):")
    print(json.dumps(retry, indent=2))
    print(f"\nJSON-RPC ids of the two legs: {calls[0]['id']} and {calls[1]['id']}; "
          f"requestState echoed byte for byte: {echoed}")
    print("\nsecond leg:")
    print(json.dumps(second.model_dump(mode="json", by_alias=True, exclude_none=True), indent=2))
    print("\nretries the server refuses:")
    for line in refusals:
        print(line)


async def automatic() -> None:
    print("\n== the SDK drives the loop, given a callback ==")
    print(f"Client.input_required_max_rounds defaults to {Client(mcp).input_required_max_rounds}")
    for label, callback in [
        ("accept, confirm false", answering("accept", {"confirm": False})),
        ("decline", answering("decline")),
        ("cancel", answering("cancel")),
    ]:
        async with Client(mcp, elicitation_callback=callback) as client:
            result = await client.call_tool("terminate_process", PID)
        print(f"{label:<22} isError={json.dumps(result.is_error)}  {result.content[0].text}")


async def no_callback() -> None:
    print("\n== no elicitation callback ==")
    async with Client(mcp) as client:
        try:
            await client.call_tool("terminate_process", PID)
        except Exception as exc:  # noqa: BLE001 - the point is to see what arrives
            print(repr(exc))
    async with open_connection(ServerSpec.memory("system-info", mcp)) as conn:
        outcome = await conn.call_tool("terminate_process", PID)
    print(f"through open_connection: ok={outcome.ok}")


async def manual_helper() -> None:
    print("\n== call_with_input, counting the questions ==")
    responder = ScriptedResponder("decline")
    async with open_connection(ServerSpec.memory("system-info", mcp), elicitation_callback=answering("decline")) as conn:
        result = await call_with_input(conn, "terminate_process", PID, responder)
    print(f"questions asked: {len(responder.seen)}; result: {result.content[0].text}")


class Go(BaseModel):
    go: bool


reworded = MCPServer("reworded")
counter = itertools.count(1)


def _ask_with_a_counter() -> Elicit[Go]:
    # The bug: the question text changes every time it is rendered.
    return Elicit(f"Go ahead? (asked {next(counter)} times so far)", Go)


@reworded.tool()
def risky(answer: Annotated[ElicitationResult[Go], Resolve(_ask_with_a_counter)]) -> str:
    """Do something after asking."""
    return "done"


async def rewording_server() -> None:
    print("\n== a server that rewords its question every round ==")
    seen: list[str] = []
    callback = as_elicitation_callback(ScriptedResponder("accept", {"go": True}, seen=seen))
    async with Client(reworded, elicitation_callback=callback, input_required_max_rounds=3) as client:
        try:
            result = await client.call_tool("risky", {})
            print(f"returned: {result.content[0].text}")
        except Exception as exc:  # noqa: BLE001
            print(f"{type(exc).__name__}: {exc}")
    print(f"questions the callback answered: {len(seen)}")


async def main() -> None:
    await by_hand()
    await automatic()
    await no_callback()
    await manual_helper()
    await rewording_server()


asyncio.run(main())
