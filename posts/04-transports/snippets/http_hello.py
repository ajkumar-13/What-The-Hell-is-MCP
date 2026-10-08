"""The same tiny server, served over Streamable HTTP, and a demonstration of it.

    uv run python posts/04-transports/snippets/http_hello.py           # the demonstration
    uv run python posts/04-transports/snippets/http_hello.py --serve   # a real server on 127.0.0.1:8000/mcp

With no arguments the file starts itself as a server on a free loopback port,
sends it hand-composed POSTs with nothing but httpx (no MCP client), prints
what came back, and stops the server. With --serve it is the server and runs
until interrupted.

Compare with stdio_hello.py: the tool is identical; only the run() call differs.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys

from mcp.server.mcpserver import MCPServer

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("hello")

mcp = MCPServer("hello")


@mcp.tool()
def whoami() -> str:
    """Report the operating system this server is running on."""
    log.info("whoami called")
    return f"{platform.system()} {platform.release()}"


# ---------------------------------------------------------------------------
# The demonstration. Everything below composes requests by hand.
# ---------------------------------------------------------------------------

REVISION = "2026-07-28"
NAME_BEARING = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}


def build(request_id: int, method: str, params: dict | None = None, revision: str = REVISION) -> dict:
    """A JSON-RPC request carrying the three request-side _meta keys."""
    body = dict(params or {})
    body["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": revision,
        "io.modelcontextprotocol/clientInfo": {"name": "http-hello-demo", "version": "1.0.0"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body}


def encode(value: str) -> str:
    """The Base64 sentinel for a header value that is not safe as plain ASCII."""
    import base64
    import re

    safe = all(0x21 <= ord(c) <= 0x7E or c == " " for c in value) and value == value.strip()
    if safe and not re.fullmatch(r"=\?base64\?.*\?=", value):
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def headers_for(body: dict) -> dict[str, str]:
    """Every header a 2026-07-28 POST needs, derived from the body it carries."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"],
        "Mcp-Method": body["method"],
    }
    if body["method"] in NAME_BEARING:
        headers["Mcp-Name"] = encode(body["params"][NAME_BEARING[body["method"]]])
    return headers


def wrap(line: str, width: int = 90, indent: str = " ") -> str:
    """Break a long JSON line after commas, for width only; the body itself is one line."""
    rows = [""]
    for piece in line.split(","):
        if rows[-1] and len(rows[-1]) + len(piece) + 1 > width:
            rows[-1] += ","
            rows.append(piece)
        else:
            rows[-1] += ("," if rows[-1] else "") + piece
    return ("\n" + indent).join(rows)


def show(title: str, response) -> None:
    """Print the status line, the content type, and the body."""
    kind = response.headers.get("content-type", "")
    print(f"--- {title}")
    print(f"HTTP {response.status_code}  {kind or '(no content-type)'}")
    text = response.text.strip()
    print(wrap(text) if text else "(empty body)")
    print()


