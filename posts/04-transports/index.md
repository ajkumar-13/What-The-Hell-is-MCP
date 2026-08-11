# 04 · Transports: stdio and Streamable HTTP

> **TL;DR.** The transport is the only layer of the Model Context Protocol (MCP) that knows
> about processes and sockets, and there are two of them: stdio, where the host spawns your
> program as a child process and talks to it through pipes, and Streamable HTTP, where your
> program listens and answers ordinary POST requests on a single endpoint. The messages are
> identical on both, so you choose by deployment shape rather than by capability. This post
> reads both at the wire level, header by header, including the one rule that breaks more
> first servers than everything else combined. It ends on what revision 2026-07-28 deleted,
> and how to recognize a server that has not caught up.
>
> **After reading this you will be able to:**
> - Choose a transport from the shape of your deployment rather than from habit.
> - Compose a conformant Streamable HTTP request by hand, with every required header.
> - Diagnose the stdout corruption that breaks most first stdio servers.
> - Probe an unfamiliar server for its era instead of guessing which revision it speaks.

![Two panels of equal size. On the left, a host spawns a server as a child process and three pipes run between them for standard input, standard output, and standard error. On the right, the same host sends an ordinary POST with protocol headers to one HTTP endpoint, and the server answers with either one JSON object or an event stream.](diagrams/01-two-transports.svg)
*Same messages, same metadata, two entirely different operational pictures.*

> **On reading order.** Section 1 opens by assuming you have written a server. If you have
> not, that is fine, and this post will keep: go to
> [Post 05](../05-first-server/index.md), write one, and come back when you want to know how
> the bytes actually move. Part II does not assume you read this post first.

---

## 1. The same message, two ways to move it

You have written a server. On your laptop, Claude Desktop starts it by running a command,
which means your code is a child process with three pipes attached and no network anywhere.
A colleague wants that same server available to a team from a browser-based host on another
continent, which means a listening socket, a certificate, and a load balancer. Same file,
same tools, same JSON.

That gap is what a transport is for. [Post 03](../03-wire-protocol/index.md) covered the
messages themselves: the JavaScript Object Notation remote procedure call format
(JSON-RPC 2.0) envelope, `server/discover`, the `_meta` contract, `resultType`, and the
split between protocol errors and tool execution errors. It said almost nothing about how
the bytes get from one process to another. This post is only that.

MCP defines exactly two standard transports.

**stdio**, short for standard input and output, is the one a desktop host uses. The client
spawns your program, writes JSON-RPC requests to its standard input, and reads JSON-RPC
responses from its standard output. There is no network layer at all.

**Streamable HTTP** is the one a shared deployment uses. Your program exposes a single
Hypertext Transfer Protocol (HTTP) endpoint that accepts POST. Each JSON-RPC message is one
POST, and the reply is either a single JSON object or a Server-Sent Events (SSE) stream
scoped to that one request.

Two properties are worth fixing in your head before the details.

**Neither transport adds protocol semantics.** Specification Enhancement Proposal
(SEP) 2575, the proposal that made MCP stateless, insists the statelessness rules apply
identically on stdio and on HTTP so the two do not diverge. A tool that works on one works
on the other, unchanged. The only difference is where request metadata lives, and even
there the body is the source of truth on both.

**Neither transport is the advanced one.** stdio is not a toy and HTTP is not the production
upgrade. A server that reads your local filesystem has no business listening on a socket,
and a server that ten people share has no business being spawned ten times. Section 9 turns
that into a table.

The specification does leave a door open: the stdio framing works unchanged over Unix domain
sockets, Transmission Control Protocol connections, or any similar channel, and a custom
transport should reuse it rather than invent something new. Hosts are not obliged to support
your custom transport, so treat that as a note rather than a plan.

## 2. stdio: the host spawns your process

A parent process starts a child and keeps hold of the three streams every process is born
with: standard input, standard output, and standard error. MCP's stdio transport is exactly
that, with a framing rule bolted on. From the specification's stdio page, in full:

- The server reads JSON-RPC messages from `stdin` and writes JSON-RPC messages to `stdout`.
- Each message is a single JSON-RPC request, notification, or response.
- Messages are delimited by newlines, and **must not** contain embedded newlines.
- The server **may** write UTF-8 strings to `stderr` for any logging purposes.
- The client **may** capture, forward, or ignore `stderr`, and **should not** assume that
  output on `stderr` indicates an error.
- The server **must not** write anything to `stdout` that is not a valid MCP message.
- The client **must not** write anything to the server's `stdin` that is not a valid MCP
  message.
- The client **must not** write JSON-RPC responses.
- The server **must not** write JSON-RPC requests to `stdout`.

The last two restate [Post 03](../03-wire-protocol/index.md)'s message-direction rules at the transport layer. Requests
travel one way in this revision, so a server writes exactly three kinds of thing: responses
correlated by `id`; notifications relating to a request currently in flight, such as
`notifications/progress` and `notifications/message`; and notifications belonging to an
active `subscriptions/listen` request, which a client correlates using
`_meta["io.modelcontextprotocol/subscriptionId"]`.

