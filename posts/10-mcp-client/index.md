# 10 · Building your own MCP client

> **TL;DR.** A Model Context Protocol (MCP) client is a few hundred lines of code, and once
> you have written one, every host behavior you have ever wondered about becomes explicable.
> This post builds one: three transports behind a single lifetime rule, discovery, and a
> result reader that catches the failure most client code gets wrong. A tool that ran and
> failed arrives as a **successful** response carrying `isError: true`, so a `try`/`except`
> around the call never sees it.
>
> **After reading this you will be able to:**
> - Connect to a server over standard input and output, Streamable Hypertext Transfer Protocol (HTTP), or in memory, through one code path.
> - Read every content block type a tool can return, and branch on `isError` rather than on exceptions.
> - Answer a server that asks a question mid-call, both automatically and by hand.
> - Tear a connection down without leaking the subprocess it was holding.

![A client shown as one box between a host on the left and a server on the right. Inside the box, four stacked internal parts are named: the transport, request-id correlation, the capability cache, and the multi round-trip driver. A fifth part, the result reader, sits on the return path and carries a two-way branch for isError. Below the box a band states the lifetime rule: the client owns a task group, so it must be entered and exited by the same task.](diagrams/01-client-internals.svg)
*One client per connected server. Four parts going out, one branch coming back, and one lifetime rule holding all of it together.*

---

## 1. The client that lied to its model

Here is the shape almost every hand-written MCP client starts with, including the previous
edition of this post. It looks defensive. Read it and decide whether you would flag it in
review.

```python
try:
    result = await session.call_tool(tool_use.name, tool_use.input)
    text = "\n".join(c.text for c in result.content if hasattr(c, "text")).strip()
except Exception as e:
    text = f"TOOL_ERROR: {type(e).__name__}: {e}"
```

Six lines, three defects, and the first one is the reason this post exists.

**The `except` clause catches the wrong failures.** It catches the transport dying and the
protocol rejecting the message. It does not catch a tool that ran and failed, because that is
not an exception. It is a successful JavaScript Object Notation Remote Procedure Call
(JSON-RPC) response with a flag set in it. Here is a real one, produced by asking the
system-information server from [Post 05](../05-first-server/index.md) to find a process with a
string where an integer belongs:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Error executing tool find_process: 2 validation errors for find_processArguments\nname\n  Field required ...\nlimit\n  Input should be a valid integer, unable to parse string as an integer ..."
    }
  ],
  "isError": true,
  "resultType": "complete"
}
```

`resultType` is `"complete"`. Nothing raised. The `try` block finished normally, the `.text`
join collected the error message, and the host handed that string to the model as the answer
to its question. The model then explained the validation error to the user as though it were
a fact about their machine.

**The `.text` join throws away four of the five content block types.** `content` is a union of
text, image, audio, resource links, and embedded resources. Reading `.text` off each block and
dropping anything without one silently discards screenshots, audio, and pointers to large
files. It also ignores `structuredContent`, which is a separate field carrying the
machine-readable form of the same answer, and which is usually the one you actually want.

**And the connection it runs on leaks.** That one takes a section of its own, and it is
section 4.

The rest of this post is the client that gets all three right. The complete project is
[code/10-mcp-client/](../../code/10-mcp-client/), it has 68 tests, and every transcript below
came from running it.

## 2. Client and host, one more time

This series uses three words precisely, and this is the post where the distinction stops being
pedantic and starts being a file layout.

- The **host** is the application a person interacts with. It owns the model, the
  conversation, and every permission decision. Claude Desktop, an integrated development
  environment, a command-line interface (CLI) you wrote.
- A **client** is the protocol-speaking object inside the host. There is exactly one per
  connected server, and it owns the transport, request-identifier correlation, and the
  capability cache.
- The **server** is the process that exposes tools, resources, and prompts.

A host contains clients, plural. If you connect to four servers you have four clients, four
transports, and four independent lifetimes, and none of them knows the others exist. Merging
their catalogs into one list the model can see is the host's job, and it is
[Post 11](../11-building-a-host/index.md).

This post builds only the client half: `connection.py`, `results.py`, and `interactive.py` in
the project. [Post 11](../11-building-a-host/index.md) adds `catalog.py`, `permissions.py`,
`providers.py`, and `loop.py` on top of them.

## 3. Three transports, one `Client`

Wire format before code, as always. Over standard input and output (stdio) a request is a
line of JSON written to a subprocess's standard input:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_system_info","arguments":{},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"mcp","version":"0.1.0"},"io.modelcontextprotocol/clientCapabilities":{"elicitation":{"form":{},"url":{}}}}}}
```

