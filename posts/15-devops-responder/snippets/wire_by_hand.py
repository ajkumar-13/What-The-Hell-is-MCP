"""Post 15, section 2: one tools/call to the first responder, by hand, over stdio.

Run from the series root:
    uv run python posts/15-devops-responder/snippets/wire_by_hand.py

The script starts itself again with `--serve`, as a stdio server that has the fake cluster of the
project's tests/conftest.py installed in place of a real one, writes one
JSON-RPC request line to that server's standard input, and reads one line back.
No SDK client is involved on this side, and no cluster anywhere. Closing
standard input ends the server.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "15-devops-responder"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "list_pods",
        "arguments": {"namespace": "default"},
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "ExampleClient", "version": "1.0.0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    },
}


def serve() -> None:
    """The server half: the real package, with the test fixture as its cluster."""
    from conftest import populated
    from k8s_responder import cluster, mcp

    state = populated()
    cluster.use(cluster.Connection(core_v1=state.core, apps_v1=state.apps, describe="fake"))
    mcp.run()


def main() -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    server = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
    )
    try:
        line = json.dumps(REQUEST)
        server.stdin.write(line + "\n")
        server.stdin.flush()
        reply = server.stdout.readline()
        server.stdin.close()
        code = server.wait(timeout=30)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()

    print("written to stdin:")
    print(line)
    print()

    frame = json.loads(reply)
    result = frame["result"]
    print("read from stdout: a result with keys", sorted(result))
    print("isError:", json.dumps(result["isError"]), " resultType:", json.dumps(result["resultType"]))
    print("_meta:", json.dumps(result["_meta"]))
    block = result["content"][0]
    same = json.loads(block["text"]) == result["structuredContent"]
    print(f"content: {len(result['content'])} block of type {block['type']!r}, "
          f"its text is the structuredContent as JSON: {same}")
    print()

    data = dict(result["structuredContent"])
    shown = data["pods"][:2]
    print(f"structuredContent, {len(data['pods'])} pods, the first two shown:")
    data["pods"] = shown
    print(json.dumps(data, indent=2))
    print()
    print("every pod: " + ", ".join(
        f"{p['name']} {p['phase']} {p['ready']} restarts={p['restarts']}"
        for p in result["structuredContent"]["pods"]))
    print("server exit code after stdin closed:", code)


if __name__ == "__main__":
    if "--serve" in sys.argv[1:]:
        serve()
    else:
        main()
