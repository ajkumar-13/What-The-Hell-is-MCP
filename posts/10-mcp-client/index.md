# 10 - Building your own MCP client

> **TL;DR.** A Model Context Protocol (MCP) client is a few hundred lines of code, and writing one explains most of what a host does with a server. This post builds one: three transports behind one code path and one lifetime rule, discovery, a result reader, and both ways of answering a server that asks a question mid-call. The failure that is easiest to get wrong is that a tool which ran and failed arrives as a **successful** response carrying `isError: true`, so a `try`/`except` around the call never sees it. Every transcript below is printed by a script in `snippets/` or by the project's own test suite.
>
> **Prerequisites:** [Post 05](../05-first-server/index.md).
> **Safe to skip?** Skip it if the reader can already open one server over standard input and output, Streamable Hypertext Transfer Protocol (HTTP), and in memory with the Python software development kit's (SDK's) `Client`, tell a tool execution error from a protocol error in a result, drive an `input_required` result to completion by hand, and close a connection without leaving its subprocess behind.
>
> **After reading, you will be able to:**
>
> - Connect to a server over standard input and output, Streamable HTTP, or in memory, through one code path.
> - Read every content block type a tool can return, and branch on isError rather than on exceptions.
> - Answer a server that asks a question mid-call, both automatically through a callback and by hand.
> - Tear a connection down without leaking the subprocess it held, even when the code inside the connection raises.

![A client shown as one box between a host on the left and a server on the right. Inside the box, five numbered parts are named: the transport, request identifier correlation, the capability cache, the multi round-trip driver, and the result reader, which carries a branch where a raised call and a result with isError true both become one failed outcome. A band across the bottom states the lifetime rule: the client wraps a task group, so it must be entered and exited by the same task.](diagrams/01-client-internals.svg)

*One client per connected server. Four parts on the way out, one branch on the way back, and one lifetime rule holding all of it together.*

---

## 1. The question: the client that lied to its model

What does a client actually do, and why does writing one remove the remaining mystery from every host's behavior? The shortest route to an answer starts with a client that gets it wrong. Here is a shape a hand-written MCP client can start with, and the one the previous edition of this post started with, as `snippets/lying_client.py` defines it. It looks defensive.

```python
async def naive_call(session, tool_use):
    try:
        result = await session.call_tool(tool_use.name, tool_use.input)
        text = "\n".join(c.text for c in result.content if hasattr(c, "text")).strip()
    except Exception as e:
        text = f"TOOL_ERROR: {type(e).__name__}: {e}"
    return text
```

Six lines inside the function, three defects, and the first one is the reason this post exists.

**The `except` clause catches the wrong failures.** It catches the transport dying and the protocol rejecting the message. It does not catch a tool that ran and failed, because that is not an exception. It is a successful JavaScript Object Notation Remote Procedure Call (JSON-RPC) response with a flag set in it. The script asks the system-information server from [post 05](../05-first-server/index.md) to find a process with a string where an integer belongs, and the server sends back this result, printed in its wire spelling:

```json
{
  "_meta": {
    "io.modelcontextprotocol/serverInfo": {
      "name": "system-info",
      "version": ""
    }
  },
  "content": [
    {
      "type": "text",
      "text": "Error executing tool find_process: 2 validation errors for find_processArguments\nname\n  Field required [type=missing, input_value={'limit': 'not-a-number'}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.14/v/missing\nlimit\n  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='not-a-number', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.14/v/int_parsing"
    }
  ],
  "isError": true,
  "resultType": "complete"
}
```

`resultType` is `"complete"`. Nothing raised. The `try` block finished normally, the `.text` join collected the error message, and the naive function returned it as the answer; the script prints `starts with TOOL_ERROR: False`. A host built on that function hands the model a validation error with nothing to say it is one.

**The `.text` join throws away four of the five content block types.** `content` is a union of text, image, audio, resource links, and embedded resources, and only the text block has a `text` attribute. Dropping anything without one silently discards screenshots, audio, and pointers to large files. It also ignores `structuredContent`, a separate field carrying the machine-readable form of the same answer, the one a program can act on.

**And the connection under it, managed by hand, can leak.** That one takes a section of its own, section 4.

The rest of this post is the client that gets all three right. The complete project is [code/10-mcp-client/](../../code/10-mcp-client/), and its suite has 68 tests. Every transcript below is printed either by that suite or by a script in this post's `snippets/` directory that imports the project and the post 05 server and runs them; section 10 lists the commands.

## 2. Client and host, one more time

This series uses three words precisely, and here the distinction becomes a file layout.

- The **host** is the application a person interacts with. It owns the model, the conversation, and every permission decision: Claude Desktop, an integrated development environment (IDE), a command-line interface (CLI) of the reader's own.
- A **client** is the protocol-speaking object inside the host. There is exactly one per connected server, and it owns the transport, request identifier correlation, and the capability cache.
- The **server** is the process that exposes tools, resources, and prompts.

A host contains clients, plural. A host connected to four servers has four clients, four transports, and four independent lifetimes, and none of the clients knows the others exist. Merging their catalogs into one list the model can see is the host's job, and it is [post 11](../11-building-a-host/index.md).

This post builds only the client half: `connection.py`, `results.py`, and `interactive.py` under `src/mcp_host/` in the project. Post 11 adds `catalog.py`, `permissions.py`, `providers.py`, and `loop.py` on top of them.

## 3. Three transports, one `Client`

Wire format before code, as always. Over standard input and output (stdio) a request is one line of JavaScript Object Notation (JSON) written to a subprocess's standard input. `snippets/wire_frames.py` connects the SDK's `Client` to the post 05 server over stdio through a wrapper that records each frame the client writes; this is the second line, the tool call:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_system_info","arguments":{},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"mcp","version":"0.1.0"},"io.modelcontextprotocol/clientCapabilities":{"elicitation":{"form":{},"url":{}}}}}}
```

The identifier is 2 because the first line was `server/discover`, sent the moment the connection opened; section 5 comes back to it. A third line followed the call, a `tools/list`: before the SDK hands back a successful result from a tool this client has not listed, it fetches the listing to validate the `structuredContent` against that tool's `outputSchema`.

Over Streamable HTTP the same JSON is a POST body, with the routing headers [post 04](../04-transports/index.md) covered. `snippets/transports.py` records the request the SDK sends for the same call:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_system_info
```

