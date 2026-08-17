# 23 · Project 4 · One server, every client

> **TL;DR.** The Model Context Protocol (MCP) promise is that one server works everywhere, and the wire format keeps that promise, but the six hosts in this post disagree about the top-level key of their configuration file, about whether a `type` field exists, and about how to write a variable. This project builds a team knowledge base once and connects it to all six without changing a line of server code. What breaks is configuration and capability gaps, and both are fixable if you know where they are.
>
> **After reading this you will be able to:**
> - Write a correct configuration for Claude Desktop, Claude Code, Cursor, VS Code, Gemini CLI, and Zed.
> - Design a server that loses presentation, never capability, on a host that supports only tools.
> - Serve the same server locally over stdio and remotely over Streamable HTTP from one process.
> - Diagnose the failures that account for most "it will not connect" reports.

![One server box in the center. Six host boxes around it, each labeled with its configuration file name and the top-level JSON key it expects: mcpServers for Claude Desktop, Claude Code, Cursor and Gemini CLI, servers for VS Code, and context_servers for Zed. A seventh box, a Python client, has no configuration file at all.](diagrams/01-one-server-five-hosts.svg) *The wire protocol is identical down every one of these arrows. The file at the top of each box is not.*

---

## 1. The brief

A team's engineering handbook is five Markdown documents in a repository: onboarding, runbooks, architecture notes, frequently asked questions, and code snippets. Everybody knows the answers are in there. Nobody can find them, because finding them means remembering which file and then scrolling.

That is a good fourth project for one reason above all others: **it is the kind of server every member of a team wants in a different application.** The person on call wants it in a terminal next to `kubectl`. The person writing code wants it in their editor. The person writing the incident review wants it in a chat window. The person who automated the weekly report wants it in a script with no user interface at all.

If MCP works, that is one server and six configuration files. This post is the test.

The complete project is [code/23-knowledge-base/](../../code/23-knowledge-base/). It has 51 tests, and two of them are the actual subject of this post. We will come to those in section 6.

### What the server does

Three tools, and their design comes straight from [Post 06](../06-tools-in-depth/index.md).

| Tool | What it does |
|---|---|
| `search_docs(query, limit)` | Ranks handbook **sections** against a query, with excerpts. |
| `get_doc(slug, section)` | Returns one document, or one section of one. |
| `list_topics()` | Names every document with a one-line summary. |

All three are annotated `read_only_hint=True`, `destructive_hint=False`, `idempotent_hint=True`, `open_world_hint=False`. Annotations are hints for the host and not enforcement, as [Post 06](../06-tools-in-depth/index.md) established, but they are the difference between a knowledge base that gets used and one that asks permission four times per question.

On top of the tools sit six concrete resources, two resource templates, and three prompts. Those are additive, and section 6 is about why that word is doing real work.

## 2. Building it once

The retrieval layer, [`src/knowledge_base/index.py`](../../code/23-knowledge-base/src/knowledge_base/index.py), imports nothing from MCP and nothing from outside the standard library. Both are deliberate. No MCP means a bad search result can be bisected without opening a client. No dependencies means the only thing a reader installs before this server starts is the software development kit (SDK) itself.

The ranking is Okapi BM25 in about sixty lines. The retrieval unit is a **section**, split on `##` headings, not a document. A whole-document match tells a model that `runbooks.md` is relevant, which it already suspected. A section match tells it that "Database failover" is the part to read, and hands back an excerpt that proves it.

### The heading boost, and why it exists

This is the most useful thing in the retrieval layer and it was not in the first draft.

Raw BM25 ranked the wrong thing. Searching `database failover` put a two-sentence aside in `architecture.md`, which mentions managed failover in passing, **above** the runbook section literally titled "Database failover". Not a bug in the implementation. BM25's `b` parameter normalizes for length so that a long document cannot out-score a short one merely by containing more words, and the consequence is that a very short passage containing your terms scores extremely well. The aside was short. The runbook section was thorough.

