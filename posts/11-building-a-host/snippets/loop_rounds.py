"""Post 11, sections 2 and 4: the tool loop, one round at a time.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/loop_rounds.py

Drives the ToolLoop of code/10-mcp-client/ against the server under
code/05-first-server/, opened in memory as that project's tests open it: no
subprocess, no socket, the real protocol path. The model is ScriptedProvider,
which follows a written plan, so no provider key and no network are needed.
Part one asks for three calls in one reply (one the gate approves, one it
denies, one that names no tool) and prints what comes back and the turn the
loop appends to the history. Part two hits the round cap. Part three asks for
two two-second watch_cpu calls in one reply and times the round.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))
sys.path.insert(0, str(ROOT / "code" / "05-first-server" / "src"))

from mcp_host.catalog import open_pool  # noqa: E402
from mcp_host.cli import on_event  # noqa: E402
from mcp_host.connection import ServerSpec  # noqa: E402
from mcp_host.loop import ToolLoop  # noqa: E402
from mcp_host.permissions import Decision, PermissionGate, always_allow, scripted_prompter  # noqa: E402
from mcp_host.providers import ScriptedProvider  # noqa: E402

from system_info import mcp as system_info_server  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed
SPECS = [ServerSpec.memory("system-info", system_info_server)]


async def mixed_reply() -> None:
    print("== one reply, three calls: approved, denied, unknown ==")
    plan = [
        [("get_system_info", {}), ("terminate_process", {"pid": 1}), ("teleport", {})],
        "done",
    ]
    async with open_pool(SPECS) as pool:
        gate = PermissionGate(scripted_prompter([Decision.DENY]))
        loop = ToolLoop(pool, gate, ScriptedProvider(plan), on_event=on_event)
        turn = await loop.run("do three things")

        for result in turn.results:
            shown = result.text if result.is_error else "(the snapshot, as JSON text)"
            print(f"{result.call.id}  {result.call.name:<18} is_error={result.is_error}")
            print(f"          {shown}")
        fed_back = loop.messages[2]
        ids = [block["tool_use_id"] for block in fed_back["content"]]
        print(f"messages in the history: {len(loop.messages)}; the third is role {fed_back['role']!r}"
              f" with {len(ids)} tool_result blocks: {ids}")
        print(f"rounds: {turn.rounds}; final text: {turn.text!r}")


async def round_cap() -> None:
    print("\n== a model that never stops asking, max_rounds=3 ==")
    plan = [[("get_system_info", {})]] * 10
    async with open_pool(SPECS) as pool:
        loop = ToolLoop(pool, PermissionGate(always_allow()), ScriptedProvider(plan), max_rounds=3)
        turn = await loop.run("loop forever")
        print(f"stopped_early={turn.stopped_early} rounds={turn.rounds} results={len(turn.results)}")
        print(f"text: {turn.text}")
    print(f"ToolLoop's default max_rounds: {ToolLoop.__init__.__kwdefaults__['max_rounds']}")


async def parallel_round() -> None:
    print("\n== two watch_cpu calls of 2 seconds each, in one reply ==")
    plan = [[("watch_cpu", {"seconds": 2}), ("watch_cpu", {"seconds": 2})], "done"]
    async with open_pool(SPECS) as pool:
        loop = ToolLoop(pool, PermissionGate(always_allow()), ScriptedProvider(plan))
        start = time.perf_counter()
        turn = await loop.run("watch twice")
        elapsed = time.perf_counter() - start
        print(f"results: {len(turn.results)}, errors: {sum(r.is_error for r in turn.results)}")
        print(f"wall clock for the round: {elapsed:.1f} s (one call alone takes 2 s)")


async def main() -> None:
    await mixed_reply()
    await round_cap()
    await parallel_round()


asyncio.run(main())
