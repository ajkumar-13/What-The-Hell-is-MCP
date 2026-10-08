"""The stdout rule, broken on purpose, and where each stray write ends up.

    uv run python posts/04-transports/snippets/stdout_trap.py                     # the demonstration
    uv run python posts/04-transports/snippets/stdout_trap.py --serve             # the broken server
    uv run python posts/04-transports/snippets/stdout_trap.py --serve --flush     # the same, stdout line-buffered
    uv run python posts/04-transports/snippets/stdout_trap.py --serve --banner    # a flushed banner before run()

The server below is stdio_hello.py with two bugs: a print() in one tool, and a
child process that inherits stdout in the other. While mcp.run() serves, the
SDK points file descriptor 1 at stderr and writes its frames through a private
duplicate of the pipe, so writes made during the session miss the wire; it
points descriptor 1 back at the pipe when the transport stops.

With no arguments the file spawns itself under the SDK's stdio client three
times: with Python's default buffering, with stdout line-buffered (what
print(..., flush=True) does), and with a banner printed and flushed before
mcp.run(), as a dependency that announces itself at import would. Each run
prints what the client logged and what reached the server's stderr. Then it
runs the cheap check twice: feed the server one request, close stdin, and
parse every line the server wrote to stdout until end of file.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("hello")


@mcp.tool()
def whoami() -> str:
    """Report the operating system this server is running on."""
    print("whoami called")          # the bug
    return f"{platform.system()} {platform.release()}"


@mcp.tool()
def python_version() -> str:
    """Report the Python version, as a child interpreter states it."""
    subprocess.run([sys.executable, "--version"])  # the child inherits stdout
    return platform.python_version()


# ---------------------------------------------------------------------------
# The demonstration.
# ---------------------------------------------------------------------------

META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "stdout-trap-demo", "version": "1.0.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
STRAY = ("whoami called", "Python ", "somelib 1.0 ready")


def report(label: str, records: list) -> None:
    """Print what the client logged, with the parse error's own lines."""
    if not records:
        print(f"{label:<15} client logged nothing")
    for record in records:
        print(f"{label:<15} client logged: {record.getMessage()}")
        label = ""
        if record.exc_info:
            for line in str(record.exc_info[1]).splitlines():
                if line.strip() and "errors.pydantic.dev" not in line:
                    print(f"{'':<15}   {line.strip()}")


async def under_the_sdk(mode: str) -> None:
    """Run the server under the SDK's stdio client and report where the stray text went."""
    import logging
    import tempfile

    from mcp import Client, StdioServerParameters, stdio_client

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    client_log = logging.getLogger("mcp.client.stdio")
    client_log.addHandler(handler)
    client_log.propagate = False

    titles = {
        "": "block-buffered (the default for a pipe)",
        "--flush": "line-buffered",
        "--banner": "block-buffered, a banner flushed before mcp.run()",
    }
    args = [__file__, "--serve"] + ([mode] if mode else [])
    print(f"=== SDK client, server stdout {titles[mode]}")
    try:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as server_stderr:
            params = StdioServerParameters(command=sys.executable, args=args)
            async with Client(stdio_client(params, errlog=server_stderr)) as client:
                report("at connect", records)
                for tool in ("whoami", "python_version"):
                    seen = len(records)
                    result = await client.call_tool(tool, {})
                    print(f"{tool:<15} result {result.content[0].text!r}")
                    report("", records[seen:])
                seen = len(records)
            report("after shutdown", records[seen:])
            server_stderr.seek(0)
            stray = [line.strip() for line in server_stderr if line.startswith(STRAY)]
            print(f"{'server stderr':<15} {stray}")
    finally:
        client_log.removeHandler(handler)
        client_log.propagate = True
    print()


def cheap_check(interpreter_flags: list[str]) -> None:
    """Feed one request, close stdin, and parse every line that reached stdout until end of file."""
    import json

    command = [sys.executable, *interpreter_flags, __file__, "--serve"]
    frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "_meta": META}}
    child = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
    )
    child.stdin.write(json.dumps(frame) + "\n")
    child.stdin.flush()
    lines: list[str] = []
    while not lines or not lines[-1].startswith('{"jsonrpc"'):  # read until the response arrives
        line = child.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))
    child.stdin.close()  # end of file on stdin: the server exits
    lines += child.stdout.read().splitlines()  # everything else, until end of file
    child.stdout.close()
    child.wait(timeout=10)
    bad = []
    for line in lines:
        try:
            json.loads(line)
        except ValueError:
            bad.append(line)
    shown = " ".join(["python", *interpreter_flags, "stdout_trap.py", "--serve"])
    print(f"cheap check, {shown:<35} {len(lines)} stdout line(s), not JSON: {bad}")


def demo() -> None:
    import anyio

    for mode in ("", "--flush", "--banner"):
        anyio.run(under_the_sdk, mode)
    for flags in ([], ["-u"]):
        cheap_check(flags)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run the broken server over stdio")
    parser.add_argument("--flush", action="store_true", help="line-buffer stdout, as print(..., flush=True) would")
    parser.add_argument("--banner", action="store_true", help="print and flush a banner before mcp.run()")
    args = parser.parse_args()
    if args.serve:
        if args.flush:
            sys.stdout.reconfigure(line_buffering=True)
        if args.banner:
            print("somelib 1.0 ready", flush=True)  # a dependency announcing itself at import
        mcp.run()
    else:
        demo()