The fix is field boosting. A section's heading is the most deliberate summary that section has, so it is counted into the section's term frequencies more than once:

```python
_HEADING_WEIGHT = 3
...
tokens = tokenize(section.heading) * _HEADING_WEIGHT + tokenize(section.text)
```

Three, not thirty. Raise it much further and a section starts matching its own title regardless of whether the body is relevant, which is a different wrong answer.

There is a regression test named after the problem, in [`tests/test_index.py`](../../code/23-knowledge-base/tests/test_index.py):

```python
def test_the_heading_boost_beats_a_short_passing_mention(index):
    hits = index.search("database failover", limit=5)
    assert hits[0].heading == "Database failover"
    assert hits[0].slug == "runbooks"
```

The reason this belongs in a post about interoperability is that **it is invisible from the protocol layer.** Every host in section 3 would have shown the wrong answer, identically and without complaint, and every one of them would have looked like it was working.

### The server object

[`app.py`](../../code/23-knowledge-base/src/knowledge_base/app.py) holds the one server instance, the logger, and the one loaded copy of the corpus. The corpus is read and indexed at import time, because it is a few tens of kilobytes and re-reading it per request would only make results depend on when a file was last saved.

```python
mcp = MCPServer(
    "knowledge-base",
    title="Team Knowledge Base",
    instructions=(
        "A searchable copy of one team's engineering handbook ...\n"
        "Three tools, and they are meant to be used in this order:\n"
        "1. search_docs(query) ranks handbook sections against a query ...\n"
        "2. get_doc(slug, section) returns the full text ...\n"
        "3. list_topics() names every document ...\n"
    ),
)
```

`instructions` is the closest thing MCP has to a README aimed at the model, and it is the only piece of documentation some hosts ever surface. Note what it does not mention: resources and prompts. A client that supports neither must still read these instructions and use this server correctly, and that constraint is the whole degradation strategy compressed into one paragraph.

Logging goes to stderr, set up once in `app.py`. Under stdio, stdout **is** the protocol channel, and a stray `print()` anywhere in the package is parsed as a JSON-RPC frame. [Post 04](../04-transports/index.md) covered why; the practical version is that the resulting error message names neither your `print` nor your file.

## 3. Six hosts, six files

Here is the whole disagreement in one table. Everything a reader gets wrong is in it.

| | Top-level key | `type` field | stdio | Streamable HTTP | Interpolation |
|---|---|---|---|---|---|
| **Claude Desktop** | `mcpServers` | none | `command` + `args` | not in this file | none |
| **Claude Code** | `mcpServers` | optional for stdio, **required** for remote | `command` + `args` | `"type": "http"` + `url` | `${VAR}`, `${VAR:-default}` |
| **Cursor** | `mcpServers` | optional | `command` + `args` | `url` | `${env:VAR}`, `${workspaceFolder}`, `${userHome}` |
| **VS Code** | `servers` | **required on every entry** | `"type": "stdio"` | `"type": "http"` + `url` | `${workspaceFolder}`, `${userHome}`, `${input:id}` |
| **Gemini CLI** | `mcpServers` | **no such field** | `command` | `httpUrl` | `$VAR`, `${VAR}`, `%VAR%` on Windows |
| **Zed** | `context_servers` | none | `command` + `args` | `url` | none documented |

Six rows, four disagreements. Take them one at a time.

### The top-level key

`mcpServers` for Claude Desktop, Claude Code, Cursor, and Gemini CLI. **`servers` for VS Code. `context_servers` for Zed.**

There is no protocol reason for this. The keys were chosen independently, before anyone needed them to agree, and none of them can change now without breaking every installation that already exists. It is the purest example in the ecosystem of a thing the specification does not cover and therefore did not standardize.

The failure mode is quiet. Paste a `mcpServers` block into `.vscode/mcp.json` and VS Code does not error; it reads a file with no `servers` key and concludes you have configured no servers. You get an empty list and no explanation.

### The `type` field: three different rules

**VS Code requires it on every entry**, stdio included. `"type": "stdio"` or `"type": "http"`.