That is the second line this client wrote, which is why the identifier is 2. The first was
`server/discover`, sent the moment the connection opened, and section 5 comes back to it.

Over Streamable HTTP the same JSON is a POST body, with the routing headers
[Post 04](../04-transports/index.md) covered:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_system_info
```

Same message, different envelope. That is the entire difference, and it is why one client
class covers both. Note the `_meta` block: revision 2026-07-28 has no `initialize` handshake
and no session, so the protocol revision and the client's capabilities ride on **every**
request. A client that sends them once and stops is not a client. `clientInfo` travels with
them, which the specification makes a **should** rather than a must, and the pinned software
development kit (SDK) sends it on every request too.

In the Python SDK, `Client` dispatches on the *shape* of its first argument:

```python
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

Client(mcp)                                            # an MCPServer object: in process
Client("https://example.com/mcp")                      # a str: always an HTTP URL
Client(stdio_client(StdioServerParameters(...)))       # anything else: a transport
Client(streamable_http_client(url, http_client=...))   # ditto, with headers and timeouts
```

**There is no `Client("stdio://...")` form.** A plain string always means an HTTP Uniform
Resource Locator (URL). This surprises people who expect a scheme-based factory, and the error
you get is a connection failure to a nonsense host rather than anything about stdio.

The project wraps that dispatch in one dataclass so a configuration file can drive it. From
[src/mcp_host/connection.py](../../code/10-mcp-client/src/mcp_host/connection.py):

```python
async def _transport_for(spec: ServerSpec, stack: AsyncExitStack) -> Any:
    if spec.transport == "memory":
        return spec.server

    if spec.transport == "http":
        if not spec.headers:
            return streamable_http_client(spec.url)
        import httpx2

        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(headers=spec.headers, timeout=30.0)
        )
        return streamable_http_client(spec.url, http_client=http_client)

    params = StdioServerParameters(
        command=spec.command, args=spec.args, env=spec.env, cwd=spec.cwd
    )
    return stdio_client(params)
```

Five details in that function will each cost you an afternoon if you meet them cold.

1. **The import is `httpx2`, not `httpx`.** The 2.x SDK ships `httpx2`. Import `httpx` here
   and you get a different library, whose exception types the SDK will never raise and whose
   client object it will not accept.
2. **The function is `streamable_http_client`.** In the 1.x line it was `streamablehttp_client`
   with no underscore before `http`, and the `get_session_id` callback that used to come with
   it is gone along with sessions. It yields a **2-tuple**, not the 3-tuple of the older
   signature.
3. **`read_timeout_seconds` is a float of seconds.** In 1.x it was a `timedelta`. Passing a
   `timedelta` now is a type error at a place that does not name the parameter.
4. **The trailing slash matters.** `POST /mcp/` against a server mounted at `/mcp` answers
   **307** with a `Location` of `/mcp` and an empty body, not 404. If your client does not
   follow redirects you see nothing back and no error worth reading.
5. **The in-memory case is not a test double.** `Client(server_object)` runs a real client
   against a real server object through the full protocol path, with no subprocess and no
   socket. It is how this project's suite runs in seconds, and
   [Post 12](../12-testing-and-debugging/index.md) is built on it.

Configuration uses the `mcpServers` shape that desktop hosts have converged on, so a file that
works here mostly works there. From
[servers.example.json](../../code/10-mcp-client/servers.example.json):

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

Two keys and two shapes: `{command, args, env, cwd}` for stdio, `{url, headers}` for HTTP.
Use absolute paths in a real configuration. The process that spawns your server does not
inherit your working directory, and "server process failed to start" is what a relative path
looks like from the outside.

## 4. Lifetimes, and the subprocess the cleanup code leaked

Here is the third defect from section 1, in the form it usually takes:

```python
# Do not do this.
self._transport_cm = stdio_client(params)
self._read, self._write = await self._transport_cm.__aenter__()
self.session = ClientSession(self._read, self._write)
await self.session.__aenter__()
```

with the matching teardown:

```python
async def close(self):
    await self.session.__aexit__(None, None, None)
    await self._transport_cm.__aexit__(None, None, None)