There is no header layer here. The specification puts it in one sentence: all request
metadata for the stdio transport is carried inline in the JSON-RPC message body. Everything
section 5 says about `MCP-Protocol-Version` and `Mcp-Method` simply does not apply.

**What it looks like on the pipe.** A newline-delimited exchange, driven with nothing but
`subprocess` and `json` against [snippets/stdio_hello.py](snippets/stdio_hello.py). Lines
marked `>>>` went into the child's standard input, lines marked `<<<` came back on its
standard output, both truncated for width:

```
>>> {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {...}}}
<<< {"jsonrpc":"2.0","id":1,"result":{"cacheScope":"private","capabilities":{"prompts":...

>>> {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {...}}}
<<< {"jsonrpc":"2.0","id":2,"result":{"cacheScope":"private","resultType":"complete",...

>>> {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "whoami", ...}}
<<< {"jsonrpc":"2.0","id":3,"result":{"content":[{"text":"Windows 10","type":"text"}],
    "isError":false,"resultType":"complete","structuredContent":{"result":"Windows 10"}}}

exit code 0
```

That is the entire transport. One JSON object per line in, one JSON object per line back.

**Cancellation.** stdio is a single shared bidirectional channel, so there is no per-request
stream to close. To cancel work in flight the client sends a `notifications/cancelled`
notification naming the request's `id`. This is the only place in the core protocol where a
client sends a notification at all.

**Shutdown.** The client should close the child's standard input, wait for it to exit, and
only then escalate: `SIGTERM` then `SIGKILL` on POSIX systems, `TerminateProcess` or a Job
Object on Windows. Your side of that contract is one line: servers should exit promptly when
standard input is closed or reads return end of file. The transcript above ends with
`exit code 0` because closing the pipe was enough.

**Lifetime.** A crashed server costs you the requests that happened to be in flight and
nothing else, because the protocol is stateless; the client retries them against a fresh
process, and re-establishes any `subscriptions/listen` stream. One rule constrains the host
rather than you, and it explains host behavior you would otherwise find arbitrary: clients
should not use an individual task, thread, or conversation as the lifetime boundary for the
stdio process. An open connection, the specification says, is not a conversation and not a
session. Expect your process to outlive many conversations.

Here is the whole server, which is [snippets/stdio_hello.py](snippets/stdio_hello.py) minus
its comments:

```python
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


if __name__ == "__main__":
    mcp.run()
```

`run()` takes the transport as its first positional argument and defaults to `"stdio"`, so a
bare `mcp.run()` is the stdio server. It is synchronous; inside an event loop you already
own, `await mcp.run_stdio_async()` does the same job. Driven by the software development
kit's (SDK) own client as a real subprocess, that file produces:

```
INFO whoami called
protocol_version 2026-07-28
server_info      name='hello' title=None version='2.0.0b2' ...
tools            ['whoami']
whoami           Windows 10
```

The first line is the interesting one. It is the server's log record, which traveled on
standard error, was forwarded by the client, and appeared interleaved with the client's own
output. That is the arrangement section 3 exists to protect.

## 3. The stdout rule

One line in the stdio specification costs more people more hours than every other line in
MCP put together.

> The server **must not** write anything to `stdout` that is not a valid MCP message.

Read literally it is obvious. In practice it collides with the most reflexive act in Python
development, which is reaching for `print()` when you want to know what your code is doing.

![On the left, a server calling print writes a bare word onto the same standard output pipe that carries JSON-RPC frames, and the client parser reports a JSON parse failure. On the right, the same server logs to standard error, so standard output carries only frames and the log line reaches the host log separately.](diagrams/02-stdout-trap.svg)
*Left, one line of prose in a stream that must contain only JSON. Right, the same diagnostic on the pipe that was built for it.*

Here is the bug in its natural habitat:

```python
@mcp.tool()
def whoami() -> str:
    """Report the operating system this server is running on."""
    print("whoami called")          # the bug
    return f"{platform.system()} {platform.release()}"
```

Running that file under the SDK's stdio client produced this, verbatim, before the tool
result came back:

```
Failed to parse JSONRPC message from server
Traceback (most recent call last):
  File "...\mcp\client\stdio.py", line 221, in _parse_line
    message = types.jsonrpc_message_adapter.validate_json(line, by_name=False)
pydantic_core._pydantic_core.ValidationError: 1 validation error for
  union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
  Invalid JSON: expected value at line 1 column 1
  [type=json_invalid, input_value='whoami called\r', input_type=str]
```

Nothing there mentions `print`, `stdout`, or your tool. It says the client read a line that
was not JSON, which is true and unhelpful. In a host with a graphical interface you will
usually not see even this much: the server appears to connect, then fails on the first call
or disconnects, and the detail is in a log file you have to go and find.

**Three details make this harder to catch than it should be.**

*It is intermittent, because of buffering.* When standard output is a pipe rather than a
terminal, Python buffers it in blocks. A single short `print()` can sit in that buffer until
the process exits and corrupt nothing. The identical file with `print(..., flush=True)` fails
immediately. Those were two runs of the same server on this machine, differing only in the
flush. A bug that appears only when your output happens to cross a buffer boundary survives
testing and shows up in front of a user.

