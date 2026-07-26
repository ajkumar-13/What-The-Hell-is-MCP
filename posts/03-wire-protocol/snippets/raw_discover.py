"""A complete MCP client for two methods, with no SDK.

Speaks protocol revision 2026-07-28 over Streamable HTTP using nothing but an
HTTP client. Sends server/discover, then tools/list, and prints both.

    uv run --with httpx python raw_discover.py http://127.0.0.1:8000/mcp

When something goes wrong later in the series, this is the fastest way to find
out whether the problem is your server or the thing calling it.
"""

from __future__ import annotations

import json
import sys

import httpx

PROTOCOL_VERSION = "2026-07-28"


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


def headers(method: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        # Both are accepted: a server may answer with one JSON object, or with
        # an SSE stream when it has progress or notifications to interleave.
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        # Lets a proxy or load balancer route without parsing the body.
        "Mcp-Method": method,
    }


def read_result(response: httpx.Response) -> dict:
    """Return the JSON-RPC message, whether it arrived plain or as SSE."""
    content_type = response.headers.get("content-type", "")

    if content_type.startswith("text/event-stream"):
        # Take the last complete `data:` frame; earlier ones are usually
        # progress notifications, which have no "id" and are not the answer.
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


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"

    with httpx.Client(timeout=30.0) as client:
        discover = call(client, url, 1, "server/discover")
        info = discover.get("serverInfo", {})
        print(f"server        {info.get('name', '?')} {info.get('version', '')}".rstrip())
        print(f"versions      {', '.join(discover.get('supportedVersions', []))}")
        print(f"capabilities  {', '.join(discover.get('capabilities', {})) or 'none'}")
        if discover.get("ttlMs") is not None:
            print(f"cacheable     {discover['ttlMs']} ms, scope {discover.get('cacheScope', '?')}")

        tools = call(client, url, 2, "tools/list")["tools"]
        print(f"\n{len(tools)} tools")
        for tool in tools:
            required = tool.get("inputSchema", {}).get("required", [])
            print(f"  {tool['name']:<24} required: {', '.join(required) or 'nothing'}")


if __name__ == "__main__":
    main()
