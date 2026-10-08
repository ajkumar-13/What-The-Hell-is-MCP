"""One request, one event stream: what a tool that reports progress sends back over HTTP.

    uv run python posts/04-transports/snippets/progress_stream.py           # the demonstration
    uv run python posts/04-transports/snippets/progress_stream.py --serve   # the server on 127.0.0.1:8000/mcp

With no arguments the file starts itself as a Streamable HTTP server on a free
loopback port, sends one hand-composed tools/call that carries a progressToken,
prints the response headers and the raw event stream exactly as they arrived,
and stops the server.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from mcp.server.mcpserver import Context, MCPServer

mcp = MCPServer("progress")


@mcp.tool()
async def slow_count(n: int, ctx: Context) -> int:
    """Count to n, a fifth of a second per step, reporting progress at each step."""
    for i in range(1, n + 1):
        await asyncio.sleep(0.2)
        await ctx.report_progress(progress=i, total=n, message=f"counted {i} of {n}")
    return n


def demo() -> None:
    import json
    import socket
    import subprocess
    import time

    import httpx

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    server = subprocess.Popen(
        [sys.executable, __file__, "--serve", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "slow_count",
            "arguments": {"n": 3},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientInfo": {"name": "progress-demo", "version": "1.0.0"},
                "io.modelcontextprotocol/clientCapabilities": {},
                "progressToken": "count-1",
            },
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "slow_count",
    }
    try:
        with httpx.Client(timeout=10.0) as http:
            for _ in range(200):
                try:
                    http.get(url)
                    break
                except httpx.TransportError:
                    time.sleep(0.05)

            with http.stream("POST", url, json=body, headers=headers) as reply:
                print(f"HTTP {reply.status_code}")
                for key, value in reply.headers.items():
                    if key not in ("date", "server"):
                        print(f"  {key}: {value}")
                print()
                raw = b"".join(reply.iter_raw())
    finally:
        server.terminate()
        server.wait(timeout=10)

    text = raw.decode("utf-8")
    events = [block for block in text.split("\r\n\r\n") if block.strip()]
    for block in events:
        for line in block.split("\r\n"):
            if line.startswith("data: ") and len(line) > 92:
                # Wrap long data lines after commas, for width only.
                rows = [""]
                for piece in line.split(","):
                    if rows[-1] and len(rows[-1]) + len(piece) + 1 > 92:
                        rows[-1] += ","
                        rows.append(piece)
                    else:
                        rows[-1] += ("," if rows[-1] else "") + piece
                line = "\n       ".join(rows)
            print(line)
        print()
    last = json.loads(events[-1].split("data: ", 1)[1])
    id_fields = sum(line.startswith("id:") for line in text.split("\r\n"))
    print(f"{len(events)} events; SSE id: fields {id_fields}; the last carries id {last['id']} and ends the stream")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run as a Streamable HTTP server until interrupted")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.serve:
        mcp.run("streamable-http", host="127.0.0.1", port=args.port, streamable_http_path="/mcp")
    else:
        demo()