*Recovery is a client's choice, not a guarantee.* The SDK client above logged the parse
failure, skipped the line, and went on to deliver the correct tool result. That is generous
behavior and you must not build on it. The requirement is on the server, and a client is
entirely within its rights to treat an unparseable frame as a broken connection.

*It is often not your `print()`.* Anything that writes to file descriptor 1 does the same
damage: a dependency printing a startup banner or a deprecation notice, a progress bar, a
`breakpoint()` you left in, since the debugger prompt goes to standard output. Most commonly
it is a `subprocess.run(...)` with no `stdout=` argument, because a child process inherits
your standard output and writes straight into the frame stream.

**The fix is one line, and it belongs at the top of the file.**

```python
import logging
import sys

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("my-server")
```

Python's `logging` already defaults to standard error, but stating it makes the intent
explicit and survives a dependency that installs a standard output handler behind your back.
From then on, `log.info(...)` instead of `print(...)`, and
`subprocess.run(..., stdout=subprocess.DEVNULL)` for anything you shell out to.

Standard error is not a consolation prize. The specification explicitly permits any UTF-8 on
it, for any logging purpose, and hosts collect it into per-server log files. Two facts about
it are worth carrying: a client may capture, forward, or ignore that stream, so do not treat
it as a delivery mechanism, and a client should not read output there as a sign of failure,
so you are free to log at `info`.

On Streamable HTTP, standard output carries nothing and a stray `print()` is merely untidy.
Keep the habit anyway. The same file should run both ways, which is the point of section 9,
and the version of you that switches transports in six months will not remember which
functions were safe. A cheap check before you ship: run the server, feed it one request, and
confirm every line it wrote to standard output parses as JSON.

## 4. Streamable HTTP: one endpoint, ordinary POSTs

Nothing spawns an HTTP server. It listens, and any number of clients talk to it. The
specification's summary of the transport is five bullets, and each one deletes a piece of
machinery that earlier revisions had:

- The server exposes a single HTTP endpoint, the **MCP endpoint**, that accepts POST.
- The client sends every JSON-RPC request or notification as its own HTTP POST.
- The server answers each request with either a single JSON object or an SSE stream scoped
  to that request, carrying request-related notifications followed by the final response.
- Server-to-client interactions are embedded in results as input requests, per Multi
  Round-Trip Requests (MRTR).
- Long-lived change notifications are delivered on the response stream of a
  `subscriptions/listen` request.

One path. Not a path per method, and not a second path for streaming.

**Sending, exactly.** Six rules govern the client side.

1. The client **must** use HTTP POST to send JSON-RPC messages.
2. The client **must** include an `Accept` header listing both `application/json` and
   `text/event-stream`.
3. The client **must** include the request metadata headers on every POST. Section 5.
4. The body **must** be a single JSON-RPC request or notification. The client **must not**
   send JSON-RPC responses, and there is no batching.
5. A notification the server accepts earns `202 Accepted` with no body. Otherwise the server
   returns an HTTP error status whose body **may** be a JSON-RPC error with no `id`.
6. For a request, the server **must** answer with either `Content-Type: application/json` or
   `Content-Type: text/event-stream`, and the client **must** support both.

Rule 5 has almost nothing to apply to. The specification notes that this revision of the core
protocol defines **no client-to-server notifications over Streamable HTTP**. The only
client-sent notification in the core protocol is `notifications/cancelled`, and that is a
stdio mechanism. On HTTP, closing the response stream *is* the cancellation signal: the
server **must** treat the disconnect as cancellation of that request, **should** stop work
promptly, and **must not** send anything further for it. Because each request has its own
stream, the disconnect is unambiguous.

**Receiving, exactly.** When the server answers with `text/event-stream`:

- It **may** send JSON-RPC notifications before the final response, typically
  `notifications/progress` or `notifications/message`. They **must** relate to the request
  that opened the stream.
- It **must not** send independent JSON-RPC requests on that stream. The specification flags
  this as a change from revisions 2025-03-26 through 2025-11-25, where servers could.
- The final response **should** terminate the stream.
- The server **should** send `X-Accel-Buffering: no` when it opens the stream, so a reverse
  proxy does not sit on the events.
- On a long-lived stream, notably `subscriptions/listen`, servers are encouraged to emit an
  SSE comment line periodically as a keep-alive, meaning a line beginning with a colon.
  Clients must ignore those and must not treat them as malformed.
- Resumable streams via `Last-Event-ID` **are not supported**. Section 7.

Here is a real one: a `tools/call` for the `watch_cpu` tool from
[code/05-first-server/](../../code/05-first-server/), sent with a `progressToken` in `_meta`.
The response headers first, then the body with one progress event and the long text block
trimmed:

```
HTTP 200
  content-type: text/event-stream
  cache-control: no-cache, no-transform
  x-accel-buffering: no
  transfer-encoding: chunked
```

```
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"cpu-1",
       "progress":1,"total":3,"message":"sampled 1 of 3 seconds"}}

event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"cpu-1",
       "progress":2,"total":3,"message":"sampled 2 of 3 seconds"}}

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"...","type":"text"}],
       "isError":false,"resultType":"complete","structuredContent":{"seconds":3,
       "average_percent":19.7,"peak_percent":25.5,"samples":[...]}}}
```

