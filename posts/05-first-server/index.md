# 05 · Your first MCP server

> **TL;DR.** A Model Context Protocol (MCP) server is an ordinary Python file: a server
> object, a decorated function, and a return value. The protocol machinery lives entirely
> inside the software development kit (SDK), so your real work is deciding what a tool
> returns and making sure the host can start your process. This post builds a
> system-information server with two tools, shows the JSON that crosses the wire before the
> decorator that produces it, and finishes on the failures that actually happen.
>
> **After reading this you will be able to:**
> - Create and pin a Python project that a desktop host can spawn.
> - Register a tool with `@mcp.tool()` and read the input schema the SDK generated from your type hints.
> - Drive your own server by hand over standard input and output, one JSON line in and one JSON line out.
> - Diagnose a server that does not appear in a host, in the order that finds the fault fastest.

![A host process on the left spawns a Python server process on the right. Three labeled channels run between them: the host writes JSON-RPC requests to the server's standard input, the server writes JSON-RPC responses back on standard output, and the server writes human-readable log lines on standard error, which the host files away in a log rather than parsing.](diagrams/01-your-file-and-the-host.svg)
*The host starts your file as a child process, and three streams do all the work.*

---

## 1. What we are building, and why it is a good first server

Every explanation so far has been about messages. [Post 03](../03-wire-protocol/index.md)
took the JavaScript Object Notation Remote Procedure Call (JSON-RPC) envelope apart field
by field, and [Post 04](../04-transports/index.md) followed those bytes down to pipes and
sockets. This post is the first one where you write something that answers them.

We are building a **system-information server**: a small process that reports on the
machine it runs on. Two tools in this post. `get_system_info` takes a snapshot of central
processing unit (CPU), memory, and disk usage. `find_process` searches running processes by
name. Later posts in Part II add a resource, a prompt, a tool that asks for confirmation,
and a tool that reports progress, all to the same server.

It is a good first server for four reasons, and none of them is that it is impressive.

**It needs no credentials.** No application programming interface (API) key, no database,
no network. Nothing between you and the protocol.

**The data is genuinely live.** A tool that returns a constant teaches you nothing about
whether the call really happened. When your CPU number changes between two calls, the round
trip is real.

**It has one tool with no arguments and one tool with two.** That is the smallest pair that
shows how a Python signature becomes a JSON Schema, which is section 5 and the single most
useful thing in this post.

**It is honest about privacy.** Process names and identifiers are real information about
your machine. Whatever your server returns goes into the conversation, and if the host uses
a cloud model, into a request to that model's provider. That is worth internalizing on a
harmless server, before you build one that touches something that matters.

The complete project lives in [code/05-first-server/](../../code/05-first-server/). This
post writes two of its files, `src/system_info/app.py` and `src/system_info/tools.py`.

## 2. Setup: a project, not a script

You need Python 3.10 or newer and [uv](https://docs.astral.sh/uv/), the package manager the
MCP tooling assumes. Check both before you start.

```bash
python --version
uv --version
```

Create the project:

```bash
mkdir mcp-system-info
cd mcp-system-info
uv init
uv add --prerelease=allow "mcp[cli]==2.0.0b2" psutil
```

That flag is where this project first stops if you leave it off. Naming a pre-release
exactly is enough for the package you asked for, and it is the reason the pin is `==` rather
than `>=2,<3`. It is not enough for `mcp-types==2.0.0b2`, which `mcp` depends on and you
never named, so the resolver refuses to reach for it on your behalf:

```
× No solution found when resolving dependencies
╰─▶ Because there is no version of mcp-types==2.0.0b2 and mcp==2.0.0b2 depends on
    mcp-types==2.0.0b2, we can conclude that mcp==2.0.0b2 cannot be used.

    hint: `mcp-types` was requested with a pre-release marker (e.g.,
    mcp-types==2.0.0b2), but pre-releases weren't enabled (try: `--prerelease=allow`)
```

You need it exactly once. `uv add` writes a `uv.lock`, and every later `uv run` and `uv sync`
reads that lock rather than resolving again.

That pin is not decoration. This series targets protocol revision **2026-07-28**, and the
2.0 line of the Python SDK is the first that speaks it. The 1.x line has a completely
different module layout, so a floating dependency will not merely behave differently, it
will fail at import:

```bash
$ python -c "import mcp.server.fastmcp"
ModuleNotFoundError: No module named 'mcp.server.fastmcp'

$ python -c "import mcp.types"
ModuleNotFoundError: No module named 'mcp.types'
```

Both of those modules are gone in 2.x. If a tutorial you find elsewhere imports either one,
it was written for the previous revision, and so was its protocol.

> **A note on the version this series pins.** `2.0.0b2` is a pre-release. When stable 2.0
> ships, widen the pin and drop the flag. Every code listing and every transcript in this
> post was produced against `mcp==2.0.0b2` and `mcp-types==2.0.0b2`.

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

A package rather than a single `server.py`, because a server that grows past one primitive
wants its registrations in separate files, and because a package is what `python -m` can
start. If you are typing along, create only `app.py` and `tools.py` for now, and leave the
later three out of `__init__.py` until the posts that write them.

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
# there is parsed as a JSON-RPC frame, and a stray print() will break the
# connection in a way that is genuinely hard to diagnose. Logging must go to
# stderr, which the host collects and shows you.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("system-info")

mcp = MCPServer("system-info")
```

Three things are happening, and one of them is the most important line in the file.

`MCPServer("system-info")` creates the server. The string is the name the server reports
about itself, and it is the name you will look for in a host's interface and in its log
filenames. It is not a unique identifier: [Post 02](../02-architecture/index.md) covered why
a host must not use a server's self-reported name as a key.

The `logging.basicConfig(stream=sys.stderr, ...)` call is the load-bearing one. Under the
standard input and output transport, which is what a desktop host uses and what
[Post 04](../04-transports/index.md) described in detail, your process's **standard output
is the protocol channel**. The bytes on it are parsed as JSON-RPC frames. Standard error, by
contrast, is yours: hosts collect it into a log file and show it to you when something
breaks. Configuring logging to stderr on the first line of the first module is how you make
sure nothing ever writes to the wrong stream by accident.

Notice what is *not* here: no transport, no port, no handshake, no capability negotiation.
There is nothing to negotiate. In revision 2026-07-28 a client declares its capabilities on
every request and the server declares its own in the result of `server/discover`, and the
SDK answers that method for you.

To actually start it, `src/system_info/__main__.py`:

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

`mcp.run()` is **synchronous**, and its first positional argument is the transport, which
defaults to `"stdio"`. A bare `mcp.run()` therefore serves over standard input and output,
which is exactly what a desktop host spawns. `host` and `port` belong to `run()` and not to
the constructor; `MCPServer("x", port=9000)` raises `TypeError: MCPServer.__init__() got an
unexpected keyword argument 'port'`.

Run it now. It should sit there doing nothing, because nothing has spoken to it yet.

```bash
uv run python -m system_info
```

Press Ctrl+C to stop. A server that exits immediately instead of hanging has a traceback,
and that traceback is your bug.

## 4. The first tool

Before the decorator, the message. This is what a client receives when it calls
`tools/list` against the finished server, trimmed to the one tool, captured from a real
`ListToolsResult` and printed with the wire spellings rather than the Python ones:

```json
{
  "name": "get_system_info",
  "title": "Get system snapshot",
  "description": "Read current CPU, memory, and disk usage for this machine.\n\nReturns a single snapshot taken over a short sampling interval.\n",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "title": "get_system_infoArguments"
  },
  "outputSchema": {
    "properties": {
      "cpu_percent": { "title": "Cpu Percent", "type": "number" },
      "memory_percent": { "title": "Memory Percent", "type": "number" },
      "memory_used_gb": { "title": "Memory Used Gb", "type": "integer" },
      "memory_total_gb": { "title": "Memory Total Gb", "type": "integer" },
      "disk_percent": { "title": "Disk Percent", "type": "number" },
      "disk_used_gb": { "title": "Disk Used Gb", "type": "integer" },
      "disk_total_gb": { "title": "Disk Total Gb", "type": "integer" }
    },
    "required": [
      "cpu_percent", "memory_percent", "memory_used_gb", "memory_total_gb",
      "disk_percent", "disk_used_gb", "disk_total_gb"
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

Read it as the model does. `name` is what gets called. `description` is the only place the
model learns what the tool is for. `inputSchema` says this tool takes no arguments.
`outputSchema` promises the shape of what comes back. `annotations` are hints for the host
about how the tool behaves.

Nothing in that object was written by hand. Here is `src/system_info/tools.py`, the part
that produces it:

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
    get `outputSchema: null` with no warning at all.
    [Post 06](../06-tools-in-depth/index.md) covers this trap.
    """

    cpu_percent: float
    memory_percent: float
    memory_used_gb: int
    memory_total_gb: int
    disk_percent: float
    disk_used_gb: int
    disk_total_gb: int


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

Line it up against the JSON above and every field has a source. The function name became
`name`. The docstring became `description`, whitespace and all. The empty parameter list
became an empty `inputSchema`. The `SystemSnapshot` dataclass became `outputSchema`, one
property per class attribute. `title=` and `annotations=` are the two decorator arguments
used here, and they became `title` and `annotations`.

**`@mcp.tool()` needs its parentheses.** This is the mistake every reader makes once:

```
TypeError: The @tool decorator was used incorrectly. Did you forget to call it? Use @tool() instead of @tool
```

The SDK checks for it explicitly, so at least the error names the fix. The same guard is on
`@mcp.resource("uri")` and `@mcp.prompt()`, each naming its own decorator.

Two details in the body are worth stealing. `psutil.cpu_percent(interval=0.5)` blocks for
half a second and measures; `interval=0` compares against the previous call and returns a
meaningless number the first time. And `pathlib.Path.home().anchor` gives `C:\` on Windows
and `/` on Unix, which is the cheapest way to ask for "the disk this machine boots from"
without branching on the platform.

The `outputSchema` and `annotations` fields both deserve more than the sentence they get
here. [Post 06](../06-tools-in-depth/index.md) is entirely about them, including the trap
where a return class without class-body annotations silently produces `"outputSchema":
null` with no warning at all.

## 5. A tool with arguments, and how a signature becomes a schema

The second tool takes input. Here is the wire form of its `inputSchema`, again captured
from a real `tools/list` result:

```json
{
  "type": "object",
  "properties": {
    "name": { "title": "Name", "type": "string" },
    "limit": { "default": 25, "title": "Limit", "type": "integer" }
  },
  "required": ["name"],
  "title": "find_processArguments"
}
```

And the signature that produced it:

```python
def find_process(name: str, limit: int = 25) -> ProcessMatches:
```

![On the left, the decorated Python function find_process, whose parameters are name annotated as str with no default and limit annotated as int with a default of 25. On the right, the JSON Schema the SDK generated from that signature. A leader line runs from each parameter to the schema property it became. Panels along the bottom state the rule: no default means the parameter is listed in required, and a default means it carries a default keyword and is left out of required.](diagrams/02-signature-to-schema.svg)
*A parameter with a default becomes an optional property; a parameter without one becomes required.*

Four rules, and they cover almost everything you will write.

**The parameter name becomes the property name.** No transformation, no camel-casing.

**The type hint becomes the JSON Schema type.** `str` is `"string"`, `int` is `"integer"`,
`float` is `"number"`, `bool` is `"boolean"`. A parameter with no annotation at all is
schema'd as a string, which is rarely what you meant.

**A default makes a parameter optional.** `limit: int = 25` produced both
`"default": 25` and an absence from `required`. `name: str` has no default, so it is in
`required`, and a client that omits it gets an error instead of a result.

**The docstring becomes the description.** Including the argument documentation, which is
where the model learns things the schema cannot express.

That last point is why the real function documents its bounds in prose:

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

The schema says `limit` is an integer. It does not say the integer must be between 1 and 50,
so the docstring says it and the first line of the body enforces it. Clamping rather than
rejecting is a deliberate choice: a model that asks for five thousand results is not
attacking you, it simply does not know the limit, and quietly doing the sensible thing costs
one round trip less than an error the model has to recover from.

The `try` block matters more than it looks. Processes vanish between the iterator yielding
them and your code reading them, and on Windows a good number of system processes refuse
inspection outright. A tool that raises on the first `AccessDenied` works on your machine
and fails on your reader's.

Two supporting dataclasses carry that result, and `total_matches` is separate from
`returned` on purpose, so a caller can tell "there were three" from "there were three
hundred and you are seeing the first twenty-five":

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

A host that speaks the standard input and output transport starts your server as a child
process. All it needs is a command and its arguments:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/05-first-server", "run", "python", "-m", "system_info"]
}
```

Which file that JSON goes in, and which top-level key it sits under, differs from host to
host. Claude Desktop reads `claude_desktop_config.json` and nests servers under
`mcpServers`; other clients use a file called `mcp.json` with a similar but not identical
shape. [Post 23](../23-multi-client/index.md) has the full matrix. For Claude Desktop the
file is at `%APPDATA%\Claude\claude_desktop_config.json` on Windows and
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, and if it does
not exist you create it.

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

**Both paths are absolute, and `command` is the absolute path to `uv` itself.** A desktop
host is a graphical application. It does not inherit the environment your terminal builds
from your shell profile, so the `PATH` addition that put `uv` on your command line very
likely does not exist in the host's environment. Find the real path and paste it in:

```powershell
# Windows
Get-Command uv | Select-Object -ExpandProperty Source
```

```bash
# macOS and Linux
which uv
```

**Backslashes are doubled.** `\` starts an escape sequence in JSON, so a Windows path
written with single backslashes is either invalid JSON or a path to somewhere else.

There is also an automatic route, and it comes with a caveat this project runs straight
into. The SDK ships a command-line interface that will edit the Claude Desktop
configuration for you:

```bash
uv run mcp install path/to/server.py
```

It takes **one file**, loads it as a standalone module, and looks for a global named `mcp`,
`server`, or `app`. Our project is a package whose modules import each other with relative
imports, so that load fails. The interesting part is what happens next: the command catches
the failure, falls back to the file's own stem for the server name, and writes the entry
anyway. Point it at `__main__.py` and it exits successfully, having registered a server
called `__main__` whose command runs a file that cannot be imported on its own.

Nothing appears on your terminal. The import error waits until the host runs that command,
which is the one place you cannot see it:

```
ImportError: attempted relative import with no known parent package
```

That is not a bug in either the tool or the layout; they simply want different things.
`mcp install` is for single-file servers. For a package, write the configuration by hand as
above. Section 8 has a second reason to prefer doing it by hand.

Then quit the host completely and start it again. Not close the window: **quit**. On Windows
that means right-clicking the icon in the system tray and choosing Quit; on macOS it means
Cmd+Q. A host reads its configuration once, at startup, and a window that looks closed is
often a process that is still running.

## 7. Watching the messages go past

You do not need a host to know whether your server works. You need a pipe.

This is a real exchange, produced by writing one line to the server's standard input and
reading one line from its standard output. The server was started with
`python -m system_info`, nothing else.

Written to the server's standard input:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_system_info", "arguments": {}, "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientInfo": {"name": "hand", "version": "1.0.0"}, "io.modelcontextprotocol/clientCapabilities": {}}}}
```

Read back from the server's standard output:

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"{\n  \"cpu_percent\": 26.5,\n  \"memory_percent\": 85.5,\n  \"memory_used_gb\": 6,\n  \"memory_total_gb\": 7,\n  \"disk_percent\": 76.9,\n  \"disk_used_gb\": 300,\n  \"disk_total_gb\": 391\n}","type":"text"}],"isError":false,"resultType":"complete","structuredContent":{"cpu_percent":26.5,"memory_percent":85.5,"memory_used_gb":6,"memory_total_gb":7,"disk_percent":76.9,"disk_used_gb":300,"disk_total_gb":391}}}
```

The server's standard error during that exchange was empty.

Sit with how little happened there. One line in, one line out, newline delimited. No
handshake, no session, no `initialize`. The request carries the two required `_meta` keys
that [Post 03](../03-wire-protocol/index.md) covered, and the result carries
`resultType: "complete"` and both a text block and `structuredContent`, the latter because
`get_system_info` declares a return type.

Leave a required key out and a conformant server refuses, exactly as the specification says
it must. Sending `tools/list` with an empty `params` over Streamable Hypertext Transfer
Protocol (HTTP) produced HTTP `400` and this body:

```json
{"jsonrpc":"2.0","id":6,"error":{"code":-32602,"message":"params._meta must carry the reserved protocol-version, client-info and client-capabilities envelope keys"}}
```

An unknown method gets a protocol error rather than silence:

```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found","data":"tools/frobnicate"}}
```

One honest divergence to note in passing. The specification puts a misspelled *tool* name in
the same category, as `-32602`, but this SDK build answers a `tools/call` for an unknown
tool with a successful response carrying `"isError": true` instead, and does the same when
an argument fails validation. That choice is defensible, because a model can read an
`isError` result and try again, and it is what
[Post 06](../06-tools-in-depth/index.md) takes apart properly.

For a quicker look at the whole surface, the no-SDK client from
[Post 03](../03-wire-protocol/snippets/raw_discover.py) works against this server as soon
as you start it over HTTP. Two terminals, and the same pinning habit as the SDK, for the
same reason: [Post 03](../03-wire-protocol/index.md) explains that bound where the script
is written.

```bash
uv run python -m system_info --http
```

```bash
uv run --with 'httpx<1' python raw_discover.py http://127.0.0.1:8000/mcp
```

which prints:

```
server        system-info 2.0.0b2
versions      2026-07-28
capabilities  completions, prompts, resources, tools
cacheable     0 ms, scope private

4 tools
  get_system_info          required: nothing
  find_process             required: name
  terminate_process        required: pid
  watch_cpu                required: nothing
```

Four tools rather than two, because that run was against the finished project from posts 05
through 09. Two lines in that output are worth explaining now.

The `versions` line is what `server/discover` reported, not the outcome of a negotiation.
The server states what it supports and the client picks; there was no handshake.

The version is `2.0.0b2`, which is the **SDK's** version, not your server's. `MCPServer`
takes an optional `version` argument, and when you leave it out the server reports the
version of the library. Pass your own once your server has a version worth reporting.

Finally, the fastest feedback loop of all is a test. The project's suite connects a client
straight to the server object with no subprocess and no socket:

```bash
uv run pytest
```

```
...................                                                      [100%]
19 passed in 7.36s
```

Nineteen tests covering the whole server across posts 05 to 09, in under eight seconds,
going through the real protocol path.
[Post 12](../12-testing-and-debugging/index.md) is about that pattern in full.

## 8. Troubleshooting, honestly

Your server will not appear in the host the first time. That is normal, and the causes are
few enough to enumerate. Work down this tree rather than guessing.

![A decision tree for a server that does not appear in a host. The first question is whether the server runs standalone in a terminal; if not, the fix is to read the traceback. Then, whether the command in the configuration is an absolute path; then whether the configuration file is valid JSON without a byte order mark; then whether the host was fully quit and restarted with no leftover processes; then whether anything in the server writes to standard output. Each terminal node names a specific fix, and the last one directs the reader to the host's own MCP log file.](diagrams/03-troubleshooting-tree.svg)
*Five questions in order. Each one eliminates a whole class of cause.*

### Does the server run at all?

```bash
uv run python -m system_info
```

It should hang, waiting for input on standard input. If it exits immediately, you have a
traceback, and that traceback is the entire problem. `ModuleNotFoundError: No module named
'mcp'` means dependencies are not installed. `ModuleNotFoundError: No module named
'mcp.server.fastmcp'` means you copied code written for the 1.x SDK.

### Is the command an absolute path?

This is the most common failure, and it produces no error message at all: the server simply
never shows up. A graphical host does not have your shell's `PATH`, so `"command": "uv"`
resolves to nothing. Use the full path from `Get-Command uv` or `which uv`.

The same applies to the project directory. Relative paths in the configuration are resolved
against a working directory you do not control.

### Is the configuration file valid JSON, with no byte order mark?

Two failures hide here. The first is ordinary: a trailing comma, a missing brace, a single
backslash in a Windows path.

The second is nastier. Some Windows editors, and PowerShell's own redirection operators,
write a UTF-8 **byte order mark** (BOM) at the start of the file: three bytes, `EF BB BF`,
invisible in every editor. A strict JSON parser sees a leading character that is not `{` and
rejects the whole file, which surfaces as a message about not being able to read settings
and *no* servers loading, not just yours. Check the first bytes:

```powershell
# Should begin 7B ({), not EF BB BF
Format-Hex "$env:APPDATA\Claude\claude_desktop_config.json" | Select-Object -First 2
```

And rewrite without one if it is there:

```powershell
$path = "$env:APPDATA\Claude\claude_desktop_config.json"
$content = Get-Content $path -Raw
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
```

### Did the host really restart?

A host reads its configuration at startup and never again. Closing the window is not
quitting. On Windows in particular, a closed window frequently leaves the process running,
and the next launch attaches to that surviving instance as a secondary window: the interface
appears, the configuration is never re-read, and no servers load. Nothing in the user
interface tells you this is what happened.

Check the task manager for processes you did not expect, and if in doubt kill them all:

```powershell
Stop-Process -Name "claude" -Force
```

```bash
# macOS
pkill -9 Claude
```

Then start the host again from scratch.

### Does anything in your server write to standard output?

The rule from [Post 04](../04-transports/index.md), restated because this is where it bites:
**under the standard input and output transport, never write to standard output.** Not
`print()`, not a stray debugger call, not a library's banner. Standard output is where
JSON-RPC frames live, and anything else on it is a parse failure.

Here is what one flushed `print("about to answer")` inside a tool did to a real client:

```
ERROR:mcp.client.stdio:Failed to parse JSONRPC message from server
pydantic_core._pydantic_core.ValidationError: 1 validation error for
union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
  Invalid JSON: expected value at line 1 column 1
  [type=json_invalid, input_value='about to answer\r', input_type=str]
```

One honest note. That particular client logged the failure and kept going, and the tool call
still returned. How gracefully a client recovers from a garbage frame is not specified, so
do not rely on any of them doing so. Different hosts fail differently, and the ones that
fail hard fail with an unhelpful message.

The defense is one line, and it is already in `app.py`:

```python
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("system-info")

log.info("Server started")   # stderr, safe, and the host will show it to you
```

### One symptom that is not on the tree

A message about not being able to *attach* to a server is the opposite of everything above:
the configuration was read, the process was launched, and it did not answer in time. Five
yeses and this message means your server is fine and its startup is too slow.

The usual cause is dependency resolution. `mcp install` writes a command of this shape:

```json
{
  "command": "C:\\Users\\yourname\\.local\\bin\\uv.exe",
  "args": ["run", "--frozen", "--with", "mcp[cli]==2.0.0b2", "mcp", "run", "C:\\...\\server.py"]
}
```

`uv run --with ...` resolves that requirement in a **fresh** environment rather than in your
project's. The first time, that means downloading packages while the host sits waiting, and
on a slow connection the host gives up first. The fix is the manual configuration from
section 6, which runs inside your already-synced project environment and starts
immediately. Run `uv sync` once in the project so that nothing needs fetching at all.

### Reading the host's log

When the tree above runs out, the host has written down what happened. Claude Desktop keeps
one log file per server:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Claude\logs\mcp-server-system-info.log` |
| macOS | `~/Library/Logs/Claude/mcp*.log` |

```powershell
Get-Content "$env:APPDATA\Claude\logs\mcp-server-system-info.log" -Tail 30
```

That file is where your stderr ends up, which is the whole reason section 3 spent a
paragraph on `logging.basicConfig`.

---

## Common pitfalls

- **Writing `@mcp.tool` without parentheses.** The SDK raises `TypeError: The @tool
  decorator was used incorrectly` at import, so your server exits instead of starting, and
  the host reports only that the process died. The same applies to `@mcp.resource("uri")`
  and `@mcp.prompt()`.
- **Copying code that imports `mcp.server.fastmcp` or `mcp.types`.** Both modules were
  removed in the 2.x SDK. Any tutorial that uses them is written against an older protocol
  revision as well as an older library, so its message shapes are wrong too, not just its
  imports.
- **Putting `"uv"` rather than the absolute path to `uv` in the configuration.** A desktop
  host is a graphical application with no shell profile and therefore no `PATH` additions.
  This fails silently: the server just never appears.
- **Single backslashes in a Windows path inside JSON.** `\U` and `\t` are escape sequences.
  Double every backslash, or use forward slashes, which Windows accepts.
- **Closing the host window instead of quitting the host.** The surviving process never
  re-reads its configuration, and the next window you open is attached to it. Kill the
  process and start again.
- **Debugging with `print()`.** Under the standard input and output transport, standard
  output is the protocol channel. Configure `logging` to `stderr` once in your entry module
  and never think about it again.
- **Assuming a tool's schema is enforcement.** `limit: int = 25` guarantees you get an
  integer, nothing more. The bounds live in your docstring for the model and in the first
  line of your function body for everyone else.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `Tool` object, `inputSchema`, and the
  distinction between a protocol error and a tool execution error.
  <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Transports"*, revision 2026-07-28. The standard input and output
  transport, including the rule that the server must not write anything to standard output
  that is not a valid MCP message.
- MCP Python SDK, version 2.0.0b2. `MCPServer`, `@mcp.tool()`, and `run()`. Every code
  listing and every transcript in this post was produced against this release.
  <https://github.com/modelcontextprotocol/python-sdk>
- Astral, *"uv documentation"*. `uv init`, `uv add`, `uv sync`, and `uv run --directory`.
  <https://docs.astral.sh/uv/>
- [code/05-first-server/](../../code/05-first-server/). The complete project, with the tests
  quoted in section 7.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 06 — Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md)**:
  the two fields this post showed and did not explain, plus why schema design is the
  difference between a tool a model calls correctly and one it does not.
- **[Post 12 — Testing and debugging MCP](../12-testing-and-debugging/index.md)**: the
  in-memory client behind that nineteen-test run, and how to assert on a schema rather than
  a result.
- **[Post 04 — Transports: stdio and Streamable HTTP](../04-transports/index.md)**: go back
  for the process model if the standard output rule in section 8 still feels arbitrary.