```

That reads as careful code. It has two bugs, and they compound.

**The first is the task boundary.** The v2 `Client` wraps an `anyio` task group, and a task
group must be exited by the task that entered it. `close()` runs in whatever task happens to
call it, which in a real host is a signal handler, a shutdown hook, or a different coroutine
entirely. The failure is `RuntimeError: Attempted to exit cancel scope in a different task
than it was entered in`, and it names nothing you wrote.

**The second is worse, because it is silent.** Look at the order of the two `__aexit__` calls.
If the session exit raises, the transport exit never runs, and the subprocess that `close()`
existed to reap survives. The cleanup method leaks exactly the resource it was written to
guard, and it does so only on the error path, which is the path you never test by hand.

`AsyncExitStack` fixes both. It unwinds every context it entered, in reverse order, on the
exception path as well as the normal one, and it does it in the task that owns it:

```python
@asynccontextmanager
async def open_connection(spec, *, elicitation_callback=None, read_timeout_seconds=None):
    async with AsyncExitStack() as stack:
        transport = await _transport_for(spec, stack)
        client = await stack.enter_async_context(
            Client(transport,
                   elicitation_callback=elicitation_callback,
                   read_timeout_seconds=read_timeout_seconds)
        )
        yield ServerConnection(spec, client)
```

The guarantee is stated as a test rather than as a comment, in
[tests/test_client.py](../../code/10-mcp-client/tests/test_client.py):

```python
async def test_the_connection_closes_cleanly_even_when_the_body_raises():
    captured = None
    with pytest.raises(BaseException) as excinfo:
        async with open_connection(spec()) as conn:
            captured = conn
            await conn.list_tools()
            raise ZeroDivisionError("boom")

    assert any(isinstance(e, ZeroDivisionError) for e in flatten(excinfo.value))
    outcome = await captured.call_tool("get_system_info", {})
    assert outcome.ok is False
    assert "closed" in outcome.for_model().lower()
```

The last two lines are the point. After the block unwinds, the connection is genuinely dead,
and a call through it fails cleanly instead of hanging on a pipe nobody is reading.

**Note `pytest.raises(BaseException)` and the `flatten` helper.** An exception escaping
`async with Client(...)` does not arrive as itself. The task group re-raises it wrapped in an
`ExceptionGroup`, sometimes nested two deep, so `except ZeroDivisionError` does not fire and
`pytest.raises(ZeroDivisionError)` does not match. The project ships four lines to deal with
it:

```python
def flatten(exc: BaseException) -> list[BaseException]:
    """Unwrap nested ExceptionGroups."""
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]
```

This is not only a testing concern. Any host code that writes `except ConnectionError` around
a client call has the same hole, and in production the symptom is an exception handler that
silently never runs.

## 5. Discovery: what the server has

There is no `initialize` in revision 2026-07-28. A client learns what a server is by calling
`server/discover`, and the SDK does that for you when the context manager opens; afterwards
the answers hang off the client as properties. The project exposes the ones a host needs:

```python
conn.server_info         # name and version the server reports for itself
conn.protocol_version    # the negotiated revision
await conn.list_tools()
await conn.list_resources()
await conn.list_prompts()
```

`list_tools`, `list_resources`, `list_resource_templates`, and `list_prompts` all take
`cursor` for pagination and `cache_mode` for the client-side cache the SDK keeps, because
since 2026-07-28 every complete list result carries `ttlMs` and `cacheScope`. Deterministic
ordering across requests, which is what makes that cache worth keeping, is only a **should**,
so a client still has to be correct when a server shuffles. `read_resource` takes `cache_mode`
as well. `call_tool` and `get_prompt` do not, and the reason is in the schema: neither
`CallToolResult` nor `GetPromptResult` extends `CacheableResult`, so neither carries a
freshness hint at all.

Here is the real thing. This is `python -m mcp_host list` against the system-information
server, launched as a subprocess over stdio:

```
[info] no servers.json; using servers.example.json
[ok] system-info: system-info 2.0.0b2 (protocol 2026-07-28)

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

The `[read-only]` and `[DESTRUCTIVE]` tags come from each tool's `annotations`. They are the
server's own claims about itself, nothing verifies them, and printing them next to the tool
name is roughly the extent of what a *client* should do with them. Turning them into a
decision is the host's job and the subject of the next post.