**Claude Code makes it optional for stdio and mandatory for remote.** An entry with a `url` and no `type` is a configuration error, because a typeless entry is read as stdio and then found to have no `command`. The message is specific, which is welcome:

```
MCP server "knowledge-base-remote" has a "url" but no "type"; add "type": "http"
```

Claude Code also accepts `"streamable-http"` as an alias for `"http"`, so a snippet copied out of a server's own documentation usually works unmodified.

**Gemini CLI has no `type` field at all.** It infers the transport from *which key you used*:

| Key | Transport |
|---|---|
| `command` | stdio |
| `url` | Server-Sent Events (SSE), the superseded transport |
| `httpUrl` | Streamable HTTP |

This is the single most expensive trap in the table. Put a Streamable HTTP endpoint under `url` and Gemini CLI will faithfully try to speak SSE to it. Nothing in the file looks wrong. The key you did not think about is the one carrying the meaning. Use `httpUrl`.

### Remote servers in Claude Desktop are not in the file at all

**You cannot add a remote MCP server by putting a `url` in `claude_desktop_config.json`.** This is worth stating flatly because a great deal of published material shows exactly that, and it does not work.

That file configures local servers that Claude Desktop launches as subprocesses. It is not a general server list. Remote servers are Custom Connectors, added through the interface:

1. **Settings > Connectors**.
2. **Add** at the top right, then **Add custom connector**.
3. Paste the server URL, ending `/mcp`, and complete whatever authentication the server asks for.

So [`clients/claude_desktop_config.json`](../../code/23-knowledge-base/clients/claude_desktop_config.json) in this project has one stdio entry and nothing else, which is the honest shape of that file.

### Interpolation: five syntaxes for one idea

| Client | Syntax | Notes |
|---|---|---|
| Claude Desktop | none | Absolute paths only. `${APPDATA}` in a path is a known failure. |
| Claude Code | `${VAR}`, `${VAR:-default}` | Expands in `command`, `args`, `env`, `url`, and `headers`. |
| Cursor | `${env:VAR}` | Plus `${workspaceFolder}`, `${userHome}`, `${pathSeparator}`. |
| VS Code | `${input:id}` | Plus `${workspaceFolder}`, `${userHome}`. Use `inputs` for secrets. |
| Gemini CLI | `$VAR` or `${VAR}` | `%VAR%` also works on Windows. Undefined resolves to empty. |
| Zed | none documented | Write the value out. |

Read the shape of that disagreement carefully, because it is not just spelling. Claude Code's `${VAR}` reads a **process** environment variable. VS Code's `${input:id}` prompts the **user** and caches the answer. Cursor's `${env:VAR}` reads the environment behind an `env:` namespace. A string copied between two of these files can parse cleanly in both and mean something different in each.

Gemini CLI's rule deserves its own line: **an undefined variable resolves to the empty string.** Not an error, not the literal text. A missing token silently becomes `Authorization: Bearer `.

### Where the files go

