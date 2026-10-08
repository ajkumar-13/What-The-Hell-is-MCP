"""Post 10, sections 3, 6 and 11: three transports through one code path.

Run from the series root:
    uv run python posts/10-mcp-client/snippets/transports.py

Starts the server under code/05-first-server/ over Streamable HTTP on
127.0.0.1 and a free port, then opens it three ways through open_connection
from code/10-mcp-client/src/mcp_host/connection.py: in memory, as a stdio
subprocess, and over HTTP. For each it prints what discovery reported and
the tools it found, and for stdio whether any process the connection started
is still running after the block exits. Then it shows the shapes Client
accepts directly, the headers the SDK puts on a tools/call POST, what a
trailing slash on the endpoint path does, and what a "stdio://" string does.
Stops the HTTP server before it exits.
"""

from __future__ import annotations

import asyncio
import logging
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
sys.path.insert(0, str(ROOT / "code" / "10-mcp-client" / "src"))

import httpx  # noqa: E402
import httpx2  # noqa: E402
import psutil  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from mcp_host.connection import ServerSpec, open_connection  # noqa: E402
from system_info import mcp  # noqa: E402

logging.disable(logging.WARNING)  # keep the output to what is printed
QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(port: int, server: subprocess.Popen, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"server exited early with code {server.returncode}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("server did not start listening in time")


def tree(pid: int | None = None) -> list[psutil.Process]:
    # A process can exit while the tree is being walked; walk again if so.
    for _ in range(5):
        try:
            return psutil.Process(pid).children(recursive=True)
        except psutil.Error:
            continue
    return []


def descendants(exclude: set[int]) -> set[int]:
    return {p.pid for p in tree()} - exclude


def leaf(exc: BaseException) -> BaseException:
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return exc


async def main(url: str, http_tree: set[int]) -> None:
    specs = [
        ServerSpec.memory("system-info", mcp),
        ServerSpec.stdio("system-info", sys.executable, "-m", "system_info", cwd=str(SRC), env=QUIET),
        ServerSpec.http("system-info", url),
    ]
    print("== one code path, three transports ==")
    for spec in specs:
        async with open_connection(spec) as conn:
            info = conn.server_info
            tools = [t.name for t in await conn.list_tools()]
            started = descendants(http_tree)
            print(f"{spec.transport:<6} server {info.name!r} version {info.version!r}, "
                  f"protocol {conn.protocol_version}, tools: {', '.join(tools)}")
        if spec.transport == "stdio":
            alive = [pid for pid in started if psutil.pid_exists(pid)]
            print(f"       processes the stdio connection started: {len(started) > 0}; "
                  f"still running after the block: {len(alive)}")

    print("\n== StdioServerParameters handed to Client directly ==")
    params = StdioServerParameters(command=sys.executable, args=["-m", "system_info"], cwd=str(SRC), env=QUIET)
    async with Client(params) as client:
        print(f"connected: {client.server_info.name!r}, {len((await client.list_tools()).tools)} tools")

    print("\n== the headers on a tools/call POST ==")
    seen: list[httpx2.Request] = []

    async def record(request: httpx2.Request) -> None:
        seen.append(request)

    async with httpx2.AsyncClient(event_hooks={"request": [record]}, timeout=30.0) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client)) as client:
            await client.call_tool("get_system_info", {})
    for request in seen:
        if request.headers.get("mcp-method") == "tools/call":
            print(f"{request.method} {request.url.path} HTTP/1.1")
            for name in ("Content-Type", "Accept", "MCP-Protocol-Version", "Mcp-Method", "Mcp-Name"):
                print(f"{name}: {request.headers[name]}")
    print(f"POSTs in that connection, by Mcp-Method: {', '.join(r.headers['mcp-method'] for r in seen)}")

    print("\n== a tool that fails, over HTTP ==")
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}}
    r = httpx.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "find_process", "arguments": {"limit": "not-a-number"}, "_meta": meta}},
        headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                 "Mcp-Method": "tools/call", "Mcp-Name": "find_process"},
    )
    body = r.json()
    print(f"find_process with a bad argument: HTTP {r.status_code}, "
          f"isError {str(body['result']['isError']).lower()}, resultType {body['result']['resultType']!r}")

    print("\n== a trailing slash on the endpoint path ==")
    slashed = url + "/"
    r = httpx.post(
        slashed,
        json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}},
        headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                 "Mcp-Method": "server/discover"},
    )
    location = r.headers.get("location", "").replace(str(url.split(":")[2].split("/")[0]), "PORT")
    print(f"POST /mcp/ without following redirects: HTTP {r.status_code}, Location {location}, "
          f"body {len(r.content)} bytes")
    async with Client(slashed) as client:
        print(f"the SDK client on /mcp/: connected, {len((await client.list_tools()).tools)} tools")

    print('\n== Client("stdio://...") ==')
    try:
        async with Client("stdio://python -m system_info"):
            pass
    except BaseException as exc:  # noqa: BLE001 - the point is to see what arrives
        inner = leaf(exc)
        print(f"raised {type(exc).__name__}; inside it: {type(inner).__name__}: {inner}")


def run() -> None:
    port = free_port()
    env = dict(os.environ, PYTHONPATH=str(SRC), **QUIET)
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info", "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        wait_for(port, server)
        http_tree = {p.pid for p in tree()}
        asyncio.run(main(f"http://127.0.0.1:{port}/mcp", http_tree))
    finally:
        # On Windows a virtual environment's python.exe is a launcher with the
        # real interpreter as its child, so stop the whole tree.
        server_tree = tree(server.pid) if server.poll() is None else []
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        for child in server_tree:
            try:
                child.kill()
            except psutil.Error:
                pass
    print("\nHTTP server stopped")


run()