One naming note that matters later. Two different things in that transcript are both spelled
`system-info`, and only one of them is yours. The heading over the tool list is the key from
the configuration file, which you chose and which is unique on your machine. The name printed
after `[ok]` is what the server reports for *itself*, and it happens to match here. A
self-reported name is not guaranteed unique across servers, so a host that has to tell two
identically named tools apart keys on the configuration key and never on that one. Building
that catalog is [Post 11](../11-building-a-host/index.md), and it qualifies **both** sides of
a collision rather than only the newcomer, and leaves every uncontested name alone.

## 6. Calling a tool, and reading the result properly

This is the section the post is for.

A `tools/call` comes back in one of three shapes. Two of the three are failures, and only one
of those two looks like one.

```json
{ "resultType": "complete", "content": [...], "isError": false }
```

```json
{ "resultType": "complete", "content": [...], "isError": true }
```

```json
{ "error": { "code": -32602, "message": "Invalid params" } }
```

The third has no `result` member at all and your client raises on it. The second is a
perfectly ordinary HTTP 200 with a flag set, and the specification says that is correct:

> Any errors that originate from the tool SHOULD be reported inside the result object, with
> `isError` set to true, *not* as an MCP protocol-level error response. Otherwise, the LLM
> would not be able to see that an error occurred and self-correct.

So the split is deliberate. A protocol error is something the large language model (LLM) in
that quotation cannot fix: unknown tool, malformed request, server fault. A tool execution
error is something it *can* fix: a date in the past, a value out of range, an application
programming interface (API) that returned 503. The first is a JSON-RPC error, the second is
`isError: true`, and a client that conflates them makes both worse.

![A decision flow for one tool result. The first branch asks whether the call raised, and a raised call becomes a failed outcome. Otherwise the flow reads isError first, before anything else, and a true value becomes the same failed outcome so both failure paths converge on one shape. On the success path, structuredContent is taken when present, and each content block is dispatched by its type through five branches for text, image, audio, resource link, and embedded resource, plus a sixth fallback branch for an unknown type from a newer server. Image, audio, and binary resources are summarized rather than inlined so their base64 never reaches the model.](diagrams/02-result-handling.svg)
*Two failure paths, one outcome shape. `isError` is read before the content, and the content has six branches, not one.*

### The flag is a plain `bool`

Two facts worth pinning, both measured against `mcp==2.0.0b2`:

```
>>> CallToolResult.model_fields["is_error"]
FieldInfo(annotation=bool, required=False, default=False, alias='isError', alias_priority=1)
```

`is_error` is a plain `bool` with a default of `False`. It is **not** `bool | None`, so absent
and false mean the same thing and a client must treat them that way. And passing `None`
explicitly is not a way of saying "unknown":

```
>>> CallToolResult(content=[], is_error=None)
ValidationError: 1 validation error for CallToolResult
is_error
  Input should be a valid boolean [type=bool_type, input_value=None, input_type=NoneType]
    For further information visit https://errors.pydantic.dev/2.14/v/bool_type
```

On the wire the field is `isError`; in Python it is `is_error`. Same rule as everywhere else in
this series: camelCase on the wire, snake_case in Python.

### One outcome type, two failure paths

The fix is not "remember to check `is_error` at every call site". The fix is to make it
impossible to forget, by giving the caller a type that has already folded both failures into
one flag. From
[src/mcp_host/results.py](../../code/10-mcp-client/src/mcp_host/results.py):

```python
def read_result(tool: str, result: CallToolResult) -> ToolOutcome:
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
async def call_tool(self, name, arguments=None) -> ToolOutcome:
    try:
        result = await self.client.call_tool(name, arguments or {})
    except Exception as exc:  # noqa: BLE001 - see docstring
        return ToolOutcome.failed(name, f"{type(exc).__name__}: {exc}")
    return read_result(name, result)
```

The broad `except Exception` is deliberate, and it is the one place in the project where one
belongs. A host that dies because a single tool call blew up is worse than a host that tells
the model the call blew up. What matters is that both paths, raised and returned, converge on
`ToolOutcome.ok is False`, so downstream code has one shape to handle rather than two.

The test that keeps this honest drops one level below the wrapper and looks at what the SDK
actually handed back:

```python
async def test_the_raw_result_would_have_looked_successful():
    async with Client(system_info_server) as client:
        raw = await client.call_tool("find_process", {"limit": "not-a-number"})

        assert raw.is_error is True
        assert raw.result_type == "complete"  # a *successful* response
        assert read_result("find_process", raw).ok is False
```