Four things in that capture are the specification made visible. `x-accel-buffering: no` is
the reverse-proxy hint. The notifications carry no `id`, so they are notifications rather
than requests, and they name the `progressToken` from the request that opened the stream. The
final message carries `id: 1` and ends the stream. And no event carries an SSE `id:` field,
because there is nothing to resume to.

The same server answers `tools/list` with one plain JSON object and
`Content-Type: application/json`. A client does not get to choose which, which is why the
`Accept` header lists both.

**The status codes**, complete, from the transports page:

| Situation | Status | Body |
|---|---|---|
| Request handled, single JSON reply | `200 OK` | `application/json`, one JSON-RPC response |
| Request handled, streamed reply | `200 OK` | `text/event-stream`, notifications then the response |
| Notification accepted | `202 Accepted` | none |
| Notification not accepted | HTTP error, e.g. `400` | **may** be a JSON-RPC error with no `id` |
| `Origin` present and invalid | `403 Forbidden` | **may** be a JSON-RPC error with no `id` |
| Header and body disagree, or a required header is missing or malformed | `400 Bad Request` | JSON-RPC `-32020` |
| Protocol revision not implemented | `400 Bad Request` | JSON-RPC `-32022`, with `data.supported` and `data.requested` |
| Required client capability not declared | `400 Bad Request` | JSON-RPC `-32021`, with `data.requiredCapabilities` |
| Required `_meta` field missing | `400 Bad Request` | JSON-RPC `-32602` |
| Method not implemented | `404 Not Found` | JSON-RPC `-32601` |
| `GET` or `DELETE` to the MCP endpoint | `405 Method Not Allowed` | none |

The `404` deserves a note, because a `404` from an HTTP endpoint usually means "wrong URL".
Here it means "right endpoint, unknown method", and the JSON-RPC error body is what tells the
two apart. The specification says so explicitly: the body distinguishes this case from a
`404` returned by an older HTTP+SSE server that does not host a modern MCP endpoint. Two rows
of that table, confirmed against a server built from
[snippets/http_hello.py](snippets/http_hello.py):

```
--- unknown method
HTTP 404  application/json
{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"Method not found",
 "data":"does/notexist"}}

--- unsupported protocol version
HTTP 400  application/json
{"jsonrpc":"2.0","id":6,"error":{"code":-32022,"message":"Unsupported protocol version",
 "data":{"supported":["2026-07-28"],"requested":"1900-01-01"}}}
```

One honest note. The beta SDK build used here rejects a notification body outright with
`-32600` rather than answering `202 Accepted`. Since the core protocol currently defines no
client-to-server notification on this transport there is nothing conformant to send, so the
divergence is invisible in practice, but an extension that defined one would meet it.

That server is [snippets/http_hello.py](snippets/http_hello.py), which is the stdio file from
section 2 with one line changed:

```python
mcp.run("streamable-http", host="127.0.0.1", port=8000, streamable_http_path="/mcp")
```

`host` and `port` belong on `run()`, not on the `MCPServer` constructor. Started that way it
announces itself and then becomes a web service like any other:

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Pointing the no-SDK client from [Post 03](../03-wire-protocol/index.md) at it,
`python raw_discover.py http://127.0.0.1:8000/mcp`, prints:

```
server        hello 2.0.0b2
versions      2026-07-28
capabilities  prompts, resources, tools
cacheable     0 ms, scope private

1 tools
  whoami                   required: nothing
```

Seventy lines of Python and an HTTP client, with no SDK on the client side at all.

## 5. The headers, one by one

stdio carries its metadata in the body. Streamable HTTP mirrors part of it into headers so
an intermediary can route a request without parsing JSON. Every header below is required
unless the text says otherwise, and every one must agree with the body.

**`Accept`.** Must list both `application/json` and `text/event-stream`. The server picks
per request, so a client that accepts only one will eventually be surprised.

**`MCP-Protocol-Version`.** Required on every POST, and for this series always
`2026-07-28`. The value **must** match `io.modelcontextprotocol/protocolVersion` in the
body's `_meta`. If they disagree the server **must** reject the request with
`400 Bad Request` and a `HeaderMismatch` error, code `-32020`. One legacy allowance: a
server that supports clients older than revision 2025-06-18, which did not define this
header, **may** treat a request without it as 2025-03-26. A server that does not support
such clients **must** reject a request that omits it.

**`Mcp-Method` and `Mcp-Name`.** Added by SEP-2243, which reached Final status in 2026.

| Header | Source field | Required for |
|---|---|---|
| `Mcp-Method` | `method` | all requests |
| `Mcp-Name` | `params.name` or `params.uri` | `tools/call`, `resources/read`, `prompts/get` |

`Mcp-Name` takes `params.name` for `tools/call` and `prompts/get`, and `params.uri` for
`resources/read`. Both headers are required for compliance. A complete `tools/call`, from the
specification's own example:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: get_weather

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": { "location": "Seattle, WA" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

