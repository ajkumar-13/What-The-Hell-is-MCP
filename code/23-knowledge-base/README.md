# knowledge-base

The server from post 23: a team's engineering handbook, made searchable, served
over MCP, and connected to six clients without changing a line of server code.

The server is the smaller half of this directory. The configuration files in
[`clients/`](clients/) are the deliverable, because that is where readers
actually get stuck.

| Path | What it is |
|---|---|
| `knowledge/` | The corpus. Five Markdown documents, committed, not generated. |
| `src/knowledge_base/index.py` | Loading and BM25 ranking, hand-written, no dependencies. |
| `src/knowledge_base/tools.py` | `search_docs`, `get_doc`, `list_topics`. |
| `src/knowledge_base/resources.py` | Each document as a resource, plus two templates. |
| `src/knowledge_base/prompts.py` | Three user-invocable prompts, plus completions. |
| `clients/` | One configuration per host, and one client written in Python. |

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`, the pre-release that
implements protocol revision 2026-07-28. Floating that pin breaks the imports
outright: the 1.x line uses `mcp.server.fastmcp` and `mcp.types`, neither of
which exists any more.

Nothing else is installed. The ranking in `index.py` is Okapi BM25 written out
against the standard library, so there is no search dependency to install, pin,
or explain.

## Run

```bash
uv run python -m knowledge_base           # stdio, what a desktop host spawns
uv run python -m knowledge_base --http    # Streamable HTTP on 127.0.0.1:8000/mcp
```

Under stdio, **stdout is the protocol channel**. A stray `print()` anywhere in
this package would be parsed as a JSON-RPC frame and break the connection. All
logging goes to stderr, which is set up in `app.py`.

## Test

```bash
uv run pytest
```

Two files, split by what they can tell you when they fail:

- `tests/test_index.py` imports no MCP at all. When a search result looks wrong,
  this tells you whether the bug is in the ranking.
- `tests/test_server.py` opens an in-memory client against the real server
  object. Each test opens its client with `async with` in the test body rather
  than taking it from a yield fixture: the client owns an anyio task group, a
  task group must be exited by the task that entered it, and a yield fixture
  tears down elsewhere and fails every test with a cancel-scope error.

The two tests at the bottom of `test_server.py` are the ones this post exists
for. See [Graceful degradation](#graceful-degradation).

---

## Where each configuration file goes

The files in `clients/` are named for their host, not for their destination.
Copy the contents to the path below, or merge them into the file already there.

| Client | Scope | macOS | Windows | Linux |
|---|---|---|---|---|
| **Claude Desktop** | global | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` | no official build |
| **Claude Code** | project | `.mcp.json` in the project root | same | same |
| **Claude Code** | user | `~/.claude.json` | `%USERPROFILE%\.claude.json` | `~/.claude.json` |
| **Cursor** | project | `.cursor/mcp.json` in the project root | same | same |
| **Cursor** | global | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` | `~/.cursor/mcp.json` |
| **VS Code** | workspace | `.vscode/mcp.json` in the project root | same | same |
| **VS Code** | user profile | `~/Library/Application Support/Code/User/mcp.json` | `%APPDATA%\Code\User\mcp.json` | `~/.config/Code/User/mcp.json` |
| **Gemini CLI** | project | `.gemini/settings.json` in the project root | same | same |
| **Gemini CLI** | user | `~/.gemini/settings.json` | `%USERPROFILE%\.gemini\settings.json` | `~/.gemini/settings.json` |
| **Zed** | global | `~/.config/zed/settings.json` | `%APPDATA%\Zed\settings.json` | `~/.config/zed/settings.json` |

Notes on the awkward rows:

- **Claude Desktop is macOS and Windows only.** There is no official Linux
  build. Rather than guess at a path for an unofficial package, open the file
  from inside the app: **Settings > Developer > Edit Config**.
- **VS Code does not document its user-profile path**, because a profile other
  than the default lives elsewhere. The reliable route is the command palette:
  **MCP: Open User Configuration**. The paths above are the default profile.
- **Zed's own instruction** is the command palette: **zed: open settings file**.

### These files contain no comments, on purpose

VS Code's `mcp.json` and Zed's `settings.json` both tolerate `//` comments in
real life. None of the six files here uses one, so that every file parses with a
plain `json.load` and can be pasted into any of them without a surprise. That is
also why the locations are in this table rather than at the top of each file.