Read the two middle assertions together. `is_error` is `True` **and** `result_type` is
`"complete"` at the same time, on the same object, from a call that did not raise. That is
the whole defect in two lines, and it is why the checking cannot live in an `except` clause.

### Five content block types, and a sixth branch

`content` is a list of blocks, and the union has five members, the same five
[Post 06](../06-tools-in-depth/index.md) writes from the server's end. Handling one of them is
the second defect from section 1.

| `type` | Required fields | What a client should do with it |
|---|---|---|
| `text` | `text` | Use it. This is the common case. |
| `image` | `data` (base64), `mimeType` | Display it, or describe it. Never paste the base64 into the model's context. |
| `audio` | `data` (base64), `mimeType` | Same. |
| `resource_link` | `uri`, `name` | A pointer, not the content. Decide whether to follow it. |
| `resource` | a nested `resource` with `uri`, plus `text` or `blob` | Inline the text form; summarize the blob form. |

`describe_block` in `results.py` flattens each of them into a `kind`, a rendering safe to put
in front of a model, and a `data` dictionary keeping what a host might act on. Two of its
choices are worth arguing for.

**Binary payloads are described, never inlined.** A screenshot is a megabyte of base64. Pasting
it into a transcript costs a few hundred thousand tokens and tells the model nothing, so the
rendering is `image (image/png, 4096 bytes of base64, not inlined)` while `block.data["data"]`
still holds the payload for a host that wants to draw it.

**An unknown block type is a placeholder, not a crash.** The content union is versioned, and a
newer server will eventually send something this client has never heard of. The final branch
of the `isinstance` chain catches it:

```python
kind = getattr(block, "type", None) or type(block).__name__
return Block("unknown", f"unsupported content block: {kind}", {"type": kind})
```

A client that raises on an unrecognized `type` fails an entire tool call because one block in
it was from the future.

### `structuredContent` is a separate field

If a tool declares an `outputSchema`, its result carries `structuredContent` as well as
`content`, and that is the machine-readable form of the same answer. On the wire it is
`structuredContent`; in Python it is `structured_content`. It can be any JSON value since
SEP-2106 (Specification Enhancement Proposal 2106) loosened it, so do not assume a dictionary.
The pinned SDK types it as `Any`, which is the honest annotation.

The convention is that a server also serializes it into a text block for backward
compatibility, which means a client that reads only `content` usually gets *something*, and
that "usually" is what makes the bug hard to notice. `ToolOutcome` keeps both and falls back
to the structured form when there are no text blocks at all.

Finally, one concurrency fact, because it changes how a host is written: **concurrent
`call_tool` calls on a single client are safe.** Request-identifier correlation is the client's
job and it does it properly, so `asyncio.gather` over several calls on one connection needs no
lock. [Post 11](../11-building-a-host/index.md) leans on that hard.

## 7. Handling `input_required` from the client side

[Post 08](../08-elicitation-and-mrtr/index.md) built Multi Round-Trip Requests (MRTR) from the
server's side. Here is the same mechanism seen from the client, which is where it stops being
abstract, because *you* are now the one who has to answer.

Under revision 2026-07-28 a server has no back channel. It cannot call into the client and
block. So a tool that needs a human answer does not ask, it **returns**, and the client calls
the same tool again with the answer attached. This is the first leg, captured from the real
server, printed in full because the shape is the lesson:

```json
{
  "resultType": "input_required",
  "inputRequests": {
    "system_info.interactive:_confirm_terminate": {
      "method": "elicitation/create",
      "params": {
        "mode": "form",
        "message": "Terminate process 999999 (unknown)? This cannot be undone.",
        "requestedSchema": {
          "properties": {
            "confirm": { "description": "Confirm that this process should be terminated.",
                         "title": "Confirm", "type": "boolean" },
            "reason":  { "default": "", "description": "Optional note recorded in the server log.",
                         "title": "Reason", "type": "string" }
          },
          "required": ["confirm"],
          "type": "object"
        }
      }
    }
  },
  "requestState": "v1.HgM_oyyaNFt9XjGr1JAMbPbYxz3FDvRTO..."
}
```

