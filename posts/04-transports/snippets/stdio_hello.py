"""The same tiny server, served over stdio, and a demonstration of it.

    uv run python posts/04-transports/snippets/stdio_hello.py           # the demonstration
    uv run python posts/04-transports/snippets/stdio_hello.py --serve   # the server a host spawns

With no arguments the file spawns itself with --serve as a child process, twice:
once driven by nothing but subprocess and json, one newline-delimited frame at
a time, and once by the SDK's own stdio client. It prints both exchanges and
exits. With --serve it is the server, reading JSON-RPC lines from stdin until
stdin closes; that is the command a host's configuration names.

stdout carries JSON-RPC frames and nothing else, so every diagnostic in the
server goes to stderr. Compare with http_hello.py, which is the identical
server on the other transport.
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
# The demonstration. Nothing below runs when a host spawns this file.
# ---------------------------------------------------------------------------

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "stdio-hello-demo", "version": "1.0.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def shorten(line: str, width: int = 92) -> str:
    return line if len(line) <= width else line[: width - 3] + "..."


def wrap(line: str, width: int = 88, indent: str = "    ") -> str:
    """Break a long JSON line after commas, for width; the frame itself is one line."""
    rows = [""]
    for piece in line.split(","):
        if rows[-1] and len(rows[-1]) + len(piece) + 1 > width:
            rows[-1] += ","
            rows.append(piece)
        else:
            rows[-1] += ("," if rows[-1] else "") + piece
    return ("\n" + indent).join(rows)


def by_hand() -> None:
    """Drive the server with subprocess and json alone: one line out, one line back."""
    import json
    import subprocess

    child = subprocess.Popen(
        [sys.executable, __file__, "--serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    requests = [
        (1, "server/discover", {}),
        (2, "tools/list", {}),
        (3, "tools/call", {"name": "whoami", "arguments": {}}),
    ]
    print("=== by hand: subprocess and json, one frame per line")
    for request_id, method, params in requests:
        frame = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {**params, "_meta": META}}
        line = json.dumps(frame)
        assert "\n" not in line  # a frame must not contain an embedded newline
        child.stdin.write(line + "\n")
        child.stdin.flush()
        shown = {**frame, "params": {**params, "_meta": "{...}"}}
        print(">>>", shorten(json.dumps(shown).replace('"{...}"', "{...}"), 88))
        reply = child.stdout.readline()
        json.loads(reply)  # every stdout line must parse as JSON
        print("<<<", wrap(reply.strip()) if method == "tools/call" else shorten(reply.strip(), 88))
    child.stdin.close()  # the shutdown signal: end of file on the server's stdin
    code = child.wait(timeout=10)
    errors = [line.strip() for line in child.stderr if "whoami" in line]
    child.stdout.close()
    child.stderr.close()
    print(f"exit code {code} after stdin was closed")
    print(f"stderr carried: {errors}")
    print()


async def with_the_sdk() -> None:
    """The same server, spawned and driven by the SDK's stdio client."""
    import tempfile

    from mcp import Client, StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=[__file__, "--serve"])
    print("=== with the SDK's stdio client")
    with tempfile.TemporaryFile("w+", encoding="utf-8") as server_stderr:
        async with Client(stdio_client(params, errlog=server_stderr)) as client:
            print("protocol_version", client.protocol_version)
            print("server_info     ", f"name={client.server_info.name!r} version={client.server_info.version!r}")
            print("tools           ", [tool.name for tool in (await client.list_tools()).tools])
            result = await client.call_tool("whoami", {})
            print("whoami          ", result.content[0].text)
        server_stderr.seek(0)
        for line in server_stderr:
            if "whoami" in line:
                print("server stderr   ", line.strip())


def demo() -> None:
    import anyio

    by_hand()
    anyio.run(with_the_sdk)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run as a stdio server, as a host spawns it")
    args = parser.parse_args()
    if args.serve:
        mcp.run()
    else:
        demo()
