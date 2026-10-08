"""Post 10, sections 3 and 6: what the client writes, and the three shapes that come back.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/wire_frames.py

Part one connects the SDK's Client to the server under code/05-first-server/
over stdio, through a transport wrapper that records every frame the client
writes, and prints those frames exactly as the stdio transport serializes
them. Part two starts the same server again and talks to it with no SDK at
all, one JSON line per request, to print the three shapes a tools/call can
come back in. Part three sends three calls at once on one client and checks
that each answer went to the call that asked for it. Every subprocess is
stopped before the script exits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

from mcp import Client, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from mcp_host.interactive import ScriptedResponder, as_elicitation_callback  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed
QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}
params = StdioServerParameters(command=sys.executable, args=["-m", "system_info"], cwd=str(SRC), env=QUIET)


class RecordingStream:
    """Wraps the client's write stream and keeps each frame it sends."""

    def __init__(self, stream, frames: list[str]) -> None:
        self._stream = stream
        self._frames = frames

    async def send(self, message) -> None:
        # The same serialization the stdio transport writes, one line per frame.
        self._frames.append(message.message.model_dump_json(by_alias=True, exclude_unset=True))
        await self._stream.send(message)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._stream.__aexit__(*exc)


@asynccontextmanager
async def recorded(transport, frames: list[str]):
    async with transport as (read, write):
        yield read, RecordingStream(write, frames)


async def what_the_client_writes() -> None:
    print("== what the client wrote to the server's standard input ==")
    frames: list[str] = []
    callback = as_elicitation_callback(ScriptedResponder("decline"))
    async with Client(recorded(stdio_client(params), frames), elicitation_callback=callback) as client:
        await client.call_tool("get_system_info", {})
    for frame in frames:
        print(frame)


def three_shapes() -> None:
    print("\n== three tools/call requests, by hand, no SDK ==")
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    requests = [
        {"name": "find_process", "arguments": {"name": "zzz-no-such-process"}},
        {"name": "get_sysinfo", "arguments": {}},
        {"name": "find_process", "arguments": "not-an-object"},
    ]
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info"],
        cwd=str(SRC),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={**__import__("os").environ, **QUIET},
        text=True,
        encoding="utf-8",
    )
    try:
        replies = {}
        for number, call in enumerate(requests, start=1):
            frame = {"jsonrpc": "2.0", "id": number, "method": "tools/call", "params": {**call, "_meta": meta}}
            server.stdin.write(json.dumps(frame) + "\n")
            server.stdin.flush()
            reply = json.loads(server.stdout.readline())
            replies[reply["id"]] = reply
        for number in sorted(replies):
            print(f"-> {json.dumps(requests[number - 1])}")
            print(f"<- {json.dumps(replies[number], separators=(',', ':'))}")
    finally:
        server.stdin.close()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


async def concurrent_calls() -> None:
    print("\n== three calls at once on one client ==")
    frames: list[str] = []
    names = ["zzz-a", "zzz-b", "zzz-c"]
    async with Client(recorded(stdio_client(params), frames)) as client:
        results = await asyncio.gather(
            *(client.call_tool("find_process", {"name": name}) for name in names)
        )
    ids = [json.loads(f)["id"] for f in frames if json.loads(f).get("method") == "tools/call"]
    answered = [r.structured_content["query"] for r in results]
    print(f"request ids on the wire: {ids}")
    print(f"each result's query, in the order gather returned them: {answered}")
    print(f"every answer matched its call: {answered == names}")


async def main() -> None:
    await what_the_client_writes()
    three_shapes()
    await concurrent_calls()


asyncio.run(main())
