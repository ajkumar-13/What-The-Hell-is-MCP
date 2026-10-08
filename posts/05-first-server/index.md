# 05 - Your first MCP server

> **TL;DR.** A Model Context Protocol (MCP) server is an ordinary Python package: one server object, a few decorated functions, and their return values, with the protocol machinery inside the software development kit (SDK). This post builds a system-information server with two tools, shows the JavaScript Object Notation (JSON) each tool publishes before the code that produces it, drives the server by hand one JSON line at a time, and ends on the failures that keep a server out of a host, in the order that finds them fastest. Every schema, transcript and error message below is printed by a script in `snippets/` or by the project's own test suite.
>
> **Prerequisites:** [Post 03](../03-wire-protocol/index.md).
> **Safe to skip?** Skip it if the reader can already build a `uv` project around `mcp[cli]==2.3.0`, predict the input schema the SDK generates for a typed function, drive a server by hand over standard input and output (stdio), and get a local server to appear in a desktop host.
>
> **After reading, you will be able to:**
>
> - Create a uv project for an MCP server, pinned to one SDK release, that a desktop host can spawn.
> - Register a tool with @mcp.tool() and read the input schema the SDK generates from the function's type hints and defaults.
> - Drive the server by hand over standard input and output, one JSON-RPC request per line, and read each response or error.
> - Diagnose a server that does not appear in a host by checking the standalone run, the paths, the JSON, the restart and stdout, in that order.

