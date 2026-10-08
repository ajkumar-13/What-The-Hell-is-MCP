"""Post 05, section 7: drive the first server by hand over standard input and output.

Run from the series root:
    uv run python posts/05-first-server/snippets/stdio_by_hand.py

Starts `python -m system_info` from code/05-first-server/src as a child process,
exactly the command a desktop host runs, then writes one JSON-RPC request per
line to its standard input and reads one line back from its standard output for
each. No SDK client is involved on this side: the requests are dictionaries and
the transport is two pipes. Closing standard input ends the server, and the
script reports its exit code and what it wrote to standard error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "code" / "05-first-server" / "src"

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "hand", "version": "1.0.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
WITHOUT_CLIENT_INFO = {k: v for k, v in META.items() if not k.endswith("/clientInfo")}

EXCHANGES = [
    ("a tool call",
     {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
      "params": {"name": "get_system_info", "arguments": {}, "_meta": META}}),
    ("a method the server does not implement",
     {"jsonrpc": "2.0", "id": 2, "method": "tools/frobnicate", "params": {"_meta": META}}),
    ("a request with no _meta at all",
     {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}),
    ("a request whose _meta has the two required keys but no clientInfo",
     {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {"_meta": WITHOUT_CLIENT_INFO}}),
    ("a misspelled tool name",
     {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
      "params": {"name": "get_sysinfo", "arguments": {}, "_meta": META}}),
]


def main() -> None:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        for label, request in EXCHANGES:
            line = json.dumps(request)
            server.stdin.write(line + "\n")
            server.stdin.flush()
            print(f"== {label} ==")
            print("written to stdin:")
            print(line)
            print("read from stdout:")
            print(server.stdout.readline().rstrip("\n"))
            print()

        # A stdio server should exit when its input closes; nothing else stops it.
        server.stdin.close()
        code = server.wait(timeout=30)
        leftover = server.stdout.read()
        stderr_lines = server.stderr.read().splitlines()
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()

    print(f"server exited with code {code} after standard input closed")
    print(f"stdout lines beyond the {len(EXCHANGES)} responses: {len(leftover.splitlines())}")
    print(f"stderr lines: {len(stderr_lines)}")
    for text in stderr_lines:
        print(f"    {text}")


if __name__ == "__main__":
    main()