Three things to read off it. `inputRequests` is a **map**, keyed by identifiers the server
chose, so a server may ask several questions in one round and the client must answer them by
key. The values are bare request objects with a `method` and `params` and no JSON-RPC envelope,
because they are not requests on the wire. And `requestState` is an opaque sealed blob, 399
characters in this capture and a different length in the next one, which the client **must**
echo back byte for byte and **must not** inspect, parse, or modify.

The reply is the same tool, the same arguments, a **new** JSON-RPC identifier, and two extra
parameters:

```json
{
  "name": "terminate_process",
  "arguments": { "pid": 999999 },
  "inputResponses": {
    "system_info.interactive:_confirm_terminate": { "action": "accept", "content": { "confirm": false } }
  },
  "requestState": "v1.HgM_oyyaNFt9XjGr1JAMbPbYxz3FDvRTO..."
}
```

and the second leg completes:

```json
{
  "content": [{ "type": "text", "text": "Not terminated. The user answered no for process 999999." }],
  "structuredContent": { "result": "Not terminated. The user answered no for process 999999." },
  "isError": false,
  "resultType": "complete"
}
```

Note that "the user said no" came back as a **successful** result with `isError` false. A
refusal is an outcome, not an error, and a server that reported it as `isError: true` would be
telling the model to retry.

There are two ways to be the client in that exchange, and one thing that is not optional in
either of them.

![Three lanes under a band stating that passing an elicitation callback is what puts the elicitation capability into the clientCapabilities carried on every request, and that the manual path never invokes the callback and still needs one. The first lane connects without a callback and dead-ends: the server refuses with -32021, MISSING_REQUIRED_CLIENT_CAPABILITY, before it ever asks, so there is no result and nothing to retry. The second lane hands the SDK a callback and lets it drive: the server returns resultType input_required, the SDK calls the callback once per inputRequests entry, and it retries with the same name, the same arguments, a new id, and the requestState echoed verbatim. The third lane drives the same loop by hand through allow_input_required, answering each key the server chose. The second and third lanes converge on one band saying both hand back the same CallToolResult. A band across the bottom explains the round cap and the question digest.](diagrams/03-two-ways-to-answer.svg)
*The two paths differ only in who writes the loop. Neither runs at all without a callback on the `Client`.*

### The automatic path

In practice you do not write that loop. Hand `Client` an `elicitation_callback` and the SDK
drives the whole thing: it dispatches each entry of `inputRequests`, retries with the answers
and the echoed `request_state`, and hands you a plain `CallToolResult`. The loop is bounded by
`input_required_max_rounds`, which defaults to 10, and exceeding it raises
`InputRequiredRoundsExceededError`.

`interactive.py` supplies the callback. The interesting part is not the adapter, it is the
console responder underneath it:

```python
async def _ask(prompt: str) -> str:
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return ""
```

**Never call bare `input()` inside a coroutine.** It blocks the event loop for as long as the
person takes to read the question. Nothing else progresses, no other server's call completes,
and any read timeout you configured fires while the loop sits on standard input. It is the
single easiest way to make an async host feel broken, and `asyncio.to_thread` costs one line.
The `EOFError` branch matters too: piped input that runs out, or a closed terminal, should
read as "no", not as a crash.

### The manual path

You still want to know how to drive it by hand, because a host that batches several servers'
questions into one dialog has to. `call_with_input` in `interactive.py` does it through the
session, which returns the `InputRequiredResult` instead of resolving it:

```python
result = await session.call_tool(name, arguments, allow_input_required=True)

for _ in range(max_rounds):
    if not isinstance(result, InputRequiredResult):
        return result

    responses = {}
    for key, request in (result.input_requests or {}).items():
        responses[key] = await _fulfil(request, responder)

    result = await session.call_tool(
        name, arguments,
        allow_input_required=True,
        input_responses=responses,
        request_state=result.request_state,
    )
```

Three rules hold that together, and breaking any of them makes the call spin until it hits
the cap.

1. **Same tool name and the same arguments on every leg.** The arguments are not "already
   sent". Each leg is a complete, independent request that may land on a different server
   instance.
2. **Echo `request_state` verbatim.** It is sealed by the server and is how the server
   recognizes the continuation.
3. **Key the responses by the server's keys.** They are arbitrary strings the server chose;
   in the capture above the key happens to be `module:qualname`, and you must not depend on
   that.