| Client | Scope | macOS | Windows | Linux |
|---|---|---|---|---|
| **Claude Desktop** | global | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` | no official build |
| **Claude Code** | project | `.mcp.json` in the project root | same | same |
| **Claude Code** | user | `~/.claude.json` | `%USERPROFILE%\.claude.json` | `~/.claude.json` |
| **Cursor** | project | `.cursor/mcp.json` | same | same |
| **Cursor** | global | `~/.cursor/mcp.json` | `%USERPROFILE%\.cursor\mcp.json` | `~/.cursor/mcp.json` |
| **VS Code** | workspace | `.vscode/mcp.json` | same | same |
| **VS Code** | user profile | `~/Library/Application Support/Code/User/mcp.json` | `%APPDATA%\Code\User\mcp.json` | `~/.config/Code/User/mcp.json` |
| **Gemini CLI** | project | `.gemini/settings.json` | same | same |
| **Gemini CLI** | user | `~/.gemini/settings.json` | `%USERPROFILE%\.gemini\settings.json` | `~/.gemini/settings.json` |
| **Zed** | global | `~/.config/zed/settings.json` | `%APPDATA%\Zed\settings.json` | `~/.config/zed/settings.json` |

Three rows need a note.

- **Claude Desktop has no official Linux build.** There are unofficial packages, and this post is not going to guess at a path for one. Open the file from inside the application instead: **Settings > Developer > Edit Config**.
- **VS Code's user-profile path is not documented**, because a profile other than the default lives somewhere else entirely. The reliable route is the command palette: **MCP: Open User Configuration**. The paths above are the default profile.
- **Zed's own instruction** is likewise the command palette: **zed: open settings file**.

Project-scoped files are the useful ones for a team. Commit `.mcp.json`, `.cursor/mcp.json`, or `.vscode/mcp.json` and everybody who clones the repository gets the server. That single fact is most of the practical value of MCP inside an organization.

### The one command they all run

```json
{
  "command": "uv",
  "args": ["--directory", "/absolute/path/to/23-knowledge-base", "run", "python", "-m", "knowledge_base"]
}
```

`uv --directory <path> run` is what makes this survive a host that launches you from an arbitrary working directory: it resolves the project and its virtual environment from the path you gave, not from the current directory. Every stdio entry in [`clients/`](../../code/23-knowledge-base/clients/) is that command with a different wrapper around it.

Every hard-coded path in those files is a placeholder like `/absolute/path/to/23-knowledge-base`. Replace it with a real absolute path. The Cursor and VS Code files use `${workspaceFolder}` instead, which the host expands for you. Relative paths are the most common reason a server fails to start, because the working directory a host launches you from is rarely the one you assumed.

### One deliberate omission

None of the six committed files contains a `//` comment, even though VS Code's `mcp.json` and Zed's `settings.json` both tolerate them. That way every file parses with a plain `json.load` and can be pasted into any of the others without a surprise. The paths live in the project README instead of at the top of each file.

## 4. Local against remote, from one process

The same code serves both transports. [Post 04](../04-transports/index.md) explained the choice; here is what it costs, which is one command-line flag:

```bash
uv run python -m knowledge_base           # stdio, what a desktop host spawns
uv run python -m knowledge_base --http    # Streamable HTTP on 127.0.0.1:8000/mcp
```

![Two columns. On the left, stdio: the host owns the process lifetime, one process per host, configuration by file path, and no network. On the right, Streamable HTTP: one process for every host, configuration by URL, and the endpoint path with no trailing slash called out. A shared box at the bottom holds the server code, identical in both columns.](diagrams/03-local-vs-remote.svg) *The same code, the same tools, the same schemas. Only who owns the process changes.*

Under stdio each host spawns its own copy. Six hosts means six processes, six copies of the index, and six chances to point one of them at the wrong directory. Over Streamable HTTP one process serves them all, which is both tidier and the only shape that makes sense once the corpus is large or lives behind credentials.

### The trailing slash, measured

**The path is `/mcp`, with no trailing slash.** That is `streamable_http_path`'s default in the SDK, and it is the only path served. Measured against this server, with bodies elided:

```
POST /mcp   -> 200
POST /mcp/  -> 307, Location: http://127.0.0.1:8000/mcp
```

Not a 404, which is what most people expect and what most people write in their notes. Starlette answers the trailing-slash form with a redirect, and whether that rescues you depends entirely on the client at the other end. The SDK's own Hypertext Transfer Protocol (HTTP) client follows redirects and works. A client configured not to follow them fails. Anything that drops the request body across the redirect fails in a more confusing way.

And even the surviving case is not free: you have bought a wasted round trip on **every single call**, and any proxy or authenticating gateway in front of the server is one more place for that redirect to go wrong. Write `/mcp`.

### Two more things before you leave loopback

`--host 0.0.0.0` makes the server reachable from your whole network. This one is read-only, but "read-only" and "safe to publish" are different claims, and [Post 19](../19-security/index.md) is about the gap between them.

