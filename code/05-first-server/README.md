# system-info

<!-- mcp-name: io.github.ajkumar-13/system-info -->

The server built across posts 05 to 09. It reports on the machine it runs on:
CPU, memory, disk, and processes.

It is deliberately small in scope and complete in protocol surface. Every
server-side primitive the series teaches appears here once:

| File | Primitive | Post |
|---|---|---|
| `src/system_info/app.py` | the server instance, stderr logging | [05](../../posts/05-first-server/index.md) |
| `src/system_info/tools.py` | tools, structured output, annotations | [05](../../posts/05-first-server/index.md), [06](../../posts/06-tools-in-depth/index.md) |
| `src/system_info/resources.py` | static and templated resources, a prompt, completions | [07](../../posts/07-resources-and-prompts/index.md) |
| `src/system_info/interactive.py` | elicitation through `Resolve`, over MRTR | [08](../../posts/08-elicitation-and-mrtr/index.md) |
| `src/system_info/progress.py` | long work and progress reporting | [09](../../posts/09-tasks/index.md) |

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`, the pre-release that
implements protocol revision 2026-07-28. Floating that pin will break the
imports outright: the 1.x line uses `mcp.server.fastmcp` and `mcp.types`, both
of which no longer exist.

## Run

```bash
uv run python -m system_info              # stdio, what a desktop host spawns
uv run python -m system_info --http       # Streamable HTTP on 127.0.0.1:8000
```

Under stdio, **stdout is the protocol channel**. A stray `print()` anywhere in
this package would be parsed as a JSON-RPC frame and break the connection. All
logging goes to stderr, which is set up in `app.py`.

## Test

```bash
uv run pytest
```

The tests connect a client directly to the server object with no subprocess and
no socket, so the whole suite finishes in a few seconds while still exercising
the real protocol path. Post 12 explains the pattern.

Two things worth knowing about the test file:

- Each test opens its client with `async with` in the test body rather than
  taking it from a yield fixture. The client owns an anyio task group, and a
  task group must be exited by the task that entered it; a yield fixture tears
  down elsewhere and every test fails with a cancel-scope error.
- `test_resolved_parameter_is_hidden_from_the_model` is the security test. The
  `terminate_process` tool takes an `approval` parameter that is resolved by
  elicitation, and that parameter must never appear in the published input
  schema. If it did, a model could pass its own approval and the confirmation
  step would be decorative.

## Connecting it to a host

The command a host needs to spawn:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/05-first-server", "run", "python", "-m", "system_info"]
}
```

Where that JSON goes differs per host, and the key it sits under differs too.
Post 23 has the matrix.