Every routing-relevant value appears twice: once in a header a proxy can read cheaply, once
in the body the server executes. That duplication is the design, and it is also its only real
hazard, which is why validation is mandatory.

One ambiguity to flag, since you will meet it if you read the proposal. SEP-2243's own table
says `Mcp-Method` is required for "all requests and notifications". The published
specification page says "all requests", and adds that header requirements for notification
POSTs are not defined by this revision. Take the page as authoritative and treat notification
headers as undefined for now.

**`Mcp-Param-{Name}`, and the `x-mcp-header` annotation.** This is the one most tutorials get
wrong, so state it precisely: **`x-mcp-header` is not an HTTP header.** It is a JSON Schema
annotation placed on a property inside a tool's `inputSchema`, and its *value* supplies the
name portion of a resulting header called `Mcp-Param-{Name}`.

```json
{
  "name": "execute_sql",
  "description": "Execute SQL on Google Cloud Spanner",
  "inputSchema": {
    "type": "object",
    "properties": {
      "region": {
        "type": "string",
        "description": "The region to execute the query in",
        "x-mcp-header": "Region"
      },
      "query": { "type": "string", "description": "The SQL query to execute" }
    },
    "required": ["region", "query"]
  }
}
```

A client calling that tool must lift `region` into a header. The argument still appears in
`arguments` in the body; the header is a mirror, not a move.

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: execute_sql
Mcp-Param-Region: us-west1
```

The constraints on an `x-mcp-header` value are strict, and all are requirements. It **must
not** be empty, **must** be a valid HTTP field-name token per Request for Comments (RFC)
9110 section 5.1, **must not** contain control characters including carriage return or line
feed, and **must** be unique case-insensitively within that schema. It **may** only sit on a
parameter of primitive type, meaning integer, string, or boolean, and **`number` is not
permitted**; integers **must** be within plus or minus 2^53 minus 1. And it **may** only sit
on a property statically reachable from the schema root through a chain of `properties` keys
alone, never through `items`, an array keyword, `oneOf`, `anyOf`, `allOf`, `not`, `if`,
`then`, `else`, or `$ref`. Nested objects are fine as long as every step is a `properties`
key.

Support is asymmetric. It is optional for servers and **mandatory for clients**. A client on
Streamable HTTP **must** reject any tool definition whose `x-mcp-header` value violates those
constraints, and "reject" has a specific meaning: exclude that tool from the result of
`tools/list`, and **should** log a warning naming the tool and the reason. Clients on other
transports, stdio included, **may** ignore `x-mcp-header` entirely.

Presence is exact. If the argument has a value the client **must** send the header and the
server **must** validate it against the body. If the value is `null`, or the parameter is
absent from `arguments`, the client **must** omit the header and the server **must not**
expect it. A client that omits the header while sending the value in the body is
non-conforming, and the server **must** reject the request.

One warning belongs on a wall: server authors **should not** mark passwords, application
programming interface keys, tokens, or personal data with `x-mcp-header`. Header values are
visible to every intermediary on the path.

**Encoding values that are not plain text.** A string goes as-is, an integer as its decimal
form, a boolean as lowercase `true` or `false`. When a value cannot be safely represented in
American Standard Code for Information Interchange (ASCII), because it contains non-ASCII
characters, control characters, or leading or trailing whitespace, the client **must** send
the Base64 encoding of the value's UTF-8 bytes wrapped in a sentinel, and the same applies to
`Mcp-Name`:

```text
Mcp-Param-{Name}: =?base64?{Base64EncodedValue}?=
```

The markers are lowercase and case-sensitive and must appear exactly as shown. Anything that
inspects these values **must** decode before comparing. Because a plain value could itself
look like the sentinel, a client **must** also Base64 encode any ASCII value that happens to
match the pattern.

| Value | Why | Header value |
|---|---|---|
| `us-west1` | plain ASCII | `Mcp-Param-Region: us-west1` |
| `Hello, 世界` | non-ASCII | `Mcp-Param-Greeting: =?base64?SGVsbG8sIOS4lueVjA==?=` |
| `" padded "` | leading and trailing spaces | `Mcp-Param-Text: =?base64?IHBhZGRlZCA=?=` |
| `=?base64?literal?=` | matches the sentinel | `Mcp-Param-Val: =?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=` |

Header names are case-insensitive, as everywhere in HTTP. Header *values*, including method
names, are case-sensitive.

**Validation, and why it is not optional.** A server that reads the body **must** reject any
request whose header values disagree with it. The stated reason is a security one: a load
balancer routing on `Mcp-Name` while the server executes on `params.name` means two
components acting on different sources of truth. Rejection is `400 Bad Request` with
`-32020`, and this came back from a live server:

```
--- Mcp-Name does not match the body
HTTP 400  application/json
{"jsonrpc":"2.0","id":3,"error":{"code":-32020,
 "message":"mcp-name header does not match the request body's 'name' parameter"}}