The SDK arms Domain Name System (DNS) rebinding protection automatically for `127.0.0.1`, `localhost`, and `::1`. Behind a real hostname you must pass `transport_security=TransportSecuritySettings(...)` or every request comes back `421 Misdirected Request`, which is not an error message anyone guesses correctly the first time.

## 5. The seventh client, which has no configuration file

[`clients/programmatic_client.py`](../../code/23-knowledge-base/clients/programmatic_client.py) is the version you reach for in continuous integration, in a chat bot, or in a batch job. It is also the fastest way to find out whether a server works at all, before you spend twenty minutes wondering why a desktop application is showing a red dot and no error text.

Three things in it correct the widely copied older version.

**One `Client`, not a `ClientSession` plus a manual `initialize()`.** In SDK 2.x, `mcp.Client` is the whole client: transport, request correlation, and the multi round-trip retry loop. There is no `initialize` to call, because revision 2026-07-28 removed it ([Post 03](../03-wire-protocol/index.md)).

**`streamable_http_client(url)` yields a 2-tuple, not 3.** The version 1 helper was `streamablehttp_client()`, it yielded three values, and it was routinely unpacked as two. The replacement yields a `TransportStreams` 2-tuple, and the right thing to do with it is not to unpack it at all:

```python
def http_transport(url: str) -> object:
    return streamable_http_client(url)

...
async with Client(transport) as client:
    await probe(client)
```

Hand the context manager straight to `Client(...)` and let the client drive it. Every unpacking bug in this area disappears if you never unpack.

**The endpoint is `/mcp`.** Same rule as section 4, from the other side of the wire.

The body of the script is capability probing, which is exactly what a real host does:

```python
tools = (await client.list_tools()).tools          # the floor. Always present.

try:
    resources = (await client.list_resources()).resources
except MCPError as error:
    print(f"resources: unavailable ({error.message})")
else:
    ...
```

Ask for tools. **Try** resources and prompts, and carry on without them. That is the client half of the idea the next section is about.

## 6. Graceful degradation, asserted rather than claimed

"Works with every client" is a marketing sentence. The engineering sentence underneath it is narrower and testable:

> A client that supports only tools must lose presentation, not capability.

Not every host in section 3 reads resources. Not every host offers prompts. So the server is built in three layers, most-supported first.

![Three stacked layers. The bottom layer, tools, is labeled universal and marked as load-bearing. The middle layer, resources, is labeled common and marked additive. The top layer, prompts, is labeled patchy and marked additive. Arrows from each of the upper layers point down to the tool that covers it. A side panel names the two tests that assert the mapping.](diagrams/02-capability-matrix.svg) *Capability goes in the bottom layer. The upper two make it nicer to reach, and nothing else.*

| Layer | Assumed support | What a client without it loses |
|---|---|---|
| `tools.py` | universal | nothing works |
| `resources.py` | common | a menu of attachable documents |
| `prompts.py` | patchy | three slash-command shortcuts |

Every resource maps to a tool that returns the same text:

| Resource | Tool that covers it |
|---|---|
| `knowledge://index` | `list_topics()` |
| `knowledge://doc/{slug}` | `get_doc(slug)` |
| `knowledge://section/{slug}/{anchor}` | `get_doc(slug, section)` |

And every prompt is a shortcut for a sequence of tool calls the model could have made itself, which is why each prompt body ends by naming the tools it expects to be used next.

### The two tests that make this a claim instead of a hope

A table in a docstring rots. A test does not. From [`tests/test_server.py`](../../code/23-knowledge-base/tests/test_server.py):

```python
async def test_every_resource_is_reachable_through_a_tool():
    async with Client(mcp) as c:
        for resource in (await c.list_resources()).resources:
            uri = str(resource.uri)
            via_resource = (await c.read_resource(uri)).contents[0].text
            ...
            slug = uri.rsplit("/", 1)[-1]
            via_tool = (
                await c.call_tool("get_doc", {"slug": slug})
            ).structured_content["text"]
            assert via_tool == via_resource, f"{uri} is not reachable through get_doc"
```