Same message, different envelope. That is the entire difference, and it is why one client class covers both. The Streamable HTTP transport page requires `Mcp-Method` on every request and `Mcp-Name` on `tools/call`, `resources/read`, and `prompts/get`. Note the `_meta` block: revision 2026-07-28 has no `initialize` handshake and no session, so the protocol revision and the client's capabilities ride on **every** request, and the Base protocol page forbids a server to rely on an earlier request for them. `clientInfo` travels with them, which the specification makes a **SHOULD** rather than a **MUST**; the SDK sends `{"name":"mcp","version":"0.1.0"}` unless the `Client` is given its own `client_info`. The capabilities here declare elicitation because the script passed an elicitation callback, which section 7 explains. When a server rejects a call with `HeaderMismatch` (`-32020`), the docstring of the SDK's `Client.call_tool` says it re-fetches the tool listing and resends once; none of this post's runs triggers that path.

In the Python SDK, `Client` decides how to connect from the *shape* of its first argument:

| What `Client(...)` is given | What it does |
|---|---|
| an `MCPServer` object | connects in process: no subprocess and no socket |
| a `str` | treats it as a Streamable HTTP Uniform Resource Locator (URL), always |
| a `StdioServerParameters` | launches the command as a subprocess and talks over its stdin and stdout |
| anything else | uses it as a transport: `stdio_client(params)`, `streamable_http_client(url, http_client=...)` |

**There is no `Client("stdio://...")` form.** A plain string always means an HTTP URL, and section 11 shows what a `stdio://` string produces. `StdioServerParameters` has been accepted directly since release 2.1.0; older code wraps it in `stdio_client(...)` first, which still works. When the context manager opens, `Client` calls `server/discover`, and against a server too old to know that method it falls back to the `initialize` handshake of earlier revisions; that is the default `mode="auto"`, and a reader will meet `initialize` only there.

The project wraps that dispatch in one function so a configuration file can drive it. From [src/mcp_host/connection.py](../../code/10-mcp-client/src/mcp_host/connection.py):