```

Omitting `Mcp-Method` entirely failed the same way, with the same code. For integers, servers
**should** compare numerically rather than as strings, so `42.0` equals `42`. An intermediary
**must** return an appropriate HTTP error status when it detects a mismatch but is not obliged
to produce a JSON-RPC body, and an intermediary that does not recognize an `Mcp-Param-{Name}`
header **must** forward it untouched.

There is a defined recovery path, and it is more thoughtful than a retry loop. If a server
rejects a request with `HeaderMismatch` because `Mcp-Param-*` headers are missing or wrong,
the client **should** call `tools/list` to see whether the tool's `inputSchema` has changed,
then retry with the right headers. A schema that gained an annotation since the client last
looked is the expected cause.

## 6. Why round-robin load balancing works now

![One client sends three requests to a round-robin load balancer, which forwards one to each of three identical replicas. A panel shows the three headers a balancer can route on, and a second panel shows the session identifier header struck out as removed.](diagrams/03-behind-a-load-balancer.svg)
*Three properties compose into one operational result: any request can be answered by any replica.*

[Post 03](../03-wire-protocol/index.md) argued this from the message shape. Here it is from the transport, where it pays off.
Four things compose.

**There is no `Mcp-Session-Id`.** SEP-2567 removed it. There is no server-minted identifier
for a balancer to pin a client to, because there is no server-side state for it to point at.

**There is no `initialize`.** SEP-2575 removed the handshake. No request depends on the
outcome of an earlier one, because every POST carries the protocol revision and the client's
capabilities in its own `_meta`.

**There is no cross-request state in flight.** MRTR ends each round trip with a real JSON-RPC
result and the client re-sends everything on the retry, so the retry can land anywhere. That
is SEP-2322, and [Post 08](../08-elicitation-and-mrtr/index.md) is the whole story.

**The balancer no longer has to read the body.** That is SEP-2243, and section 5's headers are
the mechanism. Routing on a header is cheap; routing on a JSON body means buffering, parsing,
and re-serializing it at every hop.

SEP-2575 states the problem it was solving in one sentence: under the old model a simple
stateless load balancer, layer 4 or layer 7 round-robin, could not be used. The release
announcement for 2026-07-28 states the result just as plainly: a remote MCP server that
previously needed sticky sessions, a shared session store, and deep packet inspection at the
gateway can now run behind a plain round-robin load balancer.

You can watch the absence. These are the complete response headers from a `tools/list`
against a modern server:

```
date: Sun, 26 Jul 2026 17:02:36 GMT
server: uvicorn
content-length: 402
content-type: application/json
```

No session identifier, because there is no session. Nothing for a balancer to remember, and
nothing for it to get wrong. [Post 21](../21-deploying/index.md) turns that into a deployment.

## 7. What was removed, and what to do when you meet an older server

Revisions 2025-03-26 through 2025-11-25 also called their transport Streamable HTTP, and it
was a different thing. If you are reading a tutorial, an SDK changelog, or a server written
in that window, this table is the translation.

| Removed | Replacement |
|---|---|
| `Mcp-Session-Id`, and HTTP `DELETE` to end a session | Nothing. Explicit state handles passed as ordinary tool arguments |
| A standalone `GET` SSE stream for server-initiated messages | `subscriptions/listen`, a POST whose response *is* the stream |
| Server-initiated JSON-RPC requests on an SSE stream | MRTR, and an `InputRequiredResult` returned from the call |
| `Last-Event-ID` and SSE event ids, for resumability and redelivery | Nothing. Re-issue the request with a new request id |

The specification is unambiguous that none of these are part of this revision, and it tells a
modern server exactly how to behave when an older client tries them. `GET` or `DELETE` to the
MCP endpoint: answer `405 Method Not Allowed`. An `Mcp-Session-Id` header on a request:
**ignore it**, and neither mint nor echo a session identifier. A `Last-Event-ID` header:
**ignore it**, because streams are not resumable. All three, against a live modern server:

```
--- GET to the MCP endpoint
HTTP 405  (empty body)

--- DELETE to the MCP endpoint
HTTP 405  (empty body)

--- Mcp-Session-Id: a91f sent on a tools/list
HTTP 200  application/json
{"jsonrpc":"2.0","id":8,"result":{"cacheScope":"private","resultType":"complete",
 "tools":[{"name":"whoami", ...}],"ttlMs":0}}