def demo() -> None:
    import socket
    import subprocess
    import threading
    import time

    import httpx

    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")  # one header value below is not ASCII
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"

    server = subprocess.Popen(
        [sys.executable, __file__, "--serve", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    server_log: list[str] = []
    drain = threading.Thread(target=lambda: server_log.extend(server.stderr), daemon=True)
    drain.start()

    try:
        with httpx.Client(timeout=10.0) as http:
            for _ in range(200):
                try:
                    http.get(url)
                    break
                except httpx.TransportError:
                    time.sleep(0.05)

            def post(body: dict, **changes: str | None):
                headers = headers_for(body)
                for key, value in changes.items():
                    key = key.replace("_", "-")
                    if value is None:
                        headers.pop(key, None)
                    else:
                        headers[key] = value
                return http.post(url, json=body, headers=headers)

            # 1. Discovery and the tool list, composed by hand.
            body = build(1, "server/discover")
            print("--- the headers this demonstration derives for a tools/call body")
            for key, value in headers_for(build(3, "tools/call", {"name": "whoami", "arguments": {}})).items():
                print(f"{key}: {value}")
            print()
            print("--- header values through the Base64 sentinel rule")
            for value in ["us-west1", "Hello, 世界", " padded ", "line1\nline2", "=?base64?literal?="]:
                print(f"{value!r:<22} -> {encode(value)}")
            print()

            reply = post(body)
            result = reply.json()["result"]
            print("--- server/discover")
            print(f"HTTP {reply.status_code}  {reply.headers['content-type']}")
            info = result["_meta"]["io.modelcontextprotocol/serverInfo"]
            print(f"serverInfo    in result _meta: name={info['name']!r} version={info['version']!r}")
            print(f"revisions     {', '.join(result['supportedVersions'])}")
            print(f"capabilities  {', '.join(result['capabilities'])}")
            print()

            reply = post(build(2, "tools/list"))
            print("--- tools/list")
            print(f"HTTP {reply.status_code}  {reply.headers['content-type']}")
            print(f"tools                 {[t['name'] for t in reply.json()['result']['tools']]}")
            print(f"response headers      {', '.join(reply.headers.keys())}")
            print()

            show("tools/call whoami", post(build(3, "tools/call", {"name": "whoami", "arguments": {}})))

            # 2. The rejections the status-code table promises.
            show("unknown method", post(build(4, "does/notexist")))
            show("unsupported protocol revision", post(build(5, "tools/list", revision="1900-01-01")))
            show(
                "Mcp-Name does not match the body",
                post(build(6, "tools/call", {"name": "whoami", "arguments": {}}), Mcp_Name="get_weather"),
            )
            show("Mcp-Method omitted", post(build(7, "tools/list"), Mcp_Method=None))
            show(
                "Accept lists application/json only",
                post(build(8, "tools/list"), Accept="application/json"),
            )
            show(
                "a notification body",
                http.post(
                    url,
                    json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 3}},
                    headers={**headers_for(build(0, "tools/list")), "Mcp-Method": "notifications/cancelled"},
                ),
            )

            # 3. What an older client might still send.
            modern = {"MCP-Protocol-Version": REVISION}
            show("GET to the MCP endpoint", http.get(url, headers=modern))
            show("DELETE to the MCP endpoint", http.delete(url, headers=modern))
            reply = post(build(9, "tools/list"), Mcp_Session_Id="a91f", Last_Event_ID="7")
            print("--- Mcp-Session-Id: a91f and Last-Event-ID: 7 sent on a tools/list")
            print(f"HTTP {reply.status_code}  {reply.headers['content-type']}")
            print(f"tools                 {[t['name'] for t in reply.json()['result']['tools']]}")
            print(f"mcp-session-id echoed {'mcp-session-id' in reply.headers}")
            print()

            # 4. Transport security at this layer.
            show("Origin: http://evil.example", post(build(10, "tools/list"), Origin="http://evil.example"))
            show(
                "Host: mcp.example.com, as a reverse proxy might forward it",
                post(build(11, "tools/list"), Host="mcp.example.com"),
            )

            # 5. The era probe, both ways.
            legacy = {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "old-client", "version": "0.1.0"},
                },
            }
            reply = http.post(
                url,
                json=legacy,
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            )
            print("--- a legacy initialize naming 2025-06-18")
            print(f"HTTP {reply.status_code}  {reply.headers.get('content-type', '')}")
            for line in reply.text.splitlines():
                if line.startswith("data:"):
                    answered = __import__("json").loads(line[5:])["result"]["protocolVersion"]
                    print(f"protocolVersion answered  {answered}")
            print(f"mcp-session-id minted     {'mcp-session-id' in reply.headers}")
            print()
            show(
                "header names 2025-06-18, body _meta names 2026-07-28",
                post(build(13, "tools/list"), MCP_Protocol_Version="2025-06-18"),
            )
    finally:
        server.terminate()
        server.wait(timeout=10)

    calls = sum("whoami called" in line for line in server_log)
    print(f"server stopped; its stderr logged 'whoami called' {calls} time(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run as a Streamable HTTP server until interrupted")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.serve:
        mcp.run("streamable-http", host="127.0.0.1", port=args.port, streamable_http_path="/mcp")
    else:
        demo()
