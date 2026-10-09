"""Post 12, section 3: one Client class, three targets, chosen by the argument's type.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/targets.py

Connects the SDK's Client to the server under code/05-first-server/ three
ways: the server object in memory, the same server launched as a subprocess
over stdio, and the same server started here over Streamable HTTP on a free
loopback port. For each it prints the server's name, the revision in force and
the tools it lists, then stops every process it started.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
sys.path.insert(0, str(SRC))

from mcp import Client, StdioServerParameters  # noqa: E402

from system_info import mcp  # noqa: E402

QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("the HTTP server did not start")


async def describe(label: str, client: Client) -> None:
    async with client as c:
        names = sorted(t.name for t in (await c.list_tools()).tools)
        print(f"{label:<10} server={c.server_info.name!r} revision={c.protocol_version} tools={names}")


async def main(url: str) -> None:
    targets = [
        ("in memory", Client(mcp)),                                     # the server object, level 2
        ("stdio", Client(StdioServerParameters(                         # a subprocess, level 3
            command=sys.executable, args=["-m", "system_info"], cwd=str(SRC), env=QUIET))),
        ("HTTP", Client(url)),                                          # a running HTTP server
    ]
    for label, client in targets:
        await describe(label, client)


port = free_port()
server = subprocess.Popen(
    [sys.executable, "-m", "system_info", "--http", "--port", str(port)],
    cwd=SRC, env=dict(os.environ, **QUIET), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    wait_for(port)
    asyncio.run(main(f"http://127.0.0.1:{port}/mcp"))
finally:
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