```

The header was simply not there as far as the server was concerned, and nothing came back in
its place.

**Losing resumability has a cost, and you should design for it.** A broken response stream
loses the in-flight request, and clients **must** re-issue it as a new request with a **new**
request id. A long tool call over a flaky connection now starts again from the beginning. If
that is unacceptable for your workload, the answer is the tasks extension, which turns long
work into a pollable object with its own lifecycle, and that is [Post 09](../09-tasks/index.md).

**The `GET` stream became a POST.** Everything the standalone stream used to carry, list
changes and resource updates, now arrives on the response stream of a `subscriptions/listen`
request: an ordinary POST whose reply happens to stay open. Request-scoped notifications like
`notifications/progress` never appear there; they flow on the response stream of the request
they belong to. [Post 07](../07-resources-and-prompts/index.md) covers the method.

**HTTP+SSE is deprecated.** The two-endpoint transport from revision 2025-03-26, with a `GET
/sse` endpoint for the stream and a separate POST endpoint for messages, is in the deprecated
registry. You will still meet it, because published servers outlive specifications.

Meeting one is a probing problem, and the specification defines the probes. First the
vocabulary, which matters here: a **modern** implementation carries revision, identity, and
capabilities as per-request metadata, meaning 2026-07-28 and later. A **legacy**
implementation establishes a session with `initialize`, meaning 2025-11-25 and earlier. A
**dual-era** implementation supports both. Era is a property of the server, not of a request,
and clients **should** cache the answer for the lifetime of the server process on stdio, or of
the origin on HTTP, re-probing only if a cached assumption later fails.

**The stdio probe.** Send `server/discover` first. Three outcomes:

1. A `DiscoverResult` comes back. The server is modern.
2. A recognizable modern JSON-RPC error comes back. The server is modern. Retry with a
   revision from the error's `data.supported`, and do **not** fall back.
3. Anything else, including a timeout. Treat the server as legacy and fall back to
   `initialize`.

One warning, and it is the part implementers get wrong: the fallback **must not** be keyed to
one specific error code. A legacy server can fail in any number of ways, including silence.

**The HTTP probe.** Attempt a modern request. On `400 Bad Request`, look at the body. A
recognizable modern JSON-RPC error means the server is modern and you should read the error.
An empty or unrecognizable body means fall back to `initialize`.

The compatibility outcomes, condensed from the versioning page:

| Client | Server | Outcome |
|---|---|---|
| modern | modern | Works |
| modern | legacy | **Fails.** The server may reject, stay silent, or process an ambiguous method under legacy rules |
| dual-era | modern | Works, staying modern |
| dual-era | legacy | Works, falling back to `initialize` and possibly to HTTP+SSE |
| legacy | modern | **Fails.** Legacy clients have no fall-forward mechanism |
| legacy | dual-era | Works, under the negotiated legacy revision |

That last pair is why the specification asks a modern-only server to name the protocol
revisions it supports in whatever error it returns to an `initialize` request, on any
transport. A legacy client cannot recover, and that message may be the only diagnostic a user
ever sees.

**Two observations from the beta SDK, offered as hedges rather than rules.** The 2.0.0b2
server used throughout this post is dual-era: sent a legacy `initialize` naming
`2025-06-18`, it answered normally and named that revision back. A probe against it therefore
reports "modern" from `server/discover` and "legacy" from `initialize`, which is exactly why
era is defined as a property of the server and cached rather than inferred per request.
Separately, when this build received a POST whose `MCP-Protocol-Version` header named a
legacy revision while the body's `_meta` named 2026-07-28, it routed the request into its
legacy path and answered `-32600` with "Missing session ID", rather than the `-32020`
`HeaderMismatch` the specification prescribes. Both are implementation behaviors in a beta,
not protocol facts, and both are worth re-checking against a release build.

## 8. Security that lives at this layer

Most MCP security belongs to later posts. Three items belong here, because they are transport
configuration and nothing else can fix them.

**Bind to `127.0.0.1`.** When running locally, servers **should** bind only to loopback rather
than to `0.0.0.0`. A local server on all interfaces is reachable by anything that can route to
your machine, including everything else on a coffee-shop network, and a typical local server
holds your files, your credentials, and your processes. There is a bonus in this SDK: binding
to `127.0.0.1`, `localhost`, or `::1` auto-arms its DNS rebinding protection. Behind a real
hostname you must pass `transport_security=TransportSecuritySettings(...)` explicitly, or
every request comes back `421 Misdirected Request`.

**Validate `Origin` on every incoming connection.** Servers **must** do this to prevent Domain
Name System (DNS) rebinding attacks, in which a page in the user's browser is persuaded to
resolve a hostname to `127.0.0.1` and then talk to your local server from the user's own
network position. If `Origin` is present and invalid the server **must** answer
`403 Forbidden`; the body **may** be a JSON-RPC error with no `id`. A live server, sent
`Origin: http://evil.example`:

```
HTTP 403
Invalid Origin header
```

**Never put a token in a query string.** Servers **should** implement proper authentication
for all connections, and OAuth 2.1 is [Post 20](../20-authorization/index.md). The transport-level rule is simpler than any of
that: credentials belong in the `Authorization` header, never in a uniform resource locator
(URL). Query strings are written to access logs by nearly every proxy and web server by
default, survive in browser history, and leak through the `Referer` header. A token in a
header is not encrypted either, but it is not routinely written to disk by three
intermediaries who never intended to keep it. The same reasoning is why section 5 tells you
not to mark secrets with `x-mcp-header`.

One line for the other transport. A stdio server is not sandboxed. It runs as the user who
started the host, with that user's environment, filesystem, and credentials, and the only
thing between a malicious server and the machine is the host's permission gate.
[Post 19](../19-security/index.md) is about what that gate does and does not stop.

## 9. Choosing between them

The rule is deployment shape, not preference, and one question settles almost every case:
**who is allowed to run this process?** If the answer is "the person sitting at the machine",
the host should spawn it, and that is stdio. If it is "a service account, once, for
everybody", it listens, and that is Streamable HTTP.

