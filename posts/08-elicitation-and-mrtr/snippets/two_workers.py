"""Post 08, section 5: requestState across workers, with and without a shared key.

    uv run python posts/08-elicitation-and-mrtr/snippets/two_workers.py
    uv run python posts/08-elicitation-and-mrtr/snippets/two_workers.py --serve --port 8000 --key ephemeral

With no arguments the file starts itself three times as a Streamable HTTP
worker on free loopback ports: workers A and B are given the same 32-byte key,
worker C keeps the SDK's default, a key of its own generated at startup (which
is also what any worker has after a restart). It asks worker A, sends the retry
to B and then to C, replays it on A, and prints what each answers. Then it prints the two
checks a key policy must pass (RequestStateSecurity's on the key, MCPServer's on the name). All three
workers are stopped before the script exits. With --serve it is one worker and
runs until interrupted.
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import sys
import time
from typing import Annotated

from pydantic import BaseModel, Field
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    RequestStateSecurity,
    Resolve,
)


class Confirm(BaseModel):
    confirm: bool = Field(description="Confirm the restart.")


def ask(service: str) -> Elicit[Confirm]:
    return Elicit(f"Restart {service}? Users will see errors for about a minute.", Confirm)


def build(key: str) -> MCPServer:
    """One worker of the 'fleet' service. Every worker runs this same code."""
    security = None if key == "ephemeral" else RequestStateSecurity(keys=[bytes.fromhex(key)])
    mcp = MCPServer("fleet", request_state_security=security)

    @mcp.tool()
    def restart_service(
        service: str,
        approval: Annotated[ElicitationResult[Confirm], Resolve(ask)],
    ) -> str:
        """Restart a service after the user confirms (this demonstration restarts nothing)."""
        if isinstance(approval, AcceptedElicitation) and approval.data.confirm:
            return f"Restart of {service} approved."
        return f"Restart of {service} not approved."

    return mcp


# ---------------------------------------------------------------------------
# The demonstration.
# ---------------------------------------------------------------------------

REVISION = "2026-07-28"


def message(request_id: int, **extra) -> dict:
    params = {
        "name": "restart_service",
        "arguments": {"service": "billing"},
        **extra,
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": REVISION,
            "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
        },
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": REVISION,
    "Mcp-Method": "tools/call",
    "Mcp-Name": "restart_service",
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def body_of(response) -> dict:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads([d for d in data if d][-1])
    return response.json()


def outcome(response) -> str:
    body = body_of(response)
    if "error" in body:
        return f"HTTP {response.status_code}, error {body['error']['code']} {body['error']['message']!r}"
    result = body["result"]
    if result["resultType"] == "complete":
        return f"HTTP {response.status_code}, complete: {result['content'][0]['text']!r}"
    return f"HTTP {response.status_code}, {result['resultType']}"


def demo() -> None:
    import httpx

    shared = secrets.token_bytes(32).hex()
    workers = {"A": shared, "B": shared, "C": "ephemeral"}
    ports = {name: free_port() for name in workers}
    procs = {
        name: subprocess.Popen(
            [sys.executable, __file__, "--serve", "--port", str(ports[name]), "--key", key],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for name, key in workers.items()
    }
    try:
        with httpx.Client(timeout=15.0) as http:
            for name, port in ports.items():
                for _ in range(300):
                    try:
                        http.get(f"http://127.0.0.1:{port}/mcp")
                        break
                    except httpx.TransportError:
                        time.sleep(0.05)

            def post(worker: str, body: dict):
                return http.post(f"http://127.0.0.1:{ports[worker]}/mcp", json=body, headers=HEADERS)

            print("workers: A and B share one 32-byte key; C uses the default ephemeral key\n")
            reply = post("A", message(1))
            result = body_of(reply)["result"]
            key = next(iter(result["inputRequests"]))
            print(f"tools/call on A          -> {outcome(reply)}, key {key!r}")

            answer = {key: {"action": "accept", "content": {"confirm": True}}}
            retry = dict(inputResponses=answer, requestState=result["requestState"])
            print(f"retry on B (shared key)  -> {outcome(post('B', message(2, **retry)))}")
            print(f"retry on C (its own key) -> {outcome(post('C', message(3, **retry)))}")
            print(f"same retry replayed on A -> {outcome(post('A', message(4, **retry)))}")
    finally:
        for proc in procs.values():
            proc.terminate()
        for proc in procs.values():
            proc.wait(timeout=10)
    print("\nall three workers stopped\n")

    print("== the checks a key policy must pass: RequestStateSecurity on the key, MCPServer on the name ==")
    print(f"default lifetime of sealed state: {RequestStateSecurity.ephemeral().ttl} seconds")
    for label, make in (
        ("a 16-byte key", lambda: RequestStateSecurity(keys=[secrets.token_bytes(16)])),
        (
            "a policy on a server with no name",
            lambda: MCPServer(request_state_security=RequestStateSecurity(keys=[secrets.token_bytes(32)])),
        ),
    ):
        try:
            make()
            print(f"{label}: accepted")
        except ValueError as exc:
            print(f"{label}: ValueError: {' '.join(str(exc).split())}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run one worker until interrupted")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--key", default="ephemeral", help="hex key shared by workers, or 'ephemeral'")
    args = parser.parse_args()
    if args.serve:
        build(args.key).run("streamable-http", host="127.0.0.1", port=args.port)
    else:
        demo()