It walks `resources/list`, and for every document URI it asserts that some tool call returns **byte identical** text. Not equivalent text, not text containing the same facts. Identical. The elided branch is the one exception, `knowledge://index`, which `list_topics()` covers as data rather than as Markdown and is therefore checked on content. Add a resource without adding a tool path to it and the suite fails, with the URI in the message.

The second test answers a real handbook question using nothing but `tools/list` and `tools/call`, which is what the server looks like to the least capable host in the table.

Note the shape of every test in that file: the client is opened with `async with` **inside the test body**, never handed over by a yield fixture. The client owns an anyio task group, a task group has to be exited by the task that entered it, and a yield fixture tears down in a different task. Do it the other way and every test fails with "Attempted to exit cancel scope in a different task", which reads like a bug in your server and is not.

### The rule, stated once

**Put the capability in a tool, then let richer clients present it more nicely.**

Doing it the other way round produces a server that quietly does less on Zed than it does on Claude Desktop, and nobody, including you, can tell you why. The failure is silent because neither side is broken: the host correctly does not implement a primitive it never claimed, and the server correctly published one.

## 7. Verifying on each client

The order below is deliberate. Each step rules out a whole class of problem before the next step can be confused by it.

**1. Run the tests.** `uv run pytest`. If the 51 tests pass, the server is fine and everything after this is configuration.

**2. Run the programmatic client over stdio.** `uv run python clients/programmatic_client.py`. This spawns the server exactly as a desktop host would and prints what it found. If this works and a host does not, the difference is the host's environment, not your code.

**3. Run it over HTTP.** Start `uv run python -m knowledge_base --http` in one terminal and `uv run python clients/programmatic_client.py --http` in another. This separates transport problems from configuration problems.

**4. Then, and only then, edit a host's configuration file.** One host at a time. Restart the host completely; several of them re-read the file only at startup, and a few cache a failed server until they are restarted twice.

**5. Ask a question you know the answer to.** "What do I do when the reconciler falls behind?" should come back with the `faq` entry on reconciler lag first and the `runbooks` section on reconciliation backlog second. A host that connects but returns nothing useful has usually pointed `KNOWLEDGE_DIR` at a directory with no Markdown in it, and the server said so in a log line nobody read.

## 8. When this shape is the wrong one

**When only one host will ever use it.** The abstraction cost here is small but not zero, and a server written for one host can use that host's specific features without apology.

**When the capability genuinely requires a primitive some hosts lack.** A server whose entire point is an interactive interface cannot degrade to tools, and should not pretend to. [Post 24](../24-mcp-apps-and-frontier/index.md) is about that case, and about the extension that addresses it.

**When the corpus is large.** An in-memory BM25 index over five documents is honest engineering. Over fifty thousand documents it is not, and the interesting problems move to the retrieval layer where MCP has no opinion at all.

**When the data is per-user.** This server serves the same corpus to everyone, which is why it needs no authorization. The moment the answer depends on who is asking, read [Post 20](../20-authorization/index.md) first.

---

## Troubleshooting appendix

The failures readers actually hit, in rough order of frequency.

