"""Post 08, sections 4 and 5: one MRTR round trip on the wire, composed by hand.

Run from the series root:
    uv run python posts/08-elicitation-and-mrtr/snippets/mrtr_wire.py

Starts the first-server project (code/05-first-server/) as a Streamable HTTP
server on a free loopback port, then talks to it with nothing but httpx: a
tools/call of terminate_process, the input_required result it gets back, the
retry carrying the answer and the echoed requestState, and the complete result.
Then it sends three requests the server refuses (a retry with other arguments, a modified
requestState, a first call with no elicitation capability) and prints each refusal. The process
id is 999999, which does not exist, so nothing is ever terminated. The server
is stopped before the script exits.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
PROJECT_SRC = ROOT / "code" / "05-first-server" / "src"
REVISION = "2026-07-28"
PID = 999999


def meta(capabilities: dict) -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": REVISION,
        "io.modelcontextprotocol/clientInfo": {"name": "mrtr-by-hand", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": capabilities,
    }


def call(request_id: int, arguments: dict, capabilities: dict, **extra) -> dict:
    params = {"name": "terminate_process", "arguments": arguments, **extra, "_meta": meta(capabilities)}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": REVISION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "terminate_process",
    }


def short(state: str) -> str:
    """requestState is long and different on every run; print only its format prefix."""
    return f"{state[:3]}<sealed, shortened here>"


def printable(message: dict) -> str:
    text = json.loads(json.dumps(message))
    for holder in (text.get("params"), text.get("result")):
        if holder and isinstance(holder.get("requestState"), str):
            holder["requestState"] = short(holder["requestState"])
    return json.dumps(text, indent=2)


def body_of(response: httpx.Response) -> dict:
    """The JSON-RPC message, from a JSON body or from the last event of a stream."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads([d for d in data if d][-1])
    return response.json()


def main() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    env = {**os.environ, "PYTHONPATH": str(PROJECT_SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    server = subprocess.Popen(
        [sys.executable, "-m", "system_info", "--http", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    log: list[str] = []
    threading.Thread(target=lambda: log.extend(server.stderr), daemon=True).start()

    form = {"elicitation": {"form": {}}}
    try:
        with httpx.Client(timeout=15.0) as http:
            for _ in range(300):
                try:
                    http.get(url)
                    break
                except httpx.TransportError:
                    time.sleep(0.05)

            def post(message: dict) -> httpx.Response:
                return http.post(url, json=message, headers=headers())

            # Message 1: the model's call. Only pid; the tool publishes nothing else.
            first = call(1, {"pid": PID}, form)
            print("=== message 1, client to server")
            for key, value in headers().items():
                print(f"{key}: {value}")
            print(printable(first))

            reply = post(first)
            second = body_of(reply)
            print(f"\n=== message 2, server to client (HTTP {reply.status_code})")
            print(printable(second))
            result = second["result"]
            key = next(iter(result["inputRequests"]))
            state = result["requestState"]
            print(f"\nrequestState:           {len(state)} characters, prefix {state[:3]!r}, opaque to the client")
            print(f"inputRequests key:      {key}")
            print(f"ttlMs or cacheScope:    {sorted(set(result) & {'ttlMs', 'cacheScope'}) or 'absent'}")

            # Message 3: the retry. New id, same arguments, the answer, the state verbatim.
            answer = {key: {"action": "accept", "content": {"confirm": True}}}
            third = call(2, {"pid": PID}, form, inputResponses=answer, requestState=state)
            print("\n=== message 3, client to server")
            print(printable(third))

            reply = post(third)
            print(f"\n=== message 4, server to client (HTTP {reply.status_code})")
            print(printable(body_of(reply)))

            print("\n=== three requests the server refuses")
            tampered = state[:-6] + ("A" if state[-6] != "A" else "B") + state[-5:]
            cases = [
                ("arguments changed to pid 4242", call(3, {"pid": 4242}, form, inputResponses=answer, requestState=state)),
                ("one character of requestState changed", call(4, {"pid": PID}, form, inputResponses=answer, requestState=tampered)),
                ("first call with no elicitation capability", call(5, {"pid": PID}, {})),
            ]
            for label, message in cases:
                reply = post(message)
                print(f"--- {label}: HTTP {reply.status_code}")
                print(json.dumps(body_of(reply).get("error") or body_of(reply).get("result")))
    finally:
        server.terminate()
        server.wait(timeout=10)

    rejected = [line.split("WARNING", 1)[-1].strip() for line in log if "requestState rejected" in line]
    print("\nserver stopped; its log gave the real reasons for the requestState refusals:")
    for line in rejected:
        print(f"  {line}")


if __name__ == "__main__":
    main()