There is also a rule about the *server's* side that will bite you as a client author. The SDK
matches a recorded answer against a digest of the exact question text. If a server rewords its
question between rounds, by including a timestamp or a live reading, the answer never matches,
the server asks again, and the call runs to the round cap. From the outside it looks as though
the user's answer was ignored. The project pins the good behavior by counting:

```python
async def test_the_question_is_asked_once_per_call():
    responder = ScriptedResponder("decline")
    async with open_connection(spec(), elicitation_callback=declining()) as conn:
        await call_with_input(conn, "terminate_process", {"pid": 4242}, responder)

    assert len(responder.seen) == 1
```

### Passing a callback is what declares the capability

This is the trap, and it is worth its own paragraph because the manual path never invokes the
callback, which makes it look optional.

**It is not optional.** A `Client` with no `elicitation_callback` declares no elicitation
capability in its per-request `_meta`, and a server whose tool needs an answer refuses the
call before it ever asks, with `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`). Here is the
real one, raised by the system-information server against a client that connected without a
callback:

```
MCPError(-32021,
  "Client did not declare the form elicitation capability required by resolver
   'system_info.interactive:_confirm_terminate'",
  {'requiredCapabilities': {'elicitation': {'form': {}}}})
```

`data.requiredCapabilities` is **required** on this code, not optional, and it names exactly
what to add. Note the resolver in the message: it is the same key that would have appeared in
`inputRequests` had the call got that far.

The manual path still needs a callback on the `Client`, purely as the declaration. The failing
case has a test of its own, because otherwise the bug report you get is "your tool is broken":

```python
async def test_without_an_elicitation_callback_the_call_fails_cleanly():
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("terminate_process", {"pid": 999999})

        assert outcome.ok is False
        assert "elicitation" in outcome.for_model().lower()
```

Two of the three request types a server may send are already deprecated in this revision.
`sampling/createMessage` and `roots/list` still appear in the union; form elicitation is the
only one a host is really obliged to support. The project answers `roots/list` with an empty
list, which is an honest answer for a host that exposes no filesystem roots, and refuses
sampling outright.

## 8. Resources and prompts

Tools get the attention, and the other two primitives, which
[Post 07](../07-resources-and-prompts/index.md) covers from the server's side, take about ten
lines between them:

```python
read = await conn.read_resource("system://processes/top")
print(read.contents[0].text)

prompt = await conn.get_prompt("diagnose_performance", {"symptom": "slow"})
```

Two things a client author should know. First, `resources/read` and `prompts/get` participate
in MRTR exactly as `tools/call` does, because all three extend the same request parameters, so
either of them can answer with `input_required` and expect a retry. Those three methods, and
only those three, take `inputResponses` and `requestState`.

Second, resource-not-found is `-32602` in this revision, not `-32002`. The old code was retired
and a server implementing this revision **must not** emit it, though a client **should** still
accept it from an older server. Asking the system-information server for a URI it does not have
comes back as `MCPError(-32602, 'Unknown resource: system://does/not/exist', {'uri': ...})`,
and it raises rather than returning, because it is a protocol error and not a tool failure.

## 9. Running it

```bash
cd code/10-mcp-client
uv sync --extra dev
uv run python -m mcp_host list
```

The full suite, on Windows, where `PYTHONPATH` separates with a semicolon:

```bash
cd code/10-mcp-client && PYTHONPATH="src;../05-first-server/src" pytest tests -q
```
```
....................................................................     [100%]
68 passed in 6.44s
```

The `isError` path, end to end through the CLI, calling `find_process` with a string where an
integer belongs. This is a real run:

```
$ uv run python -m mcp_host call find_process --args '{"limit": "not-a-number"}'
[info] no servers.json; using servers.example.json
[failed] TOOL FAILED (find_process): Error executing tool find_process: 2 validation errors for find_processArguments
name
  Field required [type=missing, input_value={'limit': 'not-a-number'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.14/v/missing
limit
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='not-a-number', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/int_parsing
```

The prefix is the whole point. `TOOL FAILED (find_process):` is added by `ToolOutcome.for_model`
and it is what the model sees. A model recovers from a tool error it can read. It cannot recover
from one that arrives disguised as an answer.

One deliberate absence in that transcript, and everywhere else this project prints: **the
output is plain American Standard Code for Information Interchange (ASCII).** A checkmark or an
arrow written to a Windows console under cp1252 raises `UnicodeEncodeError` from inside
`print`, which points the traceback at your output code rather than at the glyph you pasted in.
`[ok]` and `->` render everywhere.

