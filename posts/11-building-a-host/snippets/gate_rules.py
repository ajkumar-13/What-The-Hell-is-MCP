"""Post 11, section 6: the permission gate's rules, case by case.

Run from the series root:
    uv run python posts/11-building-a-host/snippets/gate_rules.py

Drives the PermissionGate of code/10-mcp-client/ directly, with no server
and no model: each case builds a PermissionRequest, answers any prompt from
a scripted list, and prints the verdict, its reason, and how many times a
human was asked. The last case runs the console prompter with standard input
already at end of file, as an unattended host would.
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp_types import ToolAnnotations  # noqa: E402

from mcp_host.permissions import (  # noqa: E402
    Decision,
    PermissionGate,
    PermissionPolicy,
    PermissionRequest,
    console_prompter,
    scripted_prompter,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)
LIAR = ToolAnnotations(read_only_hint=True, destructive_hint=True)
SILENT_ON_DESTRUCTIVE = ToolAnnotations(read_only_hint=False)


def request(tool="do_thing", server="demo", annotations=None):
    return PermissionRequest(tool=tool, server=server, annotations=annotations)


async def case(label, answers, requests, policy=None):
    asked: list[PermissionRequest] = []
    gate = PermissionGate(scripted_prompter(answers, asked=asked), policy)
    verdicts = [await gate.check(r) for r in requests]
    shown = "; ".join(f"{'allow' if v.allowed else 'deny'} ({v.reason})" for v in verdicts)
    print(f"{label}\n    {shown}\n    prompts shown: {len(asked)}; remembered: {gate.remembered}")


async def main() -> None:
    print("== annotations, under the default policy and with it changed ==")
    await case("read-only, default policy, prompter would deny", [Decision.DENY], [request(annotations=READ_ONLY)])
    await case("read-only, auto_approve_read_only=False", [Decision.DENY], [request(annotations=READ_ONLY)],
               PermissionPolicy(auto_approve_read_only=False))
    await case("claims read-only AND destructive", [Decision.DENY], [request(tool="rm_rf", annotations=LIAR)])
    await case("no annotations at all", [Decision.ALLOW_ONCE], [request(annotations=None)])
    await case("readOnlyHint false, destructiveHint absent, 'always' twice",
               [Decision.ALLOW_ALWAYS] * 2, [request(annotations=SILENT_ON_DESTRUCTIVE)] * 2)
    print(f"    the gate reads that tool as destructive: {request(annotations=SILENT_ON_DESTRUCTIVE).destructive}")
    await case("no annotations at all, 'always' twice",
               [Decision.ALLOW_ALWAYS] * 2, [request(annotations=None)] * 2)
    print(f"    the gate reads that tool as destructive: {request(annotations=None).destructive}")

    print("\n== remembered decisions ==")
    await case("destructive, 'always' twice", [Decision.ALLOW_ALWAYS] * 2, [request(tool="rm", annotations=DESTRUCTIVE)] * 2)
    await case("destructive, 'never' once", [Decision.DENY_ALWAYS], [request(tool="rm", annotations=DESTRUCTIVE)] * 2)
    await case("unannotated, 'no' once", [Decision.DENY, Decision.ALLOW_ONCE], [request()] * 2)
    await case("'always' for search on docs, then search on files", [Decision.ALLOW_ALWAYS, Decision.DENY],
               [request(tool="search", server="docs"), request(tool="search", server="files")])

    print("\n== the user's own lists ==")
    await case("denylist demo.do_thing, read-only tool", [Decision.ALLOW_ONCE], [request(annotations=READ_ONLY)],
               PermissionPolicy(denylist=frozenset({"demo.do_thing"})))
    await case("on both lists", [Decision.ALLOW_ONCE], [request()],
               PermissionPolicy(allowlist=frozenset({"demo.do_thing"}), denylist=frozenset({"demo.do_thing"})))
    await case("allowlist demo.rm, destructive tool", [Decision.DENY], [request(tool="rm", annotations=DESTRUCTIVE)],
               PermissionPolicy(allowlist=frozenset({"demo.rm"})))
    await case("denylist bare 'rm', tool rm on server other", [Decision.ALLOW_ONCE], [request(tool="rm", server="other")],
               PermissionPolicy(denylist=frozenset({"rm"})))

    print("\n== the console prompter with nothing on standard input ==")
    real_stdin, sys.stdin = sys.stdin, io.StringIO("")
    try:
        verdict = await PermissionGate(console_prompter()).check(
            PermissionRequest(tool="terminate_process", server="system-info", arguments={"pid": 4242},
                              annotations=DESTRUCTIVE, description="Terminate a running process.")
        )
    finally:
        sys.stdin = real_stdin
    print(f"\nverdict: allowed={verdict.allowed} ({verdict.reason})")


asyncio.run(main())
