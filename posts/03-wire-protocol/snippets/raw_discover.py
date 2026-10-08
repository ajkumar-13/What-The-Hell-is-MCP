"""A complete MCP client for two methods, with no SDK.

Speaks protocol revision 2026-07-28 over Streamable HTTP using nothing but an
HTTP client. Sends server/discover, then tools/list, and prints both.

From the series root, with no argument, it starts the post 05 server
(code/05-first-server) on 127.0.0.1 and a free port, queries it, and stops it:

    uv run python posts/03-wire-protocol/snippets/raw_discover.py

Given a URL, it queries a server that is already running instead:

    uv run python posts/03-wire-protocol/snippets/raw_discover.py http://127.0.0.1:8000/mcp

The root pyproject.toml pins httpx below 1.0 (`httpx<1`); this file is written
against the 0.x API, so keep the upper bound if you copy it elsewhere.

When something goes wrong later in the series, this is the fastest way to find
out whether the problem is your server or the thing calling it.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROTOCOL_VERSION = "2026-07-28"
SERIES_ROOT = Path(__file__).resolve().parents[3]
FIRST_SERVER_SRC = SERIES_ROOT / "code" / "05-first-server" / "src"


def build(request_id: int, method: str, params: dict | None = None) -> dict:
    """A JSON-RPC 2.0 request with the two mandatory _meta keys."""
    body = dict(params or {})
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "raw-discover", "version": "1.0.0"},
        # Required even when empty. Leaving the key out entirely is malformed,
        # and a conformant server will reject the request with -32602.
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


def headers(method: str, name: str | None = None) -> dict[str, str]:
    result = {
        "Content-Type": "application/json",
        # Both are accepted: a server may answer with one JSON object, or with
        # an SSE stream when it has progress or notifications to interleave.
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        # Lets a proxy or load balancer route without parsing the body.
        "Mcp-Method": method,
    }
    if name is not None:
        # tools/call, resources/read and prompts/get also mirror the name.
        result["Mcp-Name"] = name
    return result


def read_result(response: httpx.Response) -> dict:
    """Return the JSON-RPC message, whether it arrived plain or as SSE."""
    content_type = response.headers.get("content-type", "")

    if content_type.startswith("text/event-stream"):
        # Take the last complete `data:` frame that has an id; earlier frames
        # are usually progress notifications, which are not the answer.
        message = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                candidate = json.loads(line[5:].strip())
                if "id" in candidate:
                    message = candidate
        if message is None:
            raise SystemExit("no JSON-RPC response found in the event stream")
        return message

    return response.json()


def call(client: httpx.Client, url: str, request_id: int, method: str, params=None) -> dict:
    response = client.post(url, json=build(request_id, method, params), headers=headers(method))

    # A malformed request comes back as 400 with a JSON-RPC error in the body,
    # so read the body before trusting the status line.
    if response.status_code >= 400 and not response.content:
        raise SystemExit(f"{method}: HTTP {response.status_code} with an empty body")

    message = read_result(response)
    if "error" in message:
        error = message["error"]
        raise SystemExit(f"{method}: {error['code']} {error['message']}")
    return message["result"]


def server_info(result: dict) -> tuple[dict, str]:
    """Find serverInfo where the specification puts it, or where mcp==2.0.0b2 does."""
    meta = result.get("_meta") or {}
    if "io.modelcontextprotocol/serverInfo" in meta:
        return meta["io.modelcontextprotocol/serverInfo"], "in result._meta, as the specification asks"
    if "serverInfo" in result:
        return result["serverInfo"], "a top-level result field, not in result._meta"
    return {}, "absent"


@contextlib.contextmanager
def local_server():
    """Run the post 05 server over Streamable HTTP on 127.0.0.1 for one block."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, PYTHONPATH=str(FIRST_SERVER_SRC))
    process = subprocess.Popen(
        [sys.executable, "-m", "system_info", "--http", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            if process.poll() is not None:
                raise SystemExit(f"the post 05 server exited with code {process.returncode}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
                break
            if time.monotonic() > deadline:
                raise SystemExit("the post 05 server did not start within 60 seconds")
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def discover_and_list(url: str) -> None:
    with httpx.Client(timeout=30.0) as client:
        discover = call(client, url, 1, "server/discover")
        info, where = server_info(discover)
        print(f"server        {info.get('name', '?')} {info.get('version', '')}".rstrip())
        print(f"serverInfo    {where}")
        print(f"versions      {', '.join(discover.get('supportedVersions', []))}")
        print(f"capabilities  {', '.join(discover.get('capabilities', {})) or 'none'}")
        if discover.get("ttlMs") is not None:
            print(f"cacheable     {discover['ttlMs']} ms, scope {discover.get('cacheScope', '?')}")
        envelope = json.dumps(build(1, "server/discover")["params"]["_meta"], separators=(",", ":"))
        print(f"_meta         {len(envelope.encode())} bytes on every request")

        listing = call(client, url, 2, "tools/list")
        tools = listing["tools"]
        print(f"\n{len(tools)} tools, cacheable {listing.get('ttlMs', '?')} ms, scope {listing.get('cacheScope', '?')}")
        for tool in tools:
            required = tool.get("inputSchema", {}).get("required", [])
            print(f"  {tool['name']:<24} required: {', '.join(required) or 'nothing'}")


def main() -> None:
    if len(sys.argv) > 1:
        discover_and_list(sys.argv[1])
        return
    print("(no URL given: starting code/05-first-server on 127.0.0.1 for this run)", file=sys.stderr)
    with local_server() as url:
        discover_and_list(url)


if __name__ == "__main__":
    main()