## 10. When not to write your own client

**When you only want to test a server.** `Client(server_object)` in a test file is three lines
and gives you the real protocol path. You do not need a wrapper for that, and
[Post 12](../12-testing-and-debugging/index.md) shows the pattern properly.

**When an existing host will do.** If your users live in an editor or a desktop application
that already speaks MCP, shipping a server and a configuration snippet is a smaller product
than shipping a client.

**When you have not decided what the host is for.** A client is transport plumbing. The
interesting decisions, which model, which tools to expose, what to prompt about, all live in
the host, and building the plumbing first can disguise the fact that nobody has made them.

**When conformance matters more than control.** A server has one job; a client has to be right
about all of them. If you are shipping a client to other people, the conformance suite is not
optional, and it will find dialect you invented without noticing.

---

## Common pitfalls

- **Wrapping `call_tool` in `try`/`except` and calling it error handling.** The `except` clause
  sees transport and protocol faults only. A tool that ran and failed returns a normal result
  with `isError: true`, `resultType: "complete"`, and nothing raised. Check the flag explicitly,
  first, and fold both failure paths into one outcome type so no call site can skip it.
- **Reading `.text` off every content block.** The union has five members and four of them have
  no `text` attribute. Images, audio, resource links, and embedded resources vanish silently,
  and so does `structuredContent`, which is a separate field entirely.
- **Forgetting that the client owns a task group.** It shows up twice. A hand-rolled
  `__aenter__` and `__aexit__` stored on `self` gets a cancel-scope error and leaks the
  transport whenever the session exit raises first, so use `AsyncExitStack` or `async with`.
  And the group re-raises escaping exceptions wrapped in an `ExceptionGroup`, sometimes nested
  twice, so `except ConnectionError` never fires and neither does
  `pytest.raises(ConnectionError)`. Flatten the group and check the members.
- **Calling bare `input()` inside a coroutine.** It stalls the event loop for the whole time the
  user is reading. Every other in-flight call stops, and configured read timeouts fire against
  a loop that is not running. `asyncio.to_thread(input, prompt)` is the whole fix.
- **Assuming `elicitation_callback` is optional because the manual path never calls it.**
  Passing one is what declares the capability. Without it, a server whose tool needs an answer
  fails with `-32021` and a `requiredCapabilities` payload, before it ever asks.
- **Expecting `Client("stdio://...")` to work.** A plain string is always an HTTP URL. stdio
  goes through `stdio_client(StdioServerParameters(...))`, and the trailing slash on an HTTP
  path gets you a 307 rather than the 404 you were debugging.
- **Sending capabilities once.** There is no handshake and no session. The protocol revision and
  the client's capabilities travel in `_meta` on every single request, and a server must not
  infer them from an earlier one.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `isError` rule, the five content block
  types, and the protocol-error against execution-error split.
  <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Multi Round-Trip Requests"*, revision 2026-07-28, and SEP-2322 (Final,
  2026). The `inputRequests` map, the `requestState` rules, and the requirement that the retry
  carry a different JSON-RPC identifier.
- Specification, *"Base protocol"* § Error codes, revision 2026-07-28. `-32021`
  (`MISSING_REQUIRED_CLIENT_CAPABILITY`) with its required `requiredCapabilities` payload, and
  the move of resource-not-found from `-32002` to `-32602`.
- Specification, *"Transports"*, revision 2026-07-28, and SEP-2243. The `Mcp-Method` and
  `Mcp-Name` routing headers shown in section 3.
- MCP Python SDK, `mcp==2.0.0b2`. Every transcript here came from this version driving
  [code/10-mcp-client/](../../code/10-mcp-client/) against
  [code/05-first-server/](../../code/05-first-server/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 11 — Building a host: the tool loop, many servers, and permissions](../11-building-a-host/index.md)**:
  the same project, four modules further on. Several clients behind one catalog, a model
  driving them, and the permission gate that decides which calls are allowed to happen.
- **[Post 09 — Tasks: work that outlives a single request](../09-tasks/index.md)**: the other
  result shape a client has to be ready for, and why the server, not the client, decides when
  one appears.
- **[Post 12 — Testing and debugging MCP](../12-testing-and-debugging/index.md)**: the
  in-memory pattern this project's 68 tests are built on, and the two async failures that cost
  the most time to diagnose.