![A host process on the left spawns a Python server process on the right. Three labeled channels run between them: the host writes JSON-RPC requests to the server's standard input, the server writes JSON-RPC responses back on standard output, and the server writes human-readable log lines on standard error, which the host files away in a log rather than parsing.](diagrams/01-your-file-and-the-host.svg)

*The host starts the server as a child process, and three streams do all the work.*

---

## 1. The question: what is the smallest useful server?

Every explanation so far has been about messages. [Post 03](../03-wire-protocol/index.md) took the JavaScript Object Notation Remote Procedure Call (JSON-RPC) envelope apart field by field, and [post 04](../04-transports/index.md) followed those bytes down to pipes and sockets. This post writes the thing that answers them, and asks one question on the way: **what is the smallest useful MCP server, and where does the protocol go once the SDK is doing its job?**

The server is a **system-information server**: a small process that reports on the machine it runs on. This post gives it two tools. `get_system_info` takes a snapshot of central processing unit (CPU), memory, and disk usage. `find_process` searches running processes by name. Later posts in Part II add resources and a prompt ([post 07](../07-resources-and-prompts/index.md)), a tool that asks the user for confirmation ([post 08](../08-elicitation-and-mrtr/index.md)), and a tool that reports progress ([post 09](../09-tasks/index.md)), all to the same server.

It is a good first server for four reasons, and none of them is that it is impressive.

**It needs no credentials.** No application programming interface (API) key, no database, no network. Nothing stands between the reader and the protocol.

**The data is genuinely live.** A tool that returns a constant proves nothing about whether the call really happened. When the CPU number changes between two calls, the round trip is real.

**It has one tool with no arguments and one tool with two.** That is the smallest pair that shows how a Python signature becomes a JSON Schema, which is section 5 and the single most useful idea in this post.

**It is honest about privacy.** Process names and identifiers are real information about a machine. Whatever a server returns goes into the conversation, and if the host uses a cloud model, into a request to that model's provider. That is worth internalizing on a harmless server, before building one that touches something that matters.

The complete project lives in [code/05-first-server/](../../code/05-first-server/), and every path below that starts with `src/` is relative to it. This post writes two of its files, `src/system_info/app.py` and `src/system_info/tools.py`, and reads a third, `src/system_info/__main__.py`.

## 2. Setup: a project, not a script

The project needs Python 3.10 or newer and [uv](https://docs.astral.sh/uv/), the package manager that the SDK's own command-line interface (CLI) writes into host configurations. Check both first:

```bash
python --version
uv --version
```

Then create the project and add the two dependencies:

```bash
uv init --package --build-backend hatch --name system-info mcp-system-info
cd mcp-system-info
uv add "mcp[cli]==2.3.0" psutil
```

`--package` matters more than it looks. It lays the code out under `src/system_info/` and declares a build backend, so `uv run` installs the package into the project's environment and `python -m system_info` can find it. A plain `uv init` makes an application project with no build backend, nothing puts `src/` on the import path, and the server never starts. The project under `code/` declares the same backend by hand in its `pyproject.toml`.

That pin is not decoration. This series targets protocol revision **2026-07-28**, and the 2.x line of the Python SDK is the line that speaks it; the 1.x line has a different module layout, and section 10 shows what code written for it does under 2.x. The pin is exact rather than a range because releases within the 2.x line have changed what a server publishes on the wire, and an exact pin is what makes the reader's transcripts match the ones below. `uv add` writes the pin into `pyproject.toml` and the resolved versions into `uv.lock`, so every later `uv sync` and `uv run`, including the one a host spawns, installs exactly the same versions.

> **A note on the release this series pins.** Every listing and transcript in this post was produced against `mcp==2.3.0` and `mcp-types==2.3.0`; `snippets/tool_surface.py` prints the versions and the Python it ran on (3.12.12 here).

The layout of the finished project:

```
05-first-server/
├── pyproject.toml
├── README.md
├── src/
│   └── system_info/
│       ├── __init__.py
│       ├── __main__.py       # the entry point
│       ├── app.py            # this post
│       ├── tools.py          # this post
│       ├── resources.py      # post 07
│       ├── interactive.py    # post 08
│       └── progress.py       # post 09
└── tests/
    └── test_server.py
```

A package rather than a single `server.py`, because a server that grows past one primitive wants its registrations in separate files, and because a package is what `python -m` can start. A reader typing along creates only `app.py` and `tools.py` for now and leaves the later three out of `__init__.py` until the posts that write them. `uv init --package` also writes a `[project.scripts]` entry pointing at a `main` in `__init__.py`; the project points its script at `system_info.__main__:main` instead, and nothing in this post uses it.

## 3. The smallest server that runs

Here is `src/system_info/app.py` in full. Two imports, a logging call, and one object.

```python
"""The server instance itself.

Everything else in this package imports `mcp` from here and hangs a tool, a
resource, or a prompt off it. Keeping the instance in its own module is what
lets the registrations live in separate files without a circular import.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

# Under the stdio transport, stdout IS the protocol channel. Anything printed
# there before the server starts is parsed as a JSON-RPC frame, and a stray
# print() at import time breaks the connection in a way that is hard to
# diagnose (once mcp.run() is serving, the SDK diverts later prints to stderr).
# Logging goes to stderr, which a host may capture into a log.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("system-info")

mcp = MCPServer("system-info")
```

Three things are happening, and one of them is the most important line in the file.

`MCPServer("system-info")` creates the server. The string is the name the server reports about itself, in the `io.modelcontextprotocol/serverInfo` entry it puts in the `_meta` of every result (section 7 shows one). It is not a unique identifier: [post 02](../02-architecture/index.md) covered why a host must not use a server's self-reported name as a key, and a host names the connection after the key its own configuration gives it (section 6).

The `logging.basicConfig(stream=sys.stderr, ...)` call is the load-bearing one. Under the standard input and output transport (stdio), which is what a desktop host uses and what [post 04](../04-transports/index.md) described in detail, the process's **standard output is the protocol channel**. The specification's Transports page says the server **MUST NOT** write anything to stdout that is not a valid MCP message, and the bytes on it are parsed as JSON-RPC frames. Standard error is the server's own: the same page lets a server write log strings there, and lets a host capture, forward or ignore them; Claude Desktop writes them to a per-server log file (section 8). Configuring logging to stderr in the first module the package imports is how nothing ever writes to the wrong stream by accident.

Notice what is *not* here: no transport, no port, no handshake, no capability negotiation. There is nothing to negotiate. In revision 2026-07-28 a client declares its capabilities in `_meta` on every request and the server declares its own in the result of `server/discover`, and the SDK answers that method without any code in the project.

To actually start it, `src/system_info/__main__.py` reads its arguments and calls `run()`:

```python
from __future__ import annotations

import argparse

from . import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="system-info")
    parser.add_argument(
        "--http",
        action="store_true",
        help="serve over Streamable HTTP instead of stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        # Bind to loopback by default. A local server listening on 0.0.0.0 is
        # reachable by anything on the network, and this one reports on your
        # machine and can terminate processes on it.
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
```

`mcp.run()` is **synchronous**, and its first positional argument is the transport, which defaults to `"stdio"`. A bare `mcp.run()` therefore serves over stdio, which is exactly what a desktop host spawns. `host` and `port` belong to `run()` and not to the constructor, a mistake section 10 shows.

Run it now. It should sit there doing nothing, because nothing has spoken to it yet.

```bash
uv run python -m system_info
```

Press Ctrl+C to stop it. A server that exits immediately instead of waiting has printed a traceback, and that traceback is the bug.

## 4. The first tool

Before the decorator, the message. This is the entry a client receives for this tool when it calls `tools/list` against the finished server, as `snippets/tool_surface.py` prints it: one tool from the list, with the wire spellings (`inputSchema`, `readOnlyHint`) rather than the Python ones (`input_schema`, `read_only_hint`).

```json
{
  "name": "get_system_info",
  "title": "Get system snapshot",
  "description": "Read current CPU, memory, and disk usage for this machine.\n\n    Returns a single snapshot taken over a short sampling interval.\n    ",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "title": "get_system_infoArguments"
  },
  "outputSchema": {
    "properties": {
      "cpu_percent": {"title": "Cpu Percent", "type": "number"},
      "memory_percent": {"title": "Memory Percent", "type": "number"},
      "memory_used_gb": {"title": "Memory Used Gb", "type": "integer"},
      "memory_total_gb": {"title": "Memory Total Gb", "type": "integer"},
      "disk_percent": {"title": "Disk Percent", "type": "number"},
      "disk_used_gb": {"title": "Disk Used Gb", "type": "integer"},
      "disk_total_gb": {"title": "Disk Total Gb", "type": "integer"}
    },
    "required": [
      "cpu_percent",
      "memory_percent",
      "memory_used_gb",
      "memory_total_gb",
      "disk_percent",
      "disk_used_gb",
      "disk_total_gb"
    ],
    "title": "SystemSnapshot",
    "type": "object"
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  }
}
```

Read it as the model does. `name` is what gets called, and with `inputSchema` it is one of the only two fields the specification's Tools page requires. `title` is for people, in the host's interface. `description` is the prose the model reads to decide what the tool is for; the Tools page calls it a hint to the model. `inputSchema` declares no arguments. `outputSchema` promises the shape of what comes back. `annotations` are hints for the host about how the tool behaves, and the Tools page tells clients to treat them as untrusted unless the server is.

Nothing in that object was written by hand. The top of `src/system_info/tools.py` imports what it needs and declares the return type:

```python
from __future__ import annotations

import pathlib
from dataclasses import dataclass

import psutil
from mcp_types import ToolAnnotations

from .app import mcp

_GB = 1024**3

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass
class SystemSnapshot:
    """A point-in-time reading of this machine.

    The class-body annotations below are load-bearing. A class that only sets
    attributes inside __init__ has no type hints for the SDK to read, and you
    get `outputSchema: null` with no warning at all. Post 06 covers this trap.
    """

    cpu_percent: float
    memory_percent: float
    memory_used_gb: int
    memory_total_gb: int
    disk_percent: float
    disk_used_gb: int
    disk_total_gb: int
```

Further down the same file, `src/system_info/tools.py` registers the tool itself:

```python
@mcp.tool(
    title="Get system snapshot",
    annotations=READ_ONLY,
)
def get_system_info() -> SystemSnapshot:
    """Read current CPU, memory, and disk usage for this machine.

    Returns a single snapshot taken over a short sampling interval.
    """
    # A zero interval would compare against the previous call and return a
    # meaningless number on the first invocation.
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()

    # Path.home().anchor gives "C:\\" on Windows and "/" on Unix.
    disk = psutil.disk_usage(pathlib.Path.home().anchor or "/")

    return SystemSnapshot(
        cpu_percent=round(cpu, 1),
        memory_percent=round(mem.percent, 1),
        memory_used_gb=mem.used // _GB,
        memory_total_gb=mem.total // _GB,
        disk_percent=round(disk.percent, 1),
        disk_used_gb=disk.used // _GB,
        disk_total_gb=disk.total // _GB,
    )
```

Line it up against the JSON above and every field has a source. The function name became `name`. The docstring became `description`, whitespace and all: on the Python 3.12 that printed it, the docstring keeps its four-space indentation and so does the description. Python 3.13 strips that indentation when it compiles a docstring, so the same server run there publishes `"...machine.\n\nReturns a single snapshot taken over a short sampling interval.\n"`. The empty parameter list became an empty `inputSchema`. The `SystemSnapshot` dataclass became `outputSchema`, one property per class attribute. `title=` and `annotations=` are the two decorator arguments used here, and they became `title` and `annotations`; on the wire the four hints are camelCase, in Python they are snake_case.

Two details in the body are worth stealing. `psutil.cpu_percent(interval=0.5)` blocks for half a second and measures; with an interval of zero it compares against the previous call, and the first call returns a meaningless `0.0`. And `pathlib.Path.home().anchor` gives `C:\` on Windows and `/` on Unix, which is the cheapest way to ask for the disk that holds the user's home directory without branching on the platform.

The `outputSchema` and `annotations` fields both deserve more than the sentence they get here. [Post 06](../06-tools-in-depth/index.md) is entirely about them, including the trap the docstring above warns about, where a return class without class-body annotations silently publishes `"outputSchema": null`.

## 5. A tool with arguments, and how a signature becomes a schema

The second tool takes input. Here is the wire form of its `inputSchema`, again from `snippets/tool_surface.py`:

```json
{
  "type": "object",
  "properties": {
    "name": {"title": "Name", "type": "string"},
    "limit": {"default": 25, "title": "Limit", "type": "integer"}
  },
  "required": ["name"],
  "title": "find_processArguments"
}
```

And the signature in `src/system_info/tools.py` that produced it:

```python
def find_process(name: str, limit: int = 25) -> ProcessMatches:
```

![On the left, the decorated Python function find_process, whose parameters are name annotated as str with no default and limit annotated as int with a default of 25. On the right, the JSON Schema the SDK generated from that signature. A leader line runs from each parameter to the schema property it became. Panels along the bottom state the rule: no default means the parameter is listed in required, and a default means it carries a default keyword and is left out of required.](diagrams/02-signature-to-schema.svg)

*A parameter with a default becomes an optional property; a parameter without one becomes required.*

Four rules cover almost everything a first server needs.

**The parameter name becomes the property name.** No transformation, no camel-casing.

**The type hint becomes the JSON Schema type.** `str` is `"string"`, `int` is `"integer"`, `float` is `"number"`, `bool` is `"boolean"`. A parameter with no annotation at all is published as a string, which is rarely what was meant: `snippets/sdk_errors.py` registers `def lookup(key, limit: int = 5)` and the SDK publishes `"key": {"title": "key", "type": "string"}`.

**A default makes a parameter optional.** `limit: int = 25` produced both `"default": 25` and an absence from `required`. `name: str` has no default, so it is in `required`. A call that omits it gets no process list; the SDK answers with a result marked `isError: true` whose text names the missing field, as `snippets/tool_surface.py` prints:

```
isError: true
Error executing tool find_process: 1 validation error for find_processArguments
name
  Field required [type=missing, input_value={}, input_type=dict]
```

**The docstring becomes the description.** Including the argument documentation, which is where the model learns what the schema cannot express.

That last point is why the real function in `src/system_info/tools.py` documents its bounds in prose:

```python
@mcp.tool(
    title="Find processes by name",
    annotations=READ_ONLY,
)
def find_process(name: str, limit: int = 25) -> ProcessMatches:
    """Find running processes whose name contains `name`, case-insensitively.

    Args:
        name: Substring to match against the process name.
        limit: Maximum number of processes to return, between 1 and 50.
    """
    # Clamp rather than reject. A model that asks for 5000 results is not being
    # malicious, it just does not know the limit; silently doing the sensible
    # thing beats an error the model has to recover from.
    limit = max(1, min(limit, 50))
    needle = (name or "").strip().lower()

    matches: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            proc_name = proc.info["name"] or ""
            if needle and needle not in proc_name.lower():
                continue
            mem_info = proc.info["memory_info"]
            matches.append(
                ProcessInfo(
                    pid=proc.info["pid"],
                    name=proc_name,
                    memory_mb=round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Processes die between the iterator yielding them and us reading
            # them. On Windows, many system processes simply refuse inspection.
            continue

    matches.sort(key=lambda p: p.memory_mb, reverse=True)

    return ProcessMatches(
        query=name,
        total_matches=len(matches),
        returned=min(len(matches), limit),
        processes=matches[:limit],
    )
```

The schema says `limit` is an integer. It does not say the integer must be between 1 and 50, so the docstring says it and the first line of the body enforces it. Clamping rather than rejecting is a deliberate choice: a model that asks for five thousand results is not attacking anyone, it simply does not know the limit, and quietly doing the sensible thing costs one round trip less than an error the model has to recover from. The project's test `test_find_process_clamps_the_limit` asks for 9,999 and checks that at most 50 come back.

The `try` block matters more than it looks. Processes vanish between the iterator yielding them and the code reading them, and on Windows a good number of system processes refuse inspection outright. A tool that raises on the first `AccessDenied` works on the author's machine and fails on the reader's.

Two supporting dataclasses, declared in `src/system_info/tools.py` between `SystemSnapshot` and the first tool, carry that result. `total_matches` is separate from `returned` on purpose, so a caller can tell "there were three" from "there were three hundred and these are the first twenty-five":

```python
@dataclass
class ProcessInfo:
    """One matching process."""

    pid: int
    name: str
    memory_mb: float


@dataclass
class ProcessMatches:
    """The result of a process search."""

    query: str
    total_matches: int
    returned: int
    processes: list[ProcessInfo]
```

## 6. Connecting it to a host

A host that speaks stdio starts the server as a child process. All it needs is a command and its arguments:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/05-first-server", "run", "python", "-m", "system_info"]
}
```

Which file that JSON goes in, and which top-level key it sits under, differs from host to host. Claude Desktop reads `claude_desktop_config.json` and nests servers under `mcpServers`; other hosts use a file called `mcp.json` with a similar but not identical shape. [Post 23](../23-multi-client/index.md) has the full matrix. For Claude Desktop the file is at `%APPDATA%\Claude\claude_desktop_config.json` on Windows and `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, the same directories the SDK's own `mcp install` command looks in, and if the file does not exist it is created by hand.

A complete Windows example, with the two things beginners get wrong already fixed:

```json
{
  "mcpServers": {
    "system-info": {
      "command": "C:\\Users\\yourname\\.local\\bin\\uv.exe",
      "args": [
        "--directory",
        "C:\\Users\\yourname\\code\\05-first-server",
        "run",
        "python",
        "-m",
        "system_info"
      ]
    }
  }
}
```

The key `system-info` under `mcpServers` is the host's own name for this connection. It happens to equal the name the server reports, but it does not have to, and it is the name to look for in the host's interface and log files.

**Both paths are absolute, and `command` is the absolute path to `uv` itself.** A desktop host is a graphical application. It does not inherit the environment a terminal builds from the shell profile, so the `PATH` addition that put `uv` on the command line very likely does not exist in the host's environment. Find the real path and paste it in:

```powershell
# Windows
Get-Command uv | Select-Object -ExpandProperty Source
```

```bash
# macOS and Linux
which uv
```

**Backslashes are doubled.** `\` starts an escape sequence in JSON, so a Windows path written with single backslashes is either invalid JSON or a path to somewhere else.

There is also an automatic route: the SDK ships a command, `mcp install`, that edits the Claude Desktop configuration itself. It is built for single-file servers, and section 10 shows what it writes when it is pointed at this package instead. For a package, write the configuration by hand as above; section 8 has a second reason to prefer doing so.

Then quit the host completely and start it again. Not close the window: **quit**. On Windows that means right-clicking the icon in the system tray and choosing Quit; on macOS it means Cmd+Q. A host reads its configuration at startup, and a window that looks closed is often a process that is still running.

## 7. Watching the messages go past

No host is needed to know whether the server works. A pipe is enough.

This is a real exchange, produced by `snippets/stdio_by_hand.py`. It starts `python -m system_info`, the same command the host configuration runs, writes one line to the server's standard input, and reads one line from its standard output. No SDK client is involved on that side: the request is a dictionary and the transport is two pipes.

Written to the server's standard input:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_system_info", "arguments": {}, "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientInfo": {"name": "hand", "version": "1.0.0"}, "io.modelcontextprotocol/clientCapabilities": {}}}}
```

Read back from the server's standard output:

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"{\n  \"cpu_percent\": 22.7,\n  \"memory_percent\": 80.2,\n  \"memory_used_gb\": 6,\n  \"memory_total_gb\": 7,\n  \"disk_percent\": 91.5,\n  \"disk_used_gb\": 358,\n  \"disk_total_gb\": 391\n}","type":"text"}],"isError":false,"resultType":"complete","structuredContent":{"cpu_percent":22.7,"memory_percent":80.2,"memory_used_gb":6,"memory_total_gb":7,"disk_percent":91.5,"disk_used_gb":358,"disk_total_gb":391},"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"system-info","version":""}}}}
```

The numbers are one reading of the author's machine and differ on every run; the shape does not.

Sit with how little happened there. One line in, one line out, newline delimited. No handshake, no session, no `initialize`. The request carries the two `_meta` keys the specification requires on every request, `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities`, plus `io.modelcontextprotocol/clientInfo`, which it says a client **SHOULD** send; [post 03](../03-wire-protocol/index.md) covered all three. The result carries `resultType: "complete"`, both a text block and `structuredContent`, the latter because `get_system_info` declares a return type, and a `_meta` of its own whose `io.modelcontextprotocol/serverInfo` entry names the server, the entry the Base protocol page says a server **SHOULD** put in every result. Its `version` is empty because `app.py` passes none to `MCPServer`, which takes an optional `version` argument; a server worth versioning passes its own. When the script closes standard input the server exits with code 0, and standard output carried nothing but the responses. Standard error carried one line, the SDK's own `INFO` record of the misspelled tool name further down (`Tool 'get_sysinfo' failed: 'Unknown tool: get_sysinfo'`), which is on the right stream.

Leave a required key out and the server refuses, as the specification's Base protocol page says it must, with `-32602` (`Invalid params`):

```json
{"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"params._meta must be an object carrying the required 'io.modelcontextprotocol/protocolVersion' and 'io.modelcontextprotocol/clientCapabilities' envelope keys"}}
```

The message names only the two required keys. The same script sends a `tools/list` whose `_meta` carries both and no `clientInfo`, and gets the tool list back as a result.

An unknown method gets a protocol error, `-32601` (`Method not found`), rather than silence:

```json
{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"Method not found","data":"tools/frobnicate"}}
```

One honest divergence to note in passing. The specification's Tools page lists an unknown tool among the protocol errors and prints its example as `-32602`. The SDK, at release 2.3.0, answers a `tools/call` for a misspelled tool name with a successful response instead:

```json
{"jsonrpc":"2.0","id":5,"result":{"content":[{"text":"Unknown tool: get_sysinfo","type":"text"}],"isError":true,"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"system-info","version":""}}}}
```

Arguments that fail validation come back the same way, as section 5 showed, and for those the Tools page agrees: it lists input validation errors among the tool execution errors that belong in a result with `isError: true`, because a model can read such a result and try again, although the schema's own comment on `-32602` still lists invalid tool arguments. [Post 06](../06-tools-in-depth/index.md) takes the two channels apart properly.

For a quick look at the whole surface, the no-SDK client from [post 03](../03-wire-protocol/snippets/raw_discover.py) works against this server as soon as it is started over Streamable Hypertext Transfer Protocol (HTTP). In two terminals, the first inside `code/05-first-server/` and the second at the series root, whose environment carries the `httpx<1` pin that post 03 explains:

```bash
uv run python -m system_info --http
```

```bash
uv run python posts/03-wire-protocol/snippets/raw_discover.py http://127.0.0.1:8000/mcp
```

`snippets/http_discover.py` does both in one process, on a free port, and the client prints:

```
server        system-info, version ""
serverInfo    in result._meta of server/discover and of tools/list
versions      2026-07-28
capabilities  completions, prompts, resources, tools
cacheable     0 ms, scope private
_meta         183 bytes on every request

4 tools, cacheable 0 ms, scope private
  get_system_info          required: nothing
  find_process             required: name
  terminate_process        required: pid
  watch_cpu                required: nothing
```

Four tools rather than two, because that run was against the finished project from posts 05 through 09. Three lines in that output are worth explaining now.

The `versions` line is what `server/discover` reported, not the outcome of a negotiation. The server states what it supports and the client picks; there was no handshake.

The version on the `server` line is the empty string the stdio result above carried, for the same reason: nothing passed `version` to `MCPServer`.

The `serverInfo` line is post 03's client noticing where that name and version arrived: in the `_meta` of each result, where the Base protocol page puts them, and not as a field of the `server/discover` result itself.

Over HTTP, the missing-`_meta` request above comes back as `HTTP 400 Bad Request` with the same JSON-RPC body, and the unknown method as `HTTP 404 Not Found`, the two statuses the Base protocol page assigns to those errors; `snippets/http_discover.py` prints both.

Finally, the fastest feedback loop of all is a test. The project's suite connects a client straight to the server object with no subprocess and no socket. Inside `code/05-first-server/`:

```bash
uv run --extra dev pytest
```

```
tests\test_server.py ...................                                 [100%]

============================= 19 passed in 5.59s ==============================
```

Nineteen tests covering the whole server across posts 05 to 09, going through the real protocol path. The count is the result; the seconds vary by machine. [Post 12](../12-testing-and-debugging/index.md) is about that pattern in full.

## 8. Troubleshooting, honestly

When a first server does not appear in the host, the causes are few enough to enumerate. Work down this tree rather than guessing.

![A decision tree for a server that does not appear in a host. The first question is whether the server runs standalone in a terminal; if not, the fix is to read the traceback. Then, whether the command in the configuration is an absolute path; then whether the configuration file is valid JSON without a byte order mark; then whether the host was fully quit and restarted with no leftover processes; then whether anything in the server writes to standard output. Each terminal node names a specific fix, and the last one directs the reader to the host's own MCP log file.](diagrams/03-troubleshooting-tree.svg)

*Five questions in order. Each one eliminates a whole class of cause.*

### Does the server run at all?

```bash
uv run python -m system_info
```

It should wait, reading standard input. If it exits immediately, there is a traceback, and that traceback is the entire problem. `ModuleNotFoundError: No module named 'mcp'` means the dependencies are not installed; the `mcp.server.fastmcp` error of section 10 means the code was written for an older SDK.

### Is the command an absolute path?

This failure can leave no error message where anyone is looking: the server simply never shows up. A graphical host does not have the shell's `PATH`, so `"command": "uv"` may resolve to nothing. Use the full path from `Get-Command uv` or `which uv`.

The same applies to the project directory. Relative paths in the configuration are resolved against a working directory the reader does not control.

### Is the configuration file valid JSON, with no byte order mark?

Two failures hide here. The first is ordinary: a trailing comma, a missing brace, a single backslash in a Windows path.

The second is nastier. A file can begin with a **byte order mark** (BOM), a few invisible bytes that announce the encoding. Windows PowerShell 5.1 writes a UTF-8 (Unicode Transformation Format, 8-bit) one, the three bytes `EF BB BF`, whenever it is told `-Encoding UTF8`, and its `>` redirection writes UTF-16 (the 16-bit form) with the two-byte mark `FF FE`. Neither is visible in most editors. A program that reads the file as UTF-8 text and then parses it sees a first character that is not `{` and rejects the whole file, so no server loads, not only this one. `snippets/bom_check.py` shows Python doing exactly that:

```
utf-8 with BOM   first bytes EF BB BF 7B
                 read as UTF-8 text: JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
utf-16 with BOM  first bytes FF FE 7B 00
                 read as UTF-8 text: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte
```

The same script shows that Python's `json` accepts all three files when handed raw bytes, because it sniffs the encoding first. Tolerance is a property of the parser, not of the file, and a host's parser is not something to bet on. Check the first bytes:

```powershell
# Should begin 7B ({), not EF BB BF or FF FE
Format-Hex "$env:APPDATA\Claude\claude_desktop_config.json" | Select-Object -First 2
```

And rewrite without a mark if one is there:

```powershell
$path = "$env:APPDATA\Claude\claude_desktop_config.json"
$content = Get-Content $path -Raw
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
```

`Get-Content` honors either mark when it reads, so the same four lines repair a UTF-8 file and a UTF-16 one.

### Did the host really restart?

A host reads its configuration at startup. Closing the window is not quitting. On Windows in particular, a closed window can leave the process running in the system tray, and launching the host again then brings back that surviving instance, which has not re-read its configuration. Nothing in the interface says this is what happened.

Check the task manager for processes that should not be there, and if in doubt end the desktop host's own. The process is named `claude`, and so is the command-line tool Claude Code, so match the desktop application by its install path rather than by name alone:

```powershell
Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*AnthropicClaude*" } | Stop-Process -Force
```

```bash
# macOS
pkill -9 Claude
```

Then start the host again from scratch.

### Does anything in the server write to standard output?

The rule from [post 04](../04-transports/index.md), restated because this is where it bites: **under stdio, never write to standard output.** Not `print()`, not a stray debugger call, not a library's banner. Standard output is where JSON-RPC frames live, and anything else on it is a parse failure.

`snippets/stray_print.py` builds the smallest server that breaks the rule in two places, a flushed `print()` inside the tool and one at import time, before `mcp.run()` starts the transport, and drives each with the SDK's own stdio client:

```python
    if where == "import":
        print("noisy server starting", flush=True)  # the bug: stdout is the protocol channel

    @mcp.tool()
    def answer() -> str:
        """Return the answer."""
        if where == "tool":
            print("about to answer", flush=True)  # the bug: stdout is the protocol channel
        return "42"
```

The `print()` inside the tool does no harm under this SDK. The client logged nothing, and the line turned up on the server's standard error instead:

```
what the client logged:
    (nothing)
what the server wrote to stderr:
    about to answer
```

Once `mcp.run()` has started the stdio transport, the SDK serves the protocol from a private duplicate of standard output and points the process's own standard output at standard error, so a handler cannot reach the wire. Nothing protects a `print()` that runs before that, and the import-time one lands on the wire:

```
what the client logged:
    ERROR:mcp.client.stdio:Failed to parse JSONRPC message from server
    pydantic_core._pydantic_core.ValidationError: 1 validation error for union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
      Invalid JSON: expected ident at line 1 column 2 [type=json_invalid, input_value='noisy server starting\r', input_type=str]
```

The `\r` is Windows: `print()` in text mode ends the line with a carriage return and a line feed, and the client split on the line feed.

One honest note. That client logged the failure and kept going, and the tool call still returned `'42'` with `isError` false. How gracefully a client recovers from a garbage frame is not specified, so no server should rely on it, and the diversion is this SDK's courtesy rather than the protocol's rule: the Transports page's **MUST NOT** binds the server whichever library it is built on.

The defense is the `logging.basicConfig(stream=sys.stderr, ...)` call already in `app.py`, from section 3. Every diagnostic goes through `log.info(...)` and lands on standard error, where the host keeps it.

### One symptom that is not on the tree

A host that reports it could not connect to a server, or gave up waiting for it, after all five checks pass is describing the opposite of everything above: the configuration was read, the process was launched, and it did not answer in time. The server is fine and its startup is too slow.

The usual cause is dependency resolution. The entry `mcp install` writes, printed in section 10, runs `uv run --frozen --with mcp[cli]==2.3.0 mcp run <file>`. The SDK's own source explains that `uv run --with` resolves that requirement in a fresh environment rather than in the project's, so the first launch can mean downloading packages while the host waits, and on a slow connection the host gives up first. The fix is the manual configuration from section 6, which runs inside the project's already-synced environment. Run `uv sync` once in the project so that nothing needs fetching at launch.

### Reading the host's log

When the tree runs out, the host has written down what happened. Claude Desktop keeps one log file per server, named after the configuration key:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Claude\logs\mcp-server-system-info.log` |
| macOS | `~/Library/Logs/Claude/mcp*.log` |

```powershell
Get-Content "$env:APPDATA\Claude\logs\mcp-server-system-info.log" -Tail 30
```

That file is where the server's stderr ends up, which is the whole reason section 3 spent a paragraph on `logging.basicConfig`.

## 9. Make it run: the project and the scripts behind this post

The project is the post's code. From inside `code/05-first-server/`:

```bash
uv sync --extra dev                         # once: the environment, with the test tools
uv run python -m system_info                # stdio, what a desktop host spawns; Ctrl+C stops it
uv run python -m system_info --http         # Streamable HTTP on 127.0.0.1:8000
uv run --extra dev pytest                   # 19 passed
```

Seven scripts under `snippets/` produce every schema, transcript and error message quoted above. Each runs from the series root with no arguments, in a few seconds, through the root environment that pins `mcp[cli]==2.3.0`, `psutil` and `httpx<1`. Four of them import or start the project under `code/05-first-server/` themselves; every one stops whatever it started before it exits, and none needs a host or any network beyond 127.0.0.1.

```bash
uv run python posts/05-first-server/snippets/tool_surface.py         # sections 4 and 5: tools/list, inputSchema, a missing argument
uv run python posts/05-first-server/snippets/sdk_errors.py           # sections 5 and 10: an unannotated parameter, the SDK's errors
uv run python posts/05-first-server/snippets/stdio_by_hand.py        # section 7: five requests over stdio, by hand
uv run python posts/05-first-server/snippets/http_discover.py        # section 7: raw_discover.py, HTTP 400 and 404
uv run python posts/05-first-server/snippets/bom_check.py            # section 8: a byte order mark and a strict parse
uv run python posts/05-first-server/snippets/stray_print.py          # section 8: one print() in a tool, one at import
uv run python posts/05-first-server/snippets/mcp_install_sandbox.py  # section 10: mcp install in a throwaway home
```

`stray_print.py` is both halves of its experiment: with no argument it is the client, and it starts itself again with `--serve tool` and then `--serve import` as the broken stdio server, flags that are not meant to be run by hand. `mcp_install_sandbox.py` points `USERPROFILE`, `HOME` and `XDG_CONFIG_HOME` at a temporary directory, so the real Claude Desktop configuration is never touched.

## 10. What can go wrong?

Four mistakes stop a first server before any message is exchanged. `snippets/sdk_errors.py` and `snippets/mcp_install_sandbox.py` print each of them.

**Writing `@mcp.tool` without parentheses.** This is an easy mistake to make, and the SDK checks for it at import:

```
@mcp.tool
    TypeError: The @tool decorator was used incorrectly. Did you forget to call it? Use @tool() instead of @tool
```

The server exits instead of starting, and a host reports only that the process died. The same guard is on `@mcp.resource` and `@mcp.prompt`, each naming its own fix (`Use @resource('uri') instead of @resource`, `Use @prompt() instead of @prompt`).

**Code written for the 1.x SDK.** The module the 1.x tutorials import their server class from is gone in 2.x, and the error says what replaced it:

```
import mcp.server.fastmcp
    ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was renamed to MCPServer (from mcp.server.mcpserver import MCPServer) and other APIs changed; see the migration guide at https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver or pin 'mcp<2' to keep running v1 code.
```

The other 1.x import, `mcp.types`, still works: in 2.x it is a compatibility namespace that mirrors the separate `mcp_types` package, and the same script prints `mcp.types.Tool is mcp_types.Tool: True`. So only the `fastmcp` import fails at once. Code written for 1.x was written against the previous protocol revision as well as the previous library, so its message shapes are out of date too, not just its imports.

**Handing transport settings to the constructor.** `host` and `port` are arguments of `run()`:

```
MCPServer("x", port=9000)
    TypeError: MCPServer.__init__() got an unexpected keyword argument 'port'
```

**Pointing `mcp install` at a package.** `uv run mcp install path/to/server.py` takes one file, loads it as a standalone module with the file's directory on the import path, and looks for a global named `mcp`, `server`, or `app` to read the server's name from. This project is a package whose modules import each other with relative imports, so that load fails. The command catches the import failure, falls back to the file's own stem for the name, and writes the entry anyway. Pointed at `__main__.py` in a throwaway home directory, it exits successfully, prints nothing, and writes:

```
exit code 0; terminal output: 0 characters
server entries written: ['__main__']
```

with a command that runs `mcp run` on that same file. The import error waits until the host runs it, which is the one place nobody is watching; run by hand, the `mcp run` half of that command ends with exit code 1 and this line:

```
ImportError: attempted relative import with no known parent package
```

That is not a bug in either the command or the layout; they simply want different things. `mcp install` is for single-file servers, and a package gets its configuration written by hand, as in section 6.

## Common pitfalls

1. **Putting `"uv"` rather than the absolute path to `uv` in the configuration.** A desktop host is a graphical application with no shell profile and therefore no `PATH` additions, and this fails silently: the server just never appears.
2. **Single backslashes in a Windows path inside JSON.** `\t` is an escape sequence and `\U` is invalid JSON, so double every backslash, or use forward slashes, which Windows accepts.
3. **Closing the host window instead of quitting the host.** The surviving process does not re-read its configuration; end it and start the host again.
4. **Debugging with `print()`.** Under stdio, standard output is the protocol channel, and the SDK diverts only what runs after `mcp.run()` starts; configure `logging` to `stderr` once in the first module the package imports.
5. **Assuming a tool's schema is enforcement.** `limit: int = 25` guarantees an integer, nothing more; the bounds live in the docstring for the model and in the first line of the function body for everyone else.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `Tool` object, `inputSchema`, annotations as untrusted hints, and the split between a protocol error and a tool execution error. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Transports"*, revision 2026-07-28. The stdio transport, including the rule that the server must not write anything to standard output that is not a valid MCP message. <https://modelcontextprotocol.io/specification/draft/basic/transports/stdio>
- Specification, *"Base protocol"*, revision 2026-07-28. The required `_meta` keys, the rule that a request missing one is rejected with `-32602`, and the error codes with their HTTP statuses. <https://modelcontextprotocol.io/specification/draft/basic>
- MCP Python SDK, release 2.3.0. `MCPServer`, `@mcp.tool()`, `run()`, and the `mcp install` command. <https://github.com/modelcontextprotocol/python-sdk>
- Astral, *uv documentation*. `uv init --package`, `uv add`, `uv lock`, `uv sync` and `uv run --directory`. <https://docs.astral.sh/uv/>
- Claude Desktop, *"Getting started with local MCP servers on Claude Desktop"*. Where the configuration file and the per-server logs live. <https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop>
- [code/05-first-server/](../../code/05-first-server/). The complete project, with the tests run in section 7.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 06 - Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md):** the two fields this post showed and did not explain, and why schema design decides whether a model calls a tool correctly.
- **[Post 10 - Building your own MCP client](../10-mcp-client/index.md):** the other end of the pipe that section 7 drove by hand, written properly.
