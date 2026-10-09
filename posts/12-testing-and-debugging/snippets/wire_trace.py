"""Post 12, sections 3 and 7: a wire trace of the first server, and the two places a failure hides.

Run from the series root:
    uv run python posts/12-testing-and-debugging/snippets/wire_trace.py

Starts the server under code/05-first-server/ over Streamable HTTP on a free
loopback port, as a subprocess, and stops it before exiting. Part one lets the
SDK's own Client make two tools/call requests through an HTTP client that
records what crosses the wire: one well formed call that finds nothing, and
one whose `limit` is a string. It prints the request headers the SDK sent, the
request body, and each response body. Part two posts five hand-written
requests with httpx, each wrong in a different way, and prints the HTTP status
and the JSON-RPC error that comes back.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import httpx2

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"

from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

SHOWN_HEADERS = ("accept", "content-type", "mcp-protocol-version", "mcp-method", "mcp-name")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> subprocess.Popen:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", "system_info", "--http", "--port", str(port)],
        cwd=SRC, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


def recording_client(log: list) -> httpx2.AsyncClient:
    """An HTTP client that keeps every POST it makes, with the response to it."""

    async def on_response(response):
        if response.request.method == "POST":
            await response.aread()
            log.append(response)

    return httpx2.AsyncClient(event_hooks={"response": [on_response]})


async def sdk_calls(url: str) -> None:
    log: list = []
    async with recording_client(log) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            await client.call_tool("find_process", {"name": "zzz-no-such-process", "limit": 1})
            await client.call_tool("find_process", {"name": "python", "limit": "not-a-number"})

    order = [(json.loads(r.request.content).get("method"), json.loads(r.request.content).get("id"))
             for r in log]
    print(f"POSTs in order, as (method, id): {order}")
    calls = [r for r in log if json.loads(r.request.content).get("method") == "tools/call"]
    for n, response in enumerate(calls):
        request = response.request
        print("\n== request the SDK sent ==" if n == 0 else "\n== second request, same headers but Mcp-Name ==")
        if n == 0:
            for raw_name, raw_value in request.headers.raw:  # names exactly as sent
                if raw_name.decode().lower() in SHOWN_HEADERS:
                    print(f"{raw_name.decode()}: {raw_value.decode()}")
        print(json.dumps(json.loads(request.content), indent=2))
        print(f"-- HTTP {response.status_code}, content-type {response.headers.get('content-type')}")
        print(json.dumps(json.loads(response.content), indent=2))


def by_hand(url: str) -> None:
    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
    }
    cases = [
        ("_meta revision differs from the header",
         {**headers, "Mcp-Method": "tools/list"},
         {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
          "params": {"_meta": {**meta, "io.modelcontextprotocol/protocolVersion": "2025-11-25"}}}),
        ("header names an older revision than _meta",
         {**headers, "MCP-Protocol-Version": "2025-11-25", "Mcp-Method": "tools/list"},
         {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}}),
        ("Mcp-Name differs from params.name",
         {**headers, "Mcp-Method": "tools/call", "Mcp-Name": "get_system_info"},
         {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": "find_process", "arguments": {"name": "zzz"}, "_meta": meta}}),
        ("no _meta at all",
         {**headers, "Mcp-Method": "tools/list"},
         {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}),
        ("a method the server does not have",
         {**headers, "Mcp-Method": "tools/run"},
         {"jsonrpc": "2.0", "id": 4, "method": "tools/run", "params": {"_meta": meta}}),
    ]
    print("\n== five requests written by hand, each wrong once ==")
    with httpx.Client(timeout=10) as http:
        for label, h, body in cases:
            r = http.post(url, headers=h, json=body)
            payload = r.json()
            print(f"--- {label}: HTTP {r.status_code}, result member present: {'result' in payload}")
            print(json.dumps(payload.get("error", payload)))


def main() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    proc = start_server(port)
    try:
        asyncio.run(sdk_calls(url))
        by_hand(url)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


main()
