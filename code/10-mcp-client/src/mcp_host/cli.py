"""The command line.

Post 10 gives it `list`, `call` and `inspect`; post 11 adds `chat` and `demo`.

Two rules run through the whole file.

**ASCII only.** Not a style preference. A Windows console under cp1252 raises
`UnicodeEncodeError` on a checkmark or an arrow, and it does it from inside
`print`, so the traceback points at your output code rather than at the glyph
you pasted in. `[ok]` and `->` render everywhere.

**Never `input()` in a coroutine.** Every prompt goes through
`asyncio.to_thread`. See interactive.py for why.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
from typing import Any, Sequence

from .catalog import ToolCatalog, open_pool
from .connection import ServerSpec, load_config
from .interactive import ConsoleResponder, as_elicitation_callback
from .loop import ToolLoop
from .permissions import PermissionGate, PermissionPolicy, console_prompter
from .providers import (
    AnthropicProvider,
    ProviderUnavailable,
    ScriptedProvider,
    get_provider,
)

DEFAULT_CONFIG = "servers.json"
EXAMPLE_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "servers.example.json"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-host",
        description="An MCP client and host (posts 10 and 11).",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help=f"servers file (default: ./{DEFAULT_CONFIG}, then the bundled example)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="connect to every server and list the catalog")

    inspect = sub.add_parser("inspect", help="show one server's full surface")
    inspect.add_argument("server", help="server name from the config")

    call = sub.add_parser("call", help="call one tool through the permission gate")
    call.add_argument("tool", help="tool name, or server.tool")
    call.add_argument("--args", default="{}", help="arguments as a JSON object")
    call.add_argument(
        "--yes", action="store_true", help="approve without prompting (careful)"
    )

    chat = sub.add_parser("chat", help="run the tool loop against a provider")
    chat.add_argument(
        "--provider",
        default=os.environ.get("MCP_HOST_PROVIDER", "scripted"),
        help="provider name (env: MCP_HOST_PROVIDER, default: scripted)",
    )
    chat.add_argument("--max-rounds", type=int, default=8)

    sub.add_parser(
        "demo", help="run the tool loop with the scripted provider (no API key)"
    )
    return parser


def resolve_config(path: str | None) -> list[ServerSpec]:
    if path:
        return load_config(path)
    local = pathlib.Path(DEFAULT_CONFIG)
    if local.exists():
        return load_config(local)
    if EXAMPLE_CONFIG.exists():
        print(f"[info] no {DEFAULT_CONFIG}; using {EXAMPLE_CONFIG.name}")
        return load_config(EXAMPLE_CONFIG)
    raise SystemExit(
        f"no server config found. Write a {DEFAULT_CONFIG} or pass --config."
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def print_catalog(catalog: ToolCatalog) -> None:
    if not len(catalog):
        print("no tools")
        return
    for server in catalog.servers():
        print(f"\n{server}")
        for entry in catalog.for_server(server):
            marks = []
            ann = entry.annotations
            if getattr(ann, "read_only_hint", None):
                marks.append("read-only")
            if getattr(ann, "destructive_hint", None):
                marks.append("DESTRUCTIVE")
            suffix = f"  [{', '.join(marks)}]" if marks else "  [unannotated]"
            first_line = entry.description.strip().splitlines()[0] if entry.description else ""
            print(f"  {entry.name}{suffix}")
            if first_line:
                print(f"      {first_line}")
            if entry.name != entry.tool_name:
                print(f"      (namespaced: another server also exports "
                      f"{entry.tool_name!r})")


def on_event(kind: str, data: dict[str, Any]) -> None:
    """Print what the loop is doing. ASCII only."""
    if kind == "round":
        print(f"  [round {data['index']}] model asked for {data['calls']} tool call(s)")
    elif kind == "gate":
        verdict = "allowed" if data["allowed"] else "DENIED"
        print(f"    [{verdict}] {data['tool']} -- {data['reason']}")
    elif kind == "execute":
        print(f"    running: {', '.join(data['tools'])}")
    elif kind == "unknown":
        print(f"    [unknown tool] {data['tool']}")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


async def cmd_list(specs: Sequence[ServerSpec]) -> int:
    async with open_pool(specs, elicitation_callback=_responder()) as pool:
        for name, conn in pool.connections.items():
            info = conn.server_info
            print(
                f"[ok] {name}: {getattr(info, 'name', '?')} "
                f"{getattr(info, 'version', '?')} "
                f"(protocol {conn.protocol_version})"
            )
        print_catalog(pool.catalog)
    return 0


async def cmd_inspect(specs: Sequence[ServerSpec], server: str) -> int:
    chosen = [s for s in specs if s.name == server]
    if not chosen:
        print(f"no server named {server!r} in the config")
        return 1
    async with open_pool(chosen, elicitation_callback=_responder()) as pool:
        conn = pool.connections[server]
        print_catalog(pool.catalog)
        resources = await conn.list_resources()
        if resources:
            print("\nresources")
            for res in resources:
                print(f"  {res.uri}  {res.name}")
        prompts = await conn.list_prompts()
        if prompts:
            print("\nprompts")
            for prompt in prompts:
                print(f"  {prompt.name}")
    return 0


async def cmd_call(
    specs: Sequence[ServerSpec], tool: str, raw_args: str, auto_yes: bool
) -> int:
    try:
        arguments = json.loads(raw_args)
    except ValueError as exc:
        print(f"--args is not valid JSON: {exc}")
        return 2
    if not isinstance(arguments, dict):
        print("--args must be a JSON object")
        return 2

    gate = _gate(auto_yes)
    async with open_pool(specs, elicitation_callback=_responder()) as pool:
        from .permissions import request_for

        try:
            entry = pool.catalog.resolve(tool)
        except KeyError:
            print(f"no such tool: {tool}")
            print(f"known: {', '.join(sorted(pool.catalog.names()))}")
            return 1

        verdict = await gate.check(request_for(entry, arguments))
        if not verdict.allowed:
            print(f"[denied] {verdict.reason}")
            return 1

        outcome = await pool.call(tool, arguments)
        if not outcome.ok:
            print(f"[failed] {outcome.for_model()}")
            return 1
        print(f"[ok] {tool}")
        for block in outcome.blocks:
            print(f"  ({block.kind}) {block.text}")
        if outcome.structured is not None:
            print(f"  structured: {json.dumps(outcome.structured, indent=2)}")
    return 0


async def cmd_chat(
    specs: Sequence[ServerSpec], provider_name: str, max_rounds: int
) -> int:
    try:
        provider = get_provider(provider_name)
    except ProviderUnavailable as exc:
        print(f"[error] provider {provider_name!r} unavailable: {exc}")
        if provider_name != "scripted" and not AnthropicProvider.available():
            print("[info] try 'mcp-host demo' -- it needs no key and no network.")
        return 2

    gate = _gate(False)
    async with open_pool(specs, elicitation_callback=_responder()) as pool:
        loop = ToolLoop(
            pool, gate, provider, max_rounds=max_rounds, on_event=on_event
        )
        print(f"\nconnected: {len(pool.catalog)} tools, provider {provider.name}")
        print("type 'quit' to exit.\n")
        while True:
            try:
                text = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text.lower() in {"quit", "exit", "q"}:
                break
            turn = await loop.run(text)
            print(f"\nassistant> {turn.text}\n")
    return 0


async def cmd_demo(specs: Sequence[ServerSpec]) -> int:
    """The tool loop, end to end, with no API key and no network.

    The plan is built from whatever the connected servers actually publish, so
    the demo works against any server rather than only the one in the series.
    """
    gate = _gate(False)
    async with open_pool(specs, elicitation_callback=_responder()) as pool:
        argument_free = [
            entry.name
            for entry in pool.catalog
            if not (entry.tool.input_schema or {}).get("required")
            and getattr(entry.annotations, "read_only_hint", None)
        ][:2]
        if not argument_free:
            print("no read-only, argument-free tool to demonstrate with")
            return 1

        plan = [
            [(name, {}) for name in argument_free],
            "Here is what the tools reported.",
        ]
        provider = ScriptedProvider(plan)
        loop = ToolLoop(pool, gate, provider, on_event=on_event)

        print(f"\ndemo: the model will ask for {', '.join(argument_free)}")
        turn = await loop.run("What can you tell me about this machine?")
        print(f"\nassistant> {turn.text}")
        for result in turn.results:
            status = "failed" if result.is_error else "ok"
            print(f"\n[{status}] {result.call.name}\n{result.text}")
    return 0


def _responder() -> Any:
    """The elicitation callback every command connects with.

    Passing one is what declares the capability. Connect without it and a
    server whose tool needs an answer fails the call with -32021 rather than
    asking.
    """
    return as_elicitation_callback(ConsoleResponder())


def _gate(auto_yes: bool) -> PermissionGate:
    if auto_yes:
        # Documented escape hatch for scripting, and the reason it is a flag
        # rather than the default: the gate is the point of the host.
        from .permissions import always_allow

        return PermissionGate(always_allow(), PermissionPolicy())
    return PermissionGate(console_prompter(), PermissionPolicy())


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    specs = resolve_config(args.config)

    if args.command == "list":
        coro = cmd_list(specs)
    elif args.command == "inspect":
        coro = cmd_inspect(specs, args.server)
    elif args.command == "call":
        coro = cmd_call(specs, args.tool, args.args, args.yes)
    elif args.command == "chat":
        coro = cmd_chat(specs, args.provider, args.max_rounds)
    else:
        coro = cmd_demo(specs)

    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