| Symptom | Cause and fix |
|---|---|
| Server shows as failed, with no error text | Read the host's log, not its interface. Claude Desktop writes `~/Library/Logs/Claude/mcp-server-knowledge-base.log` on macOS and `%APPDATA%\Claude\logs\` on Windows. VS Code has an **MCP** output channel. Claude Code has `/mcp`. |
| Works in a terminal, fails from the host | A relative path, or `uv` not on the host's `PATH`. Hosts do not inherit your shell's environment, including anything a `.zshrc` or `.bashrc` sets. Use absolute paths for both the interpreter and the project. |
| The host lists no servers at all, silently | Wrong top-level key. `servers` for VS Code, `context_servers` for Zed, `mcpServers` for the rest. A file with an unrecognized top-level key is not an error, it is an empty configuration. |
| VS Code refuses the entry | Missing `type`. It is required on every entry, stdio included. |
| Claude Code reports a missing `command` for a remote server | The entry has a `url` and no `type`. Add `"type": "http"`. |
| Gemini CLI cannot connect to a working HTTP server | The URL is under `url`, which means SSE. Move it to `httpUrl`. |
| Remote endpoint returns an unexpected redirect | A trailing slash. `POST /mcp/` answers `307`, not `404`. The path is `/mcp`. |
| `421 Misdirected Request` | DNS-rebinding protection, because the bind host is not loopback. Pass `transport_security=TransportSecuritySettings(...)`. |
| `Authorization: Bearer ` with nothing after it | An undefined variable. Gemini CLI resolves undefined variables to the empty string rather than erroring. |
| Every search returns nothing | The corpus was not found. The server logs `no documents found` at startup. Set `KNOWLEDGE_DIR` to the absolute path of `knowledge/`. |
| The connection dies immediately with a parse error | Something wrote to stdout. Under stdio that is the protocol channel. Find the `print()`. |
| Everything works except resources or prompts | Expected. That host does not support them, and by design you lose nothing. |
| Configuration edits appear to do nothing | Restart the host fully. Some hosts read the file only at launch, and some keep a failed server marked failed until a second restart. |

---

## Common pitfalls

- **Assuming the configuration key is the same everywhere.** It is `mcpServers` for four of the six hosts here, `servers` for VS Code, and `context_servers` for Zed. The wrong key produces an empty server list, not an error, which is the worst possible failure mode.
- **Putting a Streamable HTTP URL under Gemini CLI's `url` key.** There is no `type` field to correct you. `url` means SSE, `httpUrl` means Streamable HTTP, and the file looks correct either way.
- **Adding a remote server to `claude_desktop_config.json` as `{"url": ...}`.** That file is for local subprocesses. Remote servers go through Settings, Connectors, Add custom connector.
- **Inventing a Claude Desktop path for Linux.** There is no official build. Use the in-app editor, or a different host.
- **Writing the endpoint with a trailing slash.** `POST /mcp/` returns `307`, not `404`. Clients that follow redirects survive and pay a round trip per call; clients that do not, fail.
- **Unpacking `streamable_http_client`.** It yields a 2-tuple, not the 3-tuple the version 1 helper yielded. Pass the context manager to `Client(...)` and never unpack it.
- **Making a resource or a prompt load-bearing.** If the only way to reach some content is a primitive half your hosts ignore, your server silently does less on those hosts. Put the capability in a tool and assert the mapping with a test.
- **Trusting search quality because the protocol layer is green.** The heading-boost bug returned a confidently wrong answer through every client, identically. No amount of protocol testing would have found it.

---

## Further reading

- Specification, *"Transports"*, revision 2026-07-28. The Streamable HTTP endpoint rules behind section 4. <https://modelcontextprotocol.io/specification/draft/basic/transports>
- Each host's own configuration documentation: Claude Desktop and Claude Code (Anthropic), Cursor, the VS Code MCP servers page, the Gemini CLI settings reference, and the Zed context-servers page. Every row of the table in section 3 comes from one of these, and they are the pages to re-check when a host changes, because none of this is specified anywhere central.
- MCP extensions client support matrix (2026). The only cross-host capability table that is maintained centrally, and it tracks extensions rather than core primitives. <https://modelcontextprotocol.io/extensions/client-matrix>
- Robertson and Zaragoza, *"The Probabilistic Relevance Framework: BM25 and Beyond"* (2009). The `k1` and `b` parameters in section 2, and why length normalization does what it does.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 22 — Publishing: the registry, `server.json`, and MCPB bundles](../22-publishing/index.md)**: the step before this one, if you skipped it. Six configuration files that a reader has to write by hand become one install command once the server is published.
- **[Post 24 — MCP Apps, extensions, and where the protocol goes next](../24-mcp-apps-and-frontier/index.md)**: the closing post. Everything new in MCP now arrives as an extension, and the first official one lets a server ship its own interface, which is the one case where degrading to tools is not enough.
