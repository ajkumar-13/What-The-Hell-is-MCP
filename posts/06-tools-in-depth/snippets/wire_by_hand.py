"""Post 06, sections 4, 7 and 10: the first server's tool traffic, read off the wire.

Run from the series root:
    uv run python posts/06-tools-in-depth/snippets/wire_by_hand.py

Starts `python -m system_info` from code/05-first-server/src as a child process,
the command a desktop host runs, writes one JSON-RPC request per line to its
standard input and reads one line back from its standard output for each. No
SDK client is involved on this side. Prints two tools/list results compared
byte for byte, their order and caching fields, one find_process result, and the
answers to an unknown tool, an unknown method, an argument of the wrong type
and an arguments value that is not an object. Closing standard input ends the
server; the script waits for it to exit.
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


def request(rid: int, method: str, **params) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": {**params, "_meta": META}}


def call(rid: int, name: str, arguments) -> dict:
    return request(rid, "tools/call", name=name, arguments=arguments)


def main() -> None:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONDONTWRITEBYTECODE="1")
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        encoding="utf-8",
    )

    def exchange(message: dict) -> str:
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()
        return server.stdout.readline().rstrip("\n")

    try:
        first = exchange(request(1, "tools/list"))
        second = exchange(request(2, "tools/list"))
        body = lambda line: line.split('"result":', 1)[1]  # noqa: E731 - everything after the id
        listing = json.loads(first)["result"]
        print("== tools/list, twice ==")
        print(f"results byte-identical after the id: {body(first) == body(second)}")
        print(f"order: {[t['name'] for t in listing['tools']]}")
        print(f"ttlMs: {listing['ttlMs']}   cacheScope: {listing['cacheScope']}")

        found = json.loads(exchange(call(3, "find_process", {"name": "python", "limit": 1})))["result"]
        text = found["content"][0]["text"]
        found["content"][0]["text"] = text[:40] + "..."
        print("\n== tools/call find_process {\"name\": \"python\", \"limit\": 1}, text block shortened ==")
        print(json.dumps(found, indent=2))
        print(f"text block equals the serialized structuredContent: "
              f"{json.loads(text) == found['structuredContent']}")
        print(f"result carries ttlMs or cacheScope: {'ttlMs' in found or 'cacheScope' in found}")

        cases = [
            ("a misspelled tool name", call(4, "find_procss", {"name": "python"})),
            ("a method the server does not implement", request(5, "tools/nonexistent")),
            ("an argument of the wrong JSON type", call(6, "find_process", {"name": "python", "limit": "many"})),
            ("arguments that are not an object", call(7, "find_process", ["python"])),
        ]
        for label, message in cases:
            print(f"\n== {label} ==")
            print(exchange(message))

        server.stdin.close()
        print(f"\nserver exit code: {server.wait(timeout=30)}")
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()


main()