```python
async def _transport_for(spec: ServerSpec, stack: AsyncExitStack) -> Any:
    """Build the argument `Client(...)` wants, by transport.

    `Client` dispatches on the shape of what it is given: an `MCPServer` runs
    in process, a `str` is always an HTTP URL (there is no `stdio://` form),
    and anything else is used as a transport.
    """
    if spec.transport == "memory":
        if spec.server is None:
            raise ValueError(f"server {spec.name!r}: memory transport needs a server object")
        return spec.server

    if spec.transport == "http":
        if not spec.url:
            raise ValueError(f"server {spec.name!r}: http transport needs a url")
        if not spec.headers:
            # The plain form. `Client("https://...")` would also work; going
            # through the transport keeps one code path.
            return streamable_http_client(spec.url)
        # Headers, auth, and timeouts need a client you built. Note the
        # import: v2 ships httpx2, and `import httpx` here would give you a
        # different library whose exceptions the SDK never raises.
        import httpx2

        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(headers=spec.headers, timeout=30.0)
        )
        return streamable_http_client(spec.url, http_client=http_client)

    if not spec.command:
        raise ValueError(f"server {spec.name!r}: stdio transport needs a command")
    params = StdioServerParameters(
        command=spec.command,
        args=spec.args,
        env=spec.env,
        cwd=spec.cwd,
    )
    return stdio_client(params)
```

`snippets/transports.py` opens the post 05 server through that one function three times, in memory, as a stdio subprocess, and over HTTP against a copy it starts on a free port, and prints what each connection discovered:

```
memory server 'system-info' version '', protocol 2026-07-28, tools: get_system_info, find_process, terminate_process, watch_cpu
stdio  server 'system-info' version '', protocol 2026-07-28, tools: get_system_info, find_process, terminate_process, watch_cpu
       processes the stdio connection started: True; still running after the block: 0
http   server 'system-info' version '', protocol 2026-07-28, tools: get_system_info, find_process, terminate_process, watch_cpu
```

Four details in that function each cost an afternoon when met cold.

1. **The import is `httpx2`, not `httpx`.** The 2.x SDK depends on `httpx2`. Importing `httpx` there gives a different library, whose exception types the SDK never raises and whose client objects `streamable_http_client` is not written to take.
2. **The function is `streamable_http_client`.** In the 1.x line it was `streamablehttp_client`, with no underscore before `http`, and the `get_session_id` callback that came with it is gone along with sessions. It yields a **2-tuple** of streams, not the 3-tuple of the older signature.
3. **`read_timeout_seconds` is a float of seconds.** In 1.x it was a `timedelta`; section 11 prints what a `timedelta` does now.
4. **The in-memory case is not a test double.** `Client(server_object)` sends every request through the server's real handlers, schemas, and validation, with no subprocess and no socket; in the default mode it hands each request straight to the server's dispatcher, so not even JSON-RPC framing is involved. It is how the project's suite runs in seconds, and [post 12](../12-testing-and-debugging/index.md) is built on it.

Configuration uses the `mcpServers` shape Claude Desktop reads ([post 05](../05-first-server/index.md)), so a file that works here mostly works there. This is the entry [servers.example.json](../../code/10-mcp-client/servers.example.json) ships, without the file's comment lines and its HTTP example:

```json
{
  "mcpServers": {
    "system-info": {
      "command": "uv",
      "args": ["run", "--directory", "../05-first-server", "python", "-m", "system_info"]
    }
  }
}
```

Two shapes: `{command, args, env, cwd}` for stdio and `{url, headers}` for HTTP; `ServerSpec.from_config` in `connection.py` picks one by whether the entry has a `url`. The relative path in the example resolves only when the command runs from inside `code/10-mcp-client/`, as the file's own comment says. A real configuration uses absolute paths, because the process that spawns a server does not share the reader's working directory.

## 4. Lifetimes, and the subprocess the cleanup code leaked

Here is the third defect from section 1, in the form 1.x-era client code took. `snippets/lifetimes.py` keeps it, spelled exactly that way, in a class called `HandRolled`:

```python
    async def open(self):
        # Do not do this.
        self._transport_cm = stdio_client(params)
        self._read, self._write = await self._transport_cm.__aenter__()
        self.session = ClientSession(self._read, self._write)
        await self.session.__aenter__()

    async def close(self):
        await self.session.__aexit__(None, None, None)
        await self._transport_cm.__aexit__(None, None, None)
```

That reads as careful code. It has two bugs, and they compound.

**The first is the task boundary.** The transport and the session each wrap an `anyio` task group, and a task group must be exited by the task that entered it. `close()` runs in whatever task happens to call it, which in a real host is a signal handler, a shutdown hook, or a different coroutine entirely. The failure names nothing the reader wrote.

**The second is worse, because it is silent.** Look at the order of the two `__aexit__` calls. If the session exit raises, the transport exit never runs, and the subprocess that `close()` existed to reap survives. The cleanup method leaks exactly the resource it was written to guard, and only on the error path, which is the path nobody tests by hand.

The script opens a `HandRolled` connection in one task, calls `close()` from another, and checks the process table:

```
== hand-rolled: entered in one task, closed from another ==
server process running after open(): True
close() raised RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
server process still running after close(): True
```

The 2.x `Client` alone, entered in one task and exited from another, raises the same `RuntimeError`, but its subprocess does not survive (`server process still running after __aexit__: False`), because the SDK unwinds its own layers with an exit stack that keeps going when one layer raises. The leak needs a second context exited by hand after the one that raised.

`AsyncExitStack` fixes both bugs. It unwinds every context it entered, in reverse order, on the exception path as well as the normal one, and it does so in the task that owns it. From [src/mcp_host/connection.py](../../code/10-mcp-client/src/mcp_host/connection.py):

```python
@asynccontextmanager
async def open_connection(
    spec: ServerSpec,
    *,
    elicitation_callback: Any = None,
    read_timeout_seconds: float | None = None,
) -> AsyncIterator[ServerConnection]:
    """Connect, yield, and tear down in the same task.

    Use it as a context manager and the lifetime problem disappears:

        async with open_connection(spec) as conn:
            tools = await conn.list_tools()

    `read_timeout_seconds` is a float of seconds in v2, not a `timedelta`.
    """
    async with AsyncExitStack() as stack:
        transport = await _transport_for(spec, stack)
        client = await stack.enter_async_context(
            Client(
                transport,
                elicitation_callback=elicitation_callback,
                read_timeout_seconds=read_timeout_seconds,
            )
        )
        yield ServerConnection(spec, client)
```

The project states the guarantee as a test rather than as a comment: `test_the_connection_closes_cleanly_even_when_the_body_raises` in [tests/test_client.py](../../code/10-mcp-client/tests/test_client.py) raises `ZeroDivisionError` inside the block, then calls a tool through the connection it captured and asserts that the call fails cleanly with a message about a closed connection, instead of hanging on a pipe nobody is reading. The test runs in memory; `snippets/lifetimes.py` runs the same shape over stdio and reads the process table instead:

```
== open_connection, with a body that raises ==
escaped as ExceptionGroup, nested 2 deep; members: ['ZeroDivisionError']
server process running inside the block: True; after it: False
```

**That first line is why the test catches `BaseException` and calls a `flatten` helper.** An exception escaping `async with Client(...)` does not arrive as itself. The task groups re-raise it wrapped in an `ExceptionGroup`, two deep here, so `except ZeroDivisionError` does not fire and `pytest.raises(ZeroDivisionError)` does not match. The test file ships a short helper for it:

```python
def flatten(exc: BaseException) -> list[BaseException]:
    """Unwrap nested ExceptionGroups.

    An exception raised inside `async with Client(...)` escapes through an
    anyio task group, which re-raises it wrapped in an `ExceptionGroup`. So
    `pytest.raises(ZeroDivisionError)` does not match, and neither does a
    plain `except ZeroDivisionError` in host code. Worth knowing before you
    spend an afternoon on it.
    """
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]
```

This is not only a testing concern. Host code that writes `except ConnectionError` around a whole `async with` block has the same hole, and in production the symptom is an exception handler that never runs. Python 3.11's `except*` matches inside a group; on 3.10, which the project still supports, flattening is the way.

## 5. Discovery: what the server has

There is no `initialize` in revision 2026-07-28. A client learns what a server is by calling `server/discover`, and the SDK does that when the context manager opens; afterwards the answers hang off the client as properties. The project's `ServerConnection` exposes the ones a host needs, in [src/mcp_host/connection.py](../../code/10-mcp-client/src/mcp_host/connection.py):

```python
    @property
    def server_info(self) -> Any:
        return self.client.server_info

    @property
    def protocol_version(self) -> str | None:
        return self.client.protocol_version

    async def list_tools(self) -> list[Tool]:
        return list((await self.client.list_tools()).tools)

    async def list_resources(self) -> list[Any]:
        return list((await self.client.list_resources()).resources)

    async def list_prompts(self) -> list[Any]:
        return list((await self.client.list_prompts()).prompts)
```

`server_info` is the name and version the server reports for itself, which under this revision arrives in the `io.modelcontextprotocol/serverInfo` entry of each result's `_meta`, as section 1's result showed; `protocol_version` is the revision in use.

The SDK's `list_tools`, `list_resources`, `list_resource_templates`, and `list_prompts` all take `cursor` for pagination and `cache_mode` for the client-side cache the SDK keeps, because under 2026-07-28 every complete list result carries `ttlMs` and `cacheScope`. `read_resource` takes `cache_mode` as well. `call_tool` and `get_prompt` do not, and the reason is in the schema: neither `CallToolResult` nor `GetPromptResult` extends `CacheableResult`, so neither carries a freshness hint. `snippets/resources_prompts.py` prints all three facts from the SDK itself, and what the post 05 server puts on its lists:

```
tools/list                 ttlMs=0 cacheScope='private'
resources/list             ttlMs=0 cacheScope='private'
resources/templates/list   ttlMs=0 cacheScope='private'
prompts/list               ttlMs=0 cacheScope='private'
```

A `ttlMs` of 0 means the result should be treated as stale at once (the Caching page), so the cache that `Client`'s default `CacheConfig()` keeps never serves this server from memory; passing `cache=None` turns it off altogether. Deterministic ordering across requests, which is what makes a list worth caching, is only a **SHOULD** on the Tools page, so a client still has to be correct when a server shuffles.

Here is discovery from the outside. The project's command line has a `list` command that connects to every configured server and prints the catalog. `snippets/cli_run.py` runs that command in process against a servers file whose one entry starts the post 05 server over stdio:

```
$ mcp-host list
[ok] system-info: system-info  (protocol 2026-07-28)

system-info
  get_system_info  [read-only]
      Read current CPU, memory, and disk usage for this machine.
  find_process  [read-only]
      Find running processes whose name contains `name`, case-insensitively.
  terminate_process  [DESTRUCTIVE]
      Terminate a running process, after the user confirms.
  watch_cpu  [read-only]
      Sample CPU usage once a second and report the series.
```

The double space after the second `system-info` is the server's version, printed as the empty string it is: post 05's server passes no `version` to `MCPServer`. The `[read-only]` and `[DESTRUCTIVE]` tags come from each tool's `annotations`. They are the server's own claims about itself, the Tools page says a client **MUST** treat them as untrusted unless they come from a trusted server, and printing them next to the tool name is roughly the extent of what a *client* should do with them. Turning them into a decision is the host's job and the subject of the next post.

One naming note that matters later. Two different things in that transcript are both spelled `system-info`, and only one of them belongs to the host. The heading over the tool list is the key from the configuration file, which the user chose and which is unique on that machine. The name printed after `[ok]` is what the server reports for *itself*, and it happens to match here. The Tools page says a server's name is not guaranteed to be unique and **SHOULD NOT** be relied upon to tell servers apart, so a host that must separate two identically named tools keys on the configuration key. Building that catalog is [post 11](../11-building-a-host/index.md), which qualifies **both** sides of a collision rather than only the newcomer and leaves every uncontested name alone.

## 6. Calling a tool, and reading the result properly

This is the section the post is for.

A `tools/call` comes back in one of three shapes. Two of the three are failures, and only one of those two looks like one. `snippets/wire_frames.py` starts the post 05 server again and writes three requests to it by hand, with no SDK in the way, then prints each request's `params` and the line that came back:

```
-> {"name": "find_process", "arguments": {"name": "zzz-no-such-process"}}
<- {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"{\n  \"query\": \"zzz-no-such-process\",\n  \"total_matches\": 0,\n  \"returned\": 0,\n  \"processes\": []\n}","type":"text"}],"isError":false,"resultType":"complete","structuredContent":{"query":"zzz-no-such-process","total_matches":0,"returned":0,"processes":[]},"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"system-info","version":""}}}}
-> {"name": "get_sysinfo", "arguments": {}}
<- {"jsonrpc":"2.0","id":2,"result":{"content":[{"text":"Unknown tool: get_sysinfo","type":"text"}],"isError":true,"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"system-info","version":""}}}}
-> {"name": "find_process", "arguments": "not-an-object"}
<- {"jsonrpc":"2.0","id":3,"error":{"code":-32602,"message":"Invalid request parameters","data":""}}
```

The third has no `result` member at all, and the SDK's `call_tool` raises on it: `-32602` (`Invalid params`) for a request whose `arguments` is not an object. The second, like section 1's result, is an ordinary success with a flag set, and over HTTP such a result travels with status 200, which `snippets/transports.py` confirms (`find_process with a bad argument: HTTP 200, isError true, resultType 'complete'`). The comment on `isError` in the specification's schema says that is how it should be:

> Any errors that originate from the tool SHOULD be reported inside the result object, with `isError` set to true, *not* as an MCP protocol-level error response. Otherwise, the LLM would not be able to see that an error occurred and self-correct.

So the split is deliberate. The Tools page calls a protocol error something the model is less likely to be able to fix: an unknown tool, a malformed request, a server fault. A tool execution error is something it *can* fix: a date in the past, a value out of range, an application programming interface (API) that answered 503. The first is a JSON-RPC error, the second is `isError: true`, and a client that conflates them makes both worse.

One honest divergence, which [post 05](../05-first-server/index.md) also notes. The Tools page lists an unknown tool among the protocol errors and prints its example as `-32602`; the SDK, at release 2.3.0, answers the misspelled `get_sysinfo` above with an `isError` result instead. A client written to this post's rule reads both channels, so it handles either answer.

![A decision flow for one tool result. The first branch asks whether the call raised, and a raised call becomes a failed outcome. Otherwise the flow reads isError first, before anything else, and a true value becomes the same failed outcome, so both failure paths converge on one shape. On the success path, structuredContent is taken when present, and each content block is dispatched by its type through five branches for text, image, audio, resource link, and embedded resource, plus a sixth fallback branch for an unknown type from a newer server. Image, audio, and binary resources are summarized rather than inlined so their base64 never reaches the model.](diagrams/02-result-handling.svg)

*Two failure paths, one outcome shape. `isError` is read before the content, and the content has six branches, not one.*

### The flag is a plain `bool`

Two facts worth pinning, printed by `snippets/lying_client.py` against `mcp==2.3.0`:

```
FieldInfo(annotation=bool, required=False, default=False, alias='isError', alias_priority=1)
1 validation error for CallToolResult
is_error
  Input should be a valid boolean [type=bool_type, input_value=None, input_type=NoneType]
```

`is_error` is a plain `bool` with a default of `False`. It is **not** `bool | None`, so absent and false mean the same thing, as the specification also says, and a client must treat them that way. Passing `None` explicitly is not a way of saying "unknown"; it is a validation error.

On the wire the field is `isError`; the Python SDK exposes it as `is_error`. Same rule as everywhere else in this series: camelCase on the wire, snake_case in Python.

### One outcome type, two failure paths

The fix is not "remember to check `is_error` at every call site". The fix is to make it impossible to forget, by giving the caller a type that has already folded both failures into one flag. From [src/mcp_host/results.py](../../code/10-mcp-client/src/mcp_host/results.py):

```python
def read_result(tool: str, result: CallToolResult) -> ToolOutcome:
    """Turn a `CallToolResult` into something a host can branch on.

    `result.is_error` is checked explicitly, and it is the first thing
    checked. It is optional on the wire and defaults to False, so absent and
    false mean the same thing; and it is *not* an exception, so nothing
    upstream of here will have caught it.
    """
    blocks = tuple(describe_block(b) for b in (result.content or []))
    return ToolOutcome(
        tool=tool,
        ok=not bool(result.is_error),
        blocks=blocks,
        structured=result.structured_content,
        failure=None if not result.is_error else "server reported isError",
    )
```

and, one level up, in `connection.py`:

```python
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolOutcome:
        """Call a tool and come back with something safe to branch on.

        Both failure modes are folded into `ToolOutcome.ok`:

        - the call raised, meaning the transport or the protocol failed;
        - the call returned with `is_error`, meaning the tool itself failed.

        The broad `except Exception` is deliberate and is the one place in
        this project where one belongs. A host that dies because one tool call
        blew up is worse than a host that tells the model the call blew up.
        """
        try:
            result = await self.client.call_tool(name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - see docstring
            return ToolOutcome.failed(name, f"{type(exc).__name__}: {exc}")
        return read_result(name, result)
```

What matters is that both paths, raised and returned, converge on `ToolOutcome.ok is False`, so downstream code has one shape to handle rather than two. On section 1's result, `snippets/lying_client.py` prints `read_result: ok=False` and the first line of what the model is sent:

```
TOOL FAILED (find_process): Error executing tool find_process: 2 validation errors for find_processArguments
```

The test that keeps this honest drops one level below the wrapper and looks at what the SDK actually handed back, in [tests/test_client.py](../../code/10-mcp-client/tests/test_client.py):

```python
async def test_the_raw_result_would_have_looked_successful():
    """Same call, one level lower, to show what `read_result` is protecting
    against: the response is a perfectly ordinary CallToolResult."""
    async with Client(system_info_server) as client:
        raw = await client.call_tool("find_process", {"limit": "not-a-number"})

        assert raw.is_error is True
        assert raw.result_type == "complete"  # a *successful* response
        assert read_result("find_process", raw).ok is False
```

Read the two middle assertions together. `is_error` is `True` **and** `result_type` is `"complete"`, on the same object, from a call that did not raise. That is the whole defect in two lines, and it is why the checking cannot live in an `except` clause.

### Five content block types, and a sixth branch

`content` is a list of blocks, and the union has five members, the same five [post 06](../06-tools-in-depth/index.md) writes from the server's end. Handling only one of them is the second defect from section 1. The fields below are the ones the specification marks required.

| `type` | Required fields | What a client should do with it |
|---|---|---|
| `text` | `text` | Use it. This is the common case. |
| `image` | `data` (base64), `mimeType` | Display it, or describe it. Never paste the base64 into the model's context. |
| `audio` | `data` (base64), `mimeType` | The same. |
| `resource_link` | `uri`, `name` | A pointer, not the content. Decide whether to follow it. |
| `resource` | a nested `resource` with `uri`, plus `text` or `blob` | Inline the text form; summarize the blob form. |

`describe_block` in `results.py` flattens each of them into a `kind`, a rendering safe to put in front of a model, and a `data` dictionary keeping what a host might act on. `snippets/content_blocks.py` passes one block of each type through it, plus one whose type no revision defines:

```
TextContent      kind=text           12 processes
ImageContent     kind=image          image (image/png, 4096 bytes of base64, not inlined)
AudioContent     kind=audio          audio (audio/wav, 1024 bytes of base64, not inlined)
ResourceLink     kind=resource_link  resource link: top <system://processes/top>
EmbeddedResource kind=resource       resource config://app: | debug = false
EmbeddedResource kind=resource       resource file:///x.bin (application/octet-stream, 2048 bytes of base64, not inlined)
FutureContent    kind=unknown        unsupported content block: hologram
```

(The script prints a newline inside a rendering as ` | `.) Two of its choices are worth arguing for.

**Binary payloads are described, never inlined.** A screenshot can be a megabyte of base64, and pasting it into a transcript costs a large share of the model's context and tells the model nothing, so the rendering names the type and the size while `block.data["data"]` still holds the payload for a host that wants to draw it (`the image payload is still kept for the host: 4096 characters`).

**An unknown block type is a placeholder, not a crash.** The content union is versioned, and a newer server may one day send a type this client has never heard of. The final branch of the `isinstance` chain in `describe_block` catches it:

```python
    kind = getattr(block, "type", None) or type(block).__name__
    return Block("unknown", f"unsupported content block: {kind}", {"type": kind})
```

A client that raises on an unrecognized `type` fails an entire tool call because one block in it came from the future.

### `structuredContent` is a separate field

If a tool declares an `outputSchema`, its result carries `structuredContent` as well as `content`, and that is the machine-readable form of the same answer. On the wire it is `structuredContent`; the Python SDK exposes it as `structured_content`. It can be any JSON value since SEP-2106 (Specification Enhancement Proposal 2106) loosened it from an object, so a client must not assume a dictionary; the SDK types it as `Any`.

The Tools page says a tool that returns structured content **SHOULD** also return the serialized JSON in a text block, for backward compatibility, which is what the first result in this section carries. So a client that reads only `content` usually gets *something*, and that "usually" is what makes the bug hard to notice. `ToolOutcome` keeps both, and falls back to the structured form when there are no text blocks at all; `snippets/content_blocks.py` prints `structuredContent and no text block: [3, 1, 2]` for a result whose only payload is a list.

Finally, **concurrent `call_tool` calls on one client are safe.** Request identifier correlation is the client's job, so `asyncio.gather` over several calls on one connection needs no lock. `snippets/wire_frames.py` sends three `find_process` calls at once over one stdio connection:

```
request ids on the wire: [2, 3, 4]
each result's query, in the order gather returned them: ['zzz-a', 'zzz-b', 'zzz-c']
every answer matched its call: True
```

[Post 11](../11-building-a-host/index.md) leans on that hard.

## 7. Handling `input_required` from the client side

[Post 08](../08-elicitation-and-mrtr/index.md) built Multi Round-Trip Requests (MRTR) from the server's side. Here is the same mechanism seen from the client, which is where it stops being abstract, because the client is now the one that has to answer.

Under revision 2026-07-28 a server has no back channel. It cannot call into the client and wait. So a tool that needs a human answer does not ask, it **returns**, and the client calls the same tool again with the answer attached. `snippets/mrtr_client.py` drives `terminate_process` by hand over stdio for process identifier (PID) 999999, which is not running, so nothing can be terminated. This is the first leg, printed in full because the shape is the lesson, except that the script replaces the sealed part of `requestState`, which differs on every run, with `<sealed>`:

```json
{
  "_meta": {
    "io.modelcontextprotocol/serverInfo": {
      "name": "system-info",
      "version": ""
    }
  },
  "resultType": "input_required",
  "inputRequests": {
    "system_info.interactive:_confirm_terminate": {
      "method": "elicitation/create",
      "params": {
        "mode": "form",
        "message": "Terminate process 999999 (unknown)? This cannot be undone.",
        "requestedSchema": {
          "properties": {
            "confirm": {
              "description": "Confirm that this process should be terminated.",
              "title": "Confirm",
              "type": "boolean"
            },
            "reason": {
              "default": "",
              "description": "Optional note recorded in the server log.",
              "title": "Reason",
              "type": "string"
            }
          },
          "required": [
            "confirm"
          ],
          "type": "object"
        }
      }
    }
  },
  "requestState": "v1.<sealed>"
}
```

Three things to read off it. `inputRequests` is a **map**, keyed by identifiers the server chose, so a server may ask several questions in one round and the client must answer them by key. The values are bare request objects with a `method` and `params` and no JSON-RPC envelope, because they are not requests on the wire. And `requestState` is an opaque string, about 400 characters long in this post's runs, which the MRTR page says the client **MUST** echo back exactly and **MUST NOT** inspect, parse, or modify.

The reply is the same tool, the same arguments, a **new** JSON-RPC identifier, and two extra parameters. These are the retry's `params` as the client wrote them, with `_meta` left out and `requestState` shortened again:

```json
{
  "inputResponses": {
    "system_info.interactive:_confirm_terminate": {
      "action": "accept",
      "content": {
        "confirm": false
      }
    }
  },
  "requestState": "v1.<sealed>",
  "name": "terminate_process",
  "arguments": {
    "pid": 999999
  }
}
```

The script prints the identifiers of the two legs and checks the echo: `JSON-RPC ids of the two legs: 2 and 3; requestState echoed byte for byte: True`. The second leg completes:

```json
{
  "_meta": {
    "io.modelcontextprotocol/serverInfo": {
      "name": "system-info",
      "version": ""
    }
  },
  "content": [
    {
      "type": "text",
      "text": "Not terminated. The user answered no for process 999999."
    }
  ],
  "structuredContent": {
    "result": "Not terminated. The user answered no for process 999999."
  },
  "isError": false,
  "resultType": "complete"
}
```

Note that "the user said no" came back as a **successful** result with `isError` false. A refusal is an outcome, not an error; a server that reported it as `isError: true` would tell the model that the tool had failed.

There are two ways to be the client in that exchange, and one thing that is not optional in either of them.

![Three lanes under a band stating that passing an elicitation callback is what puts the elicitation capability into the clientCapabilities carried on every request, and that the manual path never invokes the callback and still needs one. The first lane connects without a callback and dead-ends: the server refuses with -32021, MISSING_REQUIRED_CLIENT_CAPABILITY, before it ever asks. The second lane hands the SDK a callback and lets it drive the retry. The third lane drives the same loop by hand through allow_input_required, answering each key the server chose. Both converge on one CallToolResult, and a band across the bottom explains the round cap and the question digest.](diagrams/03-two-ways-to-answer.svg)

*The two paths differ only in who writes the loop. Neither runs at all without a callback on the `Client`.*

### The automatic path

In practice a host does not write that loop. Hand `Client` an `elicitation_callback` and the SDK drives the whole thing: it dispatches each entry of `inputRequests` to the callback, retries with the answers and the echoed `request_state`, and returns a plain `CallToolResult`. The loop is bounded by `input_required_max_rounds`, which defaults to 10, and running past it raises `InputRequiredRoundsExceededError`. `snippets/mrtr_client.py` lets the SDK drive the same call three times, with a callback that accepts with `confirm` false, one that declines, and one that cancels:

```
Client.input_required_max_rounds defaults to 10
accept, confirm false  isError=false  Not terminated. The user answered no for process 999999.
decline                isError=false  Not terminated. The user declined to confirm ending process 999999.
cancel                 isError=false  Not terminated. The confirmation was dismissed for process 999999.
```

`interactive.py` supplies the callback, and its interesting part is not the adapter but the console responder underneath it. From [src/mcp_host/interactive.py](../../code/10-mcp-client/src/mcp_host/interactive.py):

```python
async def _ask(prompt: str) -> str:
    """Read one line without stalling the event loop.

    An EOF (piped input that ran out, a closed terminal) reads as an empty
    answer, which every caller below treats as "no". Crashing the host because
    stdin closed would be the wrong failure.
    """
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return ""
```

**Never call bare `input()` inside a coroutine.** It blocks the event loop for as long as the person takes to read the question. Nothing else progresses, no other server's call completes, and every timeout in the process waits on a loop that is not running. It is one of the easiest ways to make an async host feel broken, and `asyncio.to_thread` costs one line. The `EOFError` branch matters too: piped input that runs out, or a closed terminal, should read as "no", not as a crash.

### The manual path

Driving it by hand is still worth knowing, because a host that batches several servers' questions into one dialog has to. `call_with_input` in `interactive.py` does it through the session, whose `call_tool(..., allow_input_required=True)` returns the `InputRequiredResult` instead of resolving it:

```python
    result = await session.call_tool(name, arguments, allow_input_required=True)

    for _ in range(max_rounds):
        if not isinstance(result, InputRequiredResult):
            return result

        responses: dict[str, Any] = {}
        for key, request in (result.input_requests or {}).items():
            responses[key] = await _fulfil(request, responder)

        result = await session.call_tool(
            name,
            arguments,
            allow_input_required=True,
            input_responses=responses,
            request_state=result.request_state,
        )
```

Three rules hold that together, and breaking any of them makes the call fail or spin until it hits the cap.

1. **Same tool name and the same arguments on every leg.** The arguments are not "already sent". Each leg is a complete, independent request that may land on a different server instance.
2. **Echo `request_state` verbatim.** The server encodes the state of the exchange in it, and the SDK's server seals it and binds it to the request. `snippets/mrtr_client.py` retries once with one character of it changed and once with it attached to other arguments, and the post 05 server refuses both the same way: `MCPError(-32602, 'Invalid or expired requestState', {'reason': 'invalid_request_state'})`.
3. **Key the responses by the server's keys.** They are strings the server chose; in the capture above the key happens to be `module:qualname`, and a client must not depend on that.

There is also a rule about the *server's* side that bites client authors. The SDK's server matches a recorded answer against a digest of the exact question text. If a server rewords its question between rounds, by including a timestamp or a live reading, the answer never matches, the server asks again, and the call runs to the round cap. From the outside it looks as though the user's answer was ignored. `snippets/mrtr_client.py` builds a throwaway server whose question carries a counter, and calls it with the cap set to 3:

```
InputRequiredRoundsExceededError: Server returned InputRequiredResult for more than 3 rounds; raise input_required_max_rounds on the Client, or use client.session.<method>(..., allow_input_required=True) to drive the loop manually.
questions the callback answered: 3
```

The project pins the good behavior of the post 05 server by counting, in [tests/test_client.py](../../code/10-mcp-client/tests/test_client.py):

```python
async def test_the_question_is_asked_once_per_call():
    """A server that reworded its question between rounds would never
    converge; the loop would run to its cap. One question means the digest
    matched."""
    responder = ScriptedResponder("decline")
    async with open_connection(spec(), elicitation_callback=declining()) as conn:
        await call_with_input(conn, "terminate_process", {"pid": 4242}, responder)

    assert len(responder.seen) == 1
```

### Passing a callback is what declares the capability

This is the trap, and it is worth its own paragraph because the manual path never invokes the callback, which makes it look optional.

**It is not optional.** A `Client` with no `elicitation_callback` declares no elicitation capability in its per-request `_meta` (section 3's frame shows the declaration a callback adds), and the Base protocol page requires a server that needs an undeclared capability to refuse with `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`) and a `data.requiredCapabilities` member naming what is missing. Here is the real one, raised by `client.call_tool` in `snippets/mrtr_client.py` against a client that connected without a callback:

```
MCPError(-32021, "Client did not declare the form elicitation capability required by resolver 'system_info.interactive:_confirm_terminate'", {'requiredCapabilities': {'elicitation': {'form': {}}}})
```

The server refused before it asked anything, and the data names exactly what to add. Note the resolver in the message: it is the same key that appears in `inputRequests` when the call gets that far.

The manual path still needs a callback on the `Client`, purely as the declaration. Through `open_connection` the same refusal becomes a failed outcome rather than a crash (`through open_connection: ok=False`), and the project pins that with a test of its own, because otherwise the bug report says "the tool is broken".

Two of the three request types a server may put in `inputRequests` are deprecated in this revision: `sampling/createMessage` and `roots/list` still appear in the union, and elicitation is the only client feature that is not deprecated. The project answers `roots/list` with an empty list, an honest answer for a host that exposes no filesystem roots, and refuses sampling outright.

## 8. Resources and prompts

Tools get the attention. The other two primitives, which [post 07](../07-resources-and-prompts/index.md) covers from the server's side, take a few lines between them. From `snippets/resources_prompts.py`:

```python
        read = await client.read_resource("system://processes/top")
        print(f"resources/read system://processes/top: first line {read.contents[0].text.splitlines()[0]!r}")
        prompt = await client.get_prompt("diagnose_performance", {"symptom": "slow"})
```

which prints:

```
resources/read system://processes/top: first line '| PID | Name | Memory (MB) |'
prompts/get diagnose_performance: 1 message(s), role 'user'
```

Two things a client author should know. First, `resources/read` and `prompts/get` take part in MRTR exactly as `tools/call` does, because all three methods' parameters extend the same retry parameters in the schema; any of them can answer with `input_required` and expect a retry, and the SDK's `read_resource` and `get_prompt` drive that loop just as `call_tool` does. Those three methods, and only those three, take `inputResponses` and `requestState`.

Second, resource-not-found is `-32602` in this revision, not `-32002`. The Resources page says a server **MUST** answer a missing resource with `-32602` and a client **SHOULD** still accept `-32002` from an older server, which used that code. Asking the post 05 server for a Uniform Resource Identifier (URI) it does not have raises, because it is a protocol error and not a tool failure:

```
MCPError(-32602, 'Unknown resource: system://does/not/exist', {'uri': 'system://does/not/exist'})
```

## 9. When not to write a client

**When the goal is only to test a server.** `Client(server_object)` in a test file is three lines and gives the real request path. No wrapper is needed for that, and [post 12](../12-testing-and-debugging/index.md) shows the pattern properly.

**When an existing host will do.** If the users already live in an editor or a desktop application that speaks MCP, shipping a server and a configuration snippet is a smaller product than shipping a client.

**When nobody has decided what the host is for.** A client is transport plumbing. The interesting decisions (which model, which tools to expose, what to prompt about) live in the host, and building the plumbing first can hide the fact that nobody has made them.

**When conformance matters more than control.** A client shipped to other people has to be right about every server it meets, and needs the conformance suite to find the dialect its authors invented without noticing.

## 10. Make it run: the project, the command line, and the scripts behind this post

The project is the post's code. From inside `code/10-mcp-client/`, which depends on `code/05-first-server/` by path, so syncing one syncs both:

```bash
uv sync --extra dev                                    # once: the environment, with the test tools
uv run python -m mcp_host list                         # connect to servers.example.json, print the catalog
uv run python -m mcp_host inspect system-info          # one server's tools, resources and prompts
uv run python -m mcp_host call find_process --args '{"limit": "not-a-number"}'
uv run --extra dev pytest                              # the suite
```

The suite:

```
tests\test_catalog.py .............                                      [ 19%]
tests\test_client.py ......................                              [ 51%]
tests\test_permissions.py ..................                             [ 77%]
tests\test_results.py ...............                                    [100%]

============================= 68 passed in 7.81s ==============================
```

Sixty-eight tests across posts 10 and 11, every one that needs a server connecting to post 05's in memory. The count is the result; the seconds vary by machine. Without a `servers.json` of its own the command line falls back to `servers.example.json` and says so on its first line (`[info] no servers.json; using servers.example.json`).

The `isError` path, end to end through the command line, calling `find_process` with a string where an integer belongs, as `snippets/cli_run.py` runs it:

```
$ mcp-host call find_process --args '{"limit": "not-a-number"}'
[failed] TOOL FAILED (find_process): Error executing tool find_process: 2 validation errors for find_processArguments
name
  Field required [type=missing, input_value={'limit': 'not-a-number'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.14/v/missing
limit
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='not-a-number', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/int_parsing
(exit code 1)
```

The prefix is the whole point. `TOOL FAILED (find_process):` is added by `ToolOutcome.for_model`, and it is what the model sees. A model can recover from a tool error it can read; it cannot recover from one that arrives disguised as an answer.

Everything the project prints is plain American Standard Code for Information Interchange (ASCII), on purpose: when output on Windows goes to a pipe or a file, Python encodes it with the system code page, often cp1252, which has no checkmark and no arrow, so `print` raises `UnicodeEncodeError` and the traceback points at the output code rather than at the glyph. `[ok]` and `->` render everywhere.

Eight scripts under `snippets/` print every transcript quoted above that the suite does not. Each runs from the series root with no arguments, in about ten seconds or less here, through the root environment that pins `mcp[cli]==2.3.0`, `psutil` and `httpx<1`. Each imports the project from `code/10-mcp-client/src/`, the server from `code/05-first-server/src/`, or both, starts any server it needs itself, and stops it before it exits; none needs a host or any network beyond 127.0.0.1.

```bash
uv run python posts/10-mcp-client/snippets/lying_client.py       # sections 1 and 6: the naive client, the raw result, isError's type
uv run python posts/10-mcp-client/snippets/wire_frames.py        # sections 3 and 6: the frames a client writes, three result shapes, concurrent calls
uv run python posts/10-mcp-client/snippets/transports.py         # sections 3, 6 and 11: memory, stdio, HTTP; headers; a trailing slash; stdio://
uv run python posts/10-mcp-client/snippets/lifetimes.py          # sections 4 and 11: the hand-rolled leak, open_connection, a timedelta
uv run python posts/10-mcp-client/snippets/resources_prompts.py  # sections 5 and 8: cache hints, a resource, a prompt, -32602
uv run python posts/10-mcp-client/snippets/content_blocks.py     # section 6: all five content block types and an unknown one
uv run python posts/10-mcp-client/snippets/mrtr_client.py        # section 7: both legs by hand, the callback, -32021, the round cap
uv run python posts/10-mcp-client/snippets/cli_run.py            # sections 5 and 10: the command line's list and call
```

`transports.py` and `lifetimes.py` together are this post's end-to-end check: every transport through one code path, a tool failure that no `except` sees, and the process table read after each connection closes. `cli_run.py` runs the command line in process with a temporary servers file pointing at the root environment's interpreter, so its first line differs from the project's own run in the way just described.

## 11. What can go wrong?

Three failures belong to the sections that explain them: the `ExceptionGroup` that an `except` around a whole `async with` block never catches (section 4), the missing elicitation callback that turns every question into `-32021` (section 7), and the reworded question that runs a call to the round cap (section 7). Three more are mistakes of the first hour, each printed by one of the scripts above.

**Passing `"stdio://..."` to `Client`.** A string is always an HTTP URL, so the HTTP library rejects the scheme, and the error arrives wrapped, as section 4 explained. `snippets/transports.py` prints:

```
raised ExceptionGroup; inside it: UnsupportedProtocol: Request URL has an unsupported protocol 'stdio://'.
```

A stdio server goes in as `StdioServerParameters(...)`, or as `stdio_client(StdioServerParameters(...))`.

**A `timedelta` for `read_timeout_seconds`.** 1.x code passes one; 2.x wants a float of seconds, and nothing checks the type at the constructor. The failure arrives at the first request, inside the timeout arithmetic, naming no parameter at all, as `snippets/lifetimes.py` prints:

```
escaped as ExceptionGroup; inside it: TypeError: unsupported operand type(s) for +: 'float' and 'datetime.timedelta'
```

**A trailing slash on the endpoint path.** The post 05 server, mounted at `/mcp`, answers a POST to `/mcp/` with a redirect and an empty body rather than 404. The SDK's client follows a redirect that keeps the endpoint's origin and the request method, so it connects anyway; a hand-written client that does not follow redirects sees nothing worth reading:

```
POST /mcp/ without following redirects: HTTP 307, Location http://127.0.0.1:PORT/mcp, body 0 bytes
the SDK client on /mcp/: connected, 4 tools
```

(`PORT` stands in for the free port the script picked.)

## Common pitfalls

1. **Wrapping `call_tool` in `try`/`except` and calling it error handling.** A tool that ran and failed returns a normal result with `isError: true` and `resultType: "complete"`, so check the flag explicitly and fold both failure paths into one outcome type.
2. **Reading `.text` off every content block.** Four of the five block types have no `text` attribute, so images, audio, resource links and embedded resources vanish silently, and `structuredContent` is a separate field again.
3. **Entering a client by hand and closing it from another task.** The close raises a cancel-scope `RuntimeError`, and a second context exited after it never runs, which leaks the subprocess; use `async with` or one `AsyncExitStack` in one task.
4. **Calling bare `input()` inside a coroutine.** It stalls the event loop, and every other in-flight call with it, for as long as the user reads; `asyncio.to_thread(input, prompt)` is the whole fix.
5. **Sending capabilities once.** There is no handshake and no session, so the protocol revision and the client's capabilities travel in `_meta` on every request, and a server must not infer them from an earlier one.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `isError` rule, the five content block types, structured content, annotations as untrusted, and the split between a protocol error and a tool execution error. <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- Specification, *"Multi Round-Trip Requests (MRTR)"*, revision 2026-07-28, and SEP-2322. The `inputRequests` map, the `requestState` rules, and the requirement that the retry carry a different JSON-RPC identifier. <https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr>
- Specification, *"Base protocol"*, revision 2026-07-28. The `_meta` carried on every request and the error codes, including `-32021` and its `requiredCapabilities` payload. <https://modelcontextprotocol.io/specification/2026-07-28/basic>
- Specification, *"Transports: Streamable HTTP"*, revision 2026-07-28, and SEP-2243. The `Mcp-Method` and `Mcp-Name` routing headers shown in section 3. <https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>
- Specification, *"Resources"* and *"Caching"*, revision 2026-07-28. Resource-not-found as `-32602`, and what `ttlMs` and `cacheScope` mean. <https://modelcontextprotocol.io/specification/2026-07-28/server/resources> and <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching>
- MCP Python SDK, release 2.3.0. `Client`, `StdioServerParameters`, `streamable_http_client`, `CacheConfig`, and the input-required driver. <https://github.com/modelcontextprotocol/python-sdk>
- [code/10-mcp-client/](../../code/10-mcp-client/). The complete project, with the suite run in section 10.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 11 - Building a host: the tool loop, many servers, and permissions](../11-building-a-host/index.md):** the same project four modules further on, with several clients behind one catalog, a model driving them, and the permission gate that decides which calls may happen.
- **[Post 12 - Testing and debugging MCP](../12-testing-and-debugging/index.md):** the in-memory pattern behind this project's client tests, and how to read a wire trace like the ones above.