---

## The configuration matrix

Everything a reader gets wrong, in one table.

| | Top-level key | `type` field | stdio | Streamable HTTP | Interpolation |
|---|---|---|---|---|---|
| **Claude Desktop** | `mcpServers` | none | `command` + `args` | not in this file | none |
| **Claude Code** | `mcpServers` | optional for stdio, **required** for remote | `command` + `args` | `"type": "http"` + `url` | `${VAR}`, `${VAR:-default}` |
| **Cursor** | `mcpServers` | optional | `command` + `args` | `url` | `${env:VAR}`, `${workspaceFolder}`, `${userHome}` |
| **VS Code** | `servers` | **required on every entry** | `"type": "stdio"` | `"type": "http"` + `url` | `${workspaceFolder}`, `${userHome}`, `${input:id}` |
| **Gemini CLI** | `mcpServers` | **no such field** | `command` | `httpUrl` | `$VAR`, `${VAR}`, `%VAR%` on Windows |
| **Zed** | `context_servers` | none | `command` + `args` | `url` | none documented |

Six rows, four disagreements. Read them one at a time.

### The top-level key

`mcpServers` for Claude Desktop, Claude Code, Cursor, and Gemini CLI. `servers`
for VS Code. `context_servers` for Zed. There is no protocol reason for this;
the keys were chosen independently before anyone needed them to agree, and none
of them can change now without breaking every existing installation.

### The `type` field

Three different rules.

- **VS Code requires it on every entry**, stdio included.
- **Claude Code makes it optional for stdio and mandatory for remote.** An entry
  with a `url` and no `type` is a configuration error, because Claude Code reads
  a typeless entry as stdio and then finds no `command`. It reports:
  `MCP server "<name>" has a "url" but no "type"; add "type": "http"`. Claude
  Code also accepts `"streamable-http"` as an alias for `"http"`, so a snippet
  copied from a server's own documentation works unmodified.
- **Gemini CLI has no `type` field at all** and infers the transport from which
  key you used. This is the one most likely to cost you an hour:

  | Key | Transport |
  |---|---|
  | `command` | stdio |
  | `url` | SSE (superseded) |
  | `httpUrl` | Streamable HTTP |

  Put a Streamable HTTP endpoint under `url` and Gemini CLI will faithfully try
  to speak SSE to it. Use `httpUrl`.

### Remote servers in Claude Desktop

**You cannot add a remote MCP server by putting a `url` in
`claude_desktop_config.json`.** That file configures local, stdio servers that
Claude Desktop launches as subprocesses. It is not a general server list.

Remote servers are Custom Connectors, added through the interface:

1. **Settings > Connectors** (`Ctrl+Comma`, or the menu icon, then File, then Settings).
2. **Add** at the top right, then **Add custom connector**.
3. Paste the server URL, ending `/mcp`, and complete whatever authentication the
   server asks for.

`clients/claude_desktop_config.json` therefore has one stdio entry and nothing
else, which is the honest shape of that file.

### Interpolation

Five syntaxes for one idea.

| Client | Syntax | Notes |
|---|---|---|
| Claude Desktop | none | Absolute paths only. `${APPDATA}` in a path is a known failure. |
| Claude Code | `${VAR}`, `${VAR:-default}` | Expands in `command`, `args`, `env`, `url`, and `headers`. |
| Cursor | `${env:VAR}` | Plus `${workspaceFolder}`, `${userHome}`, `${pathSeparator}`. |
| VS Code | `${input:id}` | Plus `${workspaceFolder}`, `${userHome}`. Use `inputs` for secrets. |
| Gemini CLI | `$VAR` or `${VAR}` | `%VAR%` also works on Windows. Undefined resolves to empty. |
| Zed | none documented | Write the value out. |

Note the shape of the disagreement: Claude Code's `${VAR}` reads a *process*
environment variable, while VS Code's `${input:id}` prompts the *user* and
Cursor's `${env:VAR}` reads the environment behind an `env:` namespace. A string
copied between two of these files can parse cleanly in both and mean something
different in each.

