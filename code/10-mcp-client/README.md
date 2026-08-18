# mcp-host

The client and the host, built across posts 10 and 11. Post 10 writes a client
from the transport up; post 11 wraps it in a loop with a model and a permission
gate.

It is a few hundred lines, which is the point of writing one: afterwards every
host behavior you see in Claude Desktop or an IDE is explicable.

| File | Subject | Post |
|---|---|---|
| `src/mcp_host/connection.py` | stdio, Streamable HTTP, in-memory; and lifetimes | [10](../../posts/10-mcp-client/index.md) |
| `src/mcp_host/results.py` | every content block, and the `isError` branch | [10](../../posts/10-mcp-client/index.md) |
| `src/mcp_host/interactive.py` | answering an `input_required` request | [10](../../posts/10-mcp-client/index.md) |
| `src/mcp_host/catalog.py` | many servers, one namespace | [11](../../posts/11-building-a-host/index.md) |
| `src/mcp_host/permissions.py` | the gate | [11](../../posts/11-building-a-host/index.md) |
| `src/mcp_host/providers.py` | the `LLMProvider` protocol | [11](../../posts/11-building-a-host/index.md) |
| `src/mcp_host/loop.py` | the tool-execution loop | [11](../../posts/11-building-a-host/index.md) |

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`, the pre-release that
implements protocol revision 2026-07-28. The 1.x line has a different module
layout and none of the client code here would import against it: `mcp.types` is
now `mcp_types`, `streamablehttp_client` is now `streamable_http_client`, and
`ClientSession(read, write)` is now `Client(...)`.

## Configure

Servers are declared in the same `mcpServers` shape desktop hosts use, so a
config that works here mostly works there. Copy the example and edit the paths:

```bash
cp servers.example.json servers.json
```

```json
{
  "mcpServers": {
    "system-info": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/05-first-server", "python", "-m", "system_info"]
    },
    "remote": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

Use absolute paths. The process that spawns your server does not share your
working directory.

## Run

```bash
uv run python -m mcp_host list                  # connect, show the catalog
uv run python -m mcp_host inspect system-info   # one server's full surface
uv run python -m mcp_host call get_system_info  # one tool, through the gate
uv run python -m mcp_host demo                  # the tool loop, no API key
uv run python -m mcp_host chat                  # the tool loop, interactive
```

`demo` is the one to run first. It drives the full loop -- catalog, gate,
parallel execution, results fed back -- with a scripted provider that needs no
API key and makes no network calls.

For `chat` against a real model, install the extra and set two environment
variables:

```bash
uv sync --extra anthropic
export ANTHROPIC_API_KEY=...
export MCP_HOST_MODEL=<the model id you want>
uv run python -m mcp_host chat --provider anthropic
```

**There is no model id in this repository, and that is deliberate.** Model ids
are retired on a schedule; one hardcoded in a tutorial is a 404 with a delay
fuse. `MCP_HOST_MODEL` has no default, and the provider says so when it is
missing.

## Test

```bash
uv sync --extra dev
uv run pytest
```

The post 05 server is a dev dependency of this project, declared in
`pyproject.toml` and resolved from `../05-first-server`, so `uv sync` is all the
setup there is; there is no `PYTHONPATH` to remember.

68 passed, a few seconds, no network and no API key. Every test that needs a
server connects to `code/05-first-server` **in memory** -- `Client(server)` with
no subprocess and no socket, going over the real protocol path. Post 12 explains
the pattern.

Two things worth knowing about the test files:

- Each test opens its client with `async with` in the test body rather than
  taking it from a yield fixture. The client owns an anyio task group, and a
  task group must be exited by the task that entered it; a yield fixture tears
  down elsewhere and every test fails with a cancel-scope error.
- `test_permissions.py` is the security specification. Start with
  `test_a_lying_read_only_annotation_is_still_only_a_hint`.

## Five things this code does on purpose

**A failed tool is a successful response.** `call_tool` raises only when the
transport or the protocol failed. A tool that ran and failed returns a normal
result with `is_error=True`. A `try/except` around the call does not see it, so
`results.py` checks the flag explicitly and every failure -- raised or returned
-- reaches the caller as the same `ToolOutcome.ok is False`.

**Text is not the only content block.** The union has five members: text,
image, audio, resource link, embedded resource. Reading `.text` off each block
silently drops four of them, and `structured_content` -- the machine-readable
form of the same answer -- is a separate field again. `describe_block` handles
all five, plus a placeholder for block types a future server might send.

**Lifetimes are `AsyncExitStack`, never hand-rolled.** The v2 `Client` wraps an
anyio task group, and a task group must be exited by the task that entered it.
The 1.x-era pattern of storing `__aenter__` results on `self` and unwinding them
in a `close()` method fails two ways: a cancel-scope error when `close()` runs
in a different task, and a leaked subprocess whenever the session exit raises
before the transport exit is reached.

**Nothing blocks the event loop.** Every terminal read goes through
`asyncio.to_thread`, and so does the synchronous provider SDK. A bare `input()`
inside a coroutine stalls every in-flight call and every timeout in the process.

**The gate is on the critical path.** There is no code path from a model's tool
call to a server that does not pass through `PermissionGate.check`. Annotations
like `readOnlyHint` are claims made by the server you are trying to contain;
they can satisfy a rule the host chose to have, and they can never be the rule.
Anything marked `destructiveHint` prompts every time, and "allow always" on a
destructive tool is not remembered by default.

## Console output is ASCII only

A checkmark or an arrow printed to a Windows console under cp1252 raises
`UnicodeEncodeError` from inside `print`, which makes the traceback point at
your output code rather than at the glyph. `[ok]` and `->` render everywhere.