| Your situation | Transport |
|---|---|
| A desktop host on the user's own machine | stdio |
| The server needs local files, local processes, or local credentials | stdio |
| Distributed as an MCPB bundle or run with `uvx` | stdio |
| One deployment serving a team or the public | Streamable HTTP |
| The host runs in a browser | Streamable HTTP |
| You need OAuth 2.1, rate limiting, or an audit trail at the edge | Streamable HTTP |
| You want to scale horizontally, or deploy without restarting hosts | Streamable HTTP |
| You are writing tests | Neither. Connect in memory, [Post 12](../12-testing-and-debugging/index.md) |

Two corrections to the reflex that "stdio is local, HTTP is remote".

**Streamable HTTP on `127.0.0.1` is an excellent local choice**, and it is what you want while
developing. Restarting a listening server does not require restarting the host, and you can
drive it with `curl` or the no-SDK client from [Post 03](../03-wire-protocol/index.md) while
the real host is connected.
That is why the companion server for [Post 05](../05-first-server/index.md) runs both ways
from one command line.

**stdio is not automatically safer.** It has no network exposure, which removes one attack
class, but it hands your process the user's full local privilege, which adds another. Loopback
HTTP with `Origin` validation is a defensible local posture too.

The reason this choice stays cheap is that it is one line. The server in
[code/05-first-server/](../../code/05-first-server/) runs either way from the same file, and
its tests do not care which:

```python
if args.http:
    mcp.run("streamable-http", host=args.host, port=args.port)
else:
    mcp.run()
```

Write your tools against the protocol, keep your logging on standard error, and the transport
stays a deployment decision you can revisit later.

---

## Common pitfalls

- **Calling `print()` anywhere in a stdio server.** It writes into the frame stream, the
  client reports a JSON parse error naming neither your file nor your function, and because
  standard output is block-buffered when it is a pipe, it may not fail on the run where you
  introduced it. Configure `logging` to `sys.stderr` and never look back.
- **Letting a child process inherit your standard output.** `subprocess.run(["git", "log"])`
  with no `stdout=` argument writes `git`'s output directly onto the protocol channel. Pass
  `stdout=subprocess.PIPE` or `stdout=subprocess.DEVNULL` for every subprocess a stdio server
  launches.
- **Letting `Mcp-Method` or `Mcp-Name` drift from the body.** They are not decoration. A
  conformant server compares them to the body and answers `400` with `-32020` when they
  disagree, and omitting one fails the same way. If you generate the headers anywhere other
  than where you build the body, they will eventually diverge.
- **Sending `Accept: application/json` only.** The server chooses the reply shape per request,
  and a tool that reports progress will answer with `text/event-stream`. A client that accepts
  one content type works right up until the first slow tool.
- **Retrying a broken stream with the same request id.** There is no resumability and no
  `Last-Event-ID`. The specification requires a new request id, and reusing an outstanding one
  produces a response the client correlates to the wrong request.
- **Expecting a `GET` stream, or minting a session identifier.** Those earn `405` and silence
  respectively from a modern server. If you are porting code that opened a `GET /mcp` stream,
  what you want is `subscriptions/listen`.
- **Binding a local server to `0.0.0.0` because the example did.** Loopback is the default for
  a reason, and in this SDK it is also what turns on DNS rebinding protection.

---

## Further reading

- Specification, *"Transports: Streamable HTTP"*, revision 2026-07-28. The endpoint rule, the
  send and receive rules, the complete header set, the status-code table, cancellation by
  stream close, and the list of what earlier revisions had.
  <https://modelcontextprotocol.io/specification/draft/basic/transports>
- Specification, *"Transports: stdio"*, revision 2026-07-28. The framing rules quoted in
  section 2, the stdout requirement, shutdown, and the backward-compatibility probe.
- Specification, *"Versioning and compatibility"*, revision 2026-07-28. The modern, legacy,
  and dual-era model, and the compatibility matrix in section 7.
- SEP-2243, *"HTTP standardization"* (Final, 2026). `Mcp-Method`, `Mcp-Name`, the
  `x-mcp-header` annotation, `Mcp-Param-{Name}`, the Base64 sentinel, and the reasoning behind
  mandatory server-side validation.
- SEP-2575, *"Stateless MCP"*, and SEP-2567, *"Sessionless MCP"* (2026). Why the session
  and the handshake were removed, and the load-balancing argument in section 6.
- Specification, *"Deprecated features"*, revision 2026-07-28. Where HTTP+SSE now lives.
  <https://modelcontextprotocol.io/specification/draft/deprecated>
- RFC 9110 section 5.1, *HTTP Semantics*. The field-name token syntax an `x-mcp-header` value
  must satisfy. <https://datatracker.ietf.org/doc/html/rfc9110#section-5.1>

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 05 — Your first MCP server](../05-first-server/index.md)**: the same two `run()`
  calls, attached to a server that does something worth calling, and connected to a real host.
- **[Post 21 — Deploying to production](../21-deploying/index.md)**: containers, health checks,
  and the round-robin deployment section 6 argued for.
- **[Post 10 — Building your own MCP client](../10-mcp-client/index.md)**: the other end of
  both transports, including spawning a subprocess and reading an SSE stream yourself.