Every path in `clients/` is a placeholder like
`/absolute/path/to/23-knowledge-base`. Replace it with a real absolute path.
Relative paths are the most common reason a server fails to start, because the
working directory a host launches you from is rarely the one you assumed.

---

## Graceful degradation

"Works with every client" is a marketing claim. The engineering claim underneath
it is narrower and testable: **a client that supports only tools must lose
presentation, not capability.**

Not every host in the table reads resources, and not every host offers prompts.
So this server is built in three layers, most-supported first:

| Layer | Assumed support | What a client without it loses |
|---|---|---|
| `tools.py` | universal | nothing works |
| `resources.py` | common | a menu of attachable documents |
| `prompts.py` | patchy | three slash-command shortcuts |

Every resource maps to a tool that returns the same text, and every prompt is a
shortcut for a sequence of tool calls the model could make itself:

| Resource | Tool that covers it |
|---|---|
| `knowledge://index` | `list_topics()` |
| `knowledge://doc/{slug}` | `get_doc(slug)` |
| `knowledge://section/{slug}/{anchor}` | `get_doc(slug, section)` |

Two tests hold the line:

- `test_every_resource_is_reachable_through_a_tool` walks `resources/list` and
  asserts that some tool call returns identical text for each URI. Add a
  resource without a tool path to it and the suite fails.
- `test_a_tools_only_client_can_still_answer_a_real_question` answers a genuine
  handbook question using nothing but `tools/list` and `tools/call`.

The rule in one line: **put the capability in a tool, then let richer clients
present it more nicely.** Doing it the other way round produces a server that
quietly does less on some hosts than on others, and nobody can tell you why.

`clients/programmatic_client.py` demonstrates the client half of the same idea.
It probes for resources and prompts, prints what it found, and carries on
without them.

---

## Connecting it, host by host

The command every stdio configuration in `clients/` runs:

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/23-knowledge-base", "run", "python", "-m", "knowledge_base"]
}
```

`uv --directory <path> run` is what makes this work from a host that launches
you from an arbitrary working directory: it resolves the project and its virtual
environment from the path you gave rather than from `cwd`.

To serve every host from one process instead, run it once over HTTP:

```bash
uv run python -m knowledge_base --http
```

and point the remote entries at `http://127.0.0.1:8000/mcp`.

**The path is `/mcp`, with no trailing slash.** `/mcp/` is not served. Starlette
answers it with a `307` redirect to `/mcp`, so a client that follows redirects
survives and a client that does not fails, and even the surviving case pays a
wasted round trip on every call.

Two more things before you expose it beyond loopback:

- `--host 0.0.0.0` makes it reachable from your whole network. This server is
  read-only, but "read-only" and "safe to publish" are different claims.
- The SDK arms DNS-rebinding protection automatically for `127.0.0.1`,
  `localhost`, and `::1`. Behind a real hostname you must pass
  `transport_security=TransportSecuritySettings(...)` or every request comes
  back `421 Misdirected Request`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Server shows as failed, no error text | Look at the host's log, not its UI. Claude Desktop writes `~/Library/Logs/Claude/mcp-server-knowledge-base.log` on macOS and `%APPDATA%\Claude\logs\` on Windows. |
| `search_docs` returns nothing for everything | The corpus was not found. The server logs `no documents found` at startup. Set `KNOWLEDGE_DIR` to the absolute path of `knowledge/`. |
| Works in a terminal, fails from the host | A relative path, or `uv` not on the host's PATH. Hosts do not inherit your shell's environment. Use absolute paths for both. |
| Gemini CLI cannot connect to a working HTTP server | The URL is under `url`, which means SSE. Move it to `httpUrl`. |
| Claude Code reports a missing `command` for a remote server | The entry has a `url` and no `type`. Add `"type": "http"`. |
| Remote endpoint returns 404 or an extra redirect | A trailing slash. The path is `/mcp`. |
| `421 Misdirected Request` | DNS-rebinding protection, because the host is not loopback. Pass `transport_security`. |
| Everything works except resources or prompts | Expected. That host does not support them, and by design you lose nothing. |
