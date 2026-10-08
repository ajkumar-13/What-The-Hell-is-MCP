"""Post 05, section 7: the finished server over Streamable HTTP, read with no SDK.

Run from the series root:
    uv run python posts/05-first-server/snippets/http_discover.py

Starts `python -m system_info --http` from code/05-first-server/src on
127.0.0.1 and a free port, runs post 03's raw_discover.py against it (the
client that speaks the protocol with nothing but an HTTP library), sends one
tools/list with an empty params object and one request for a method that does
not exist, prints the status line and body of each, and stops the server.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"
RAW_DISCOVER = ROOT / "posts" / "03-wire-protocol" / "snippets" / "raw_discover.py"

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "hand", "version": "1.0.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


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


def headers(method: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }


def main() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info", "--http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        wait_for(port, server)

        print(f"== raw_discover.py against the server on a free port (shown here as {port}) ==")
        if RAW_DISCOVER.exists():
            run = subprocess.run(
                [sys.executable, str(RAW_DISCOVER), url],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
            print(run.stdout.rstrip() or f"(no output; exit {run.returncode}) {run.stderr.strip()[-300:]}")
        else:
            print(f"(not found: {RAW_DISCOVER.relative_to(ROOT)})")

        with httpx.Client(timeout=30.0) as client:
            print("\n== tools/list with params {} ==")
            r = client.post(url, json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
                            headers=headers("tools/list"))
            print(f"HTTP {r.status_code} {r.reason_phrase}")
            print(r.text)

            print("\n== tools/frobnicate, a method the server does not implement ==")
            r = client.post(url, json={"jsonrpc": "2.0", "id": 7, "method": "tools/frobnicate",
                                       "params": {"_meta": META}},
                            headers=headers("tools/frobnicate"))
            print(f"HTTP {r.status_code} {r.reason_phrase}")
            print(r.text)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    print("\nserver stopped")


if __name__ == "__main__":
    main()
