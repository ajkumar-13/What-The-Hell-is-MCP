# 04 - Transports: stdio and Streamable HTTP

> **TL;DR.** The transport is the only layer of the Model Context Protocol (MCP) that knows about processes and sockets, and the specification defines two: stdio, where the host spawns the server as a child process and exchanges one JavaScript Object Notation (JSON) message per line over its pipes, and Streamable HTTP (Hypertext Transfer Protocol), where the server listens on one endpoint and answers each ordinary POST with one JSON object or an event stream scoped to that request. The messages are identical on both, so the choice follows the shape of the deployment, not any capability. On stdio a single stray write to standard output corrupts the frame stream, and although the SDK used here diverts writes made while it serves, a startup banner or text that Python buffered until exit still lands on the pipe; on HTTP every POST mirrors its revision and method, and for three methods a name, into headers that a load balancer routes on and the server must check against the body. Four scripts in `snippets/` reproduce every exchange quoted here that is not the specification's own example, including how a server of revision 2026-07-28 answers a client that still expects sessions.
>
> **Prerequisites:** [Post 03](../03-wire-protocol/index.md).
> **Safe to skip?** Skip it if the reader can already list every header a Streamable HTTP POST needs, say why one `print()` breaks a stdio server, and tell a modern server from a legacy one with a single probe.
>
> **After reading, you will be able to:**
>
> - Choose between stdio and Streamable HTTP from the shape of a deployment rather than from habit.
> - Compose a conformant Streamable HTTP request by hand, with every required header derived from its body.
> - Diagnose stdout corruption in a stdio server, including the stray text that output buffering delays until the server exits.
> - Probe an unfamiliar server for its era instead of guessing which revision it speaks.

![Two panels of equal size. On the left, a host spawns a server as a child process and three pipes run between them for standard input, standard output, and standard error, with two newline-terminated JSON objects beneath. On the right, the same host sends an ordinary POST with protocol and method headers to one HTTP endpoint, and the server answers with either one JSON object or an event stream.](diagrams/01-two-transports.svg)

*Same messages, same metadata, two different operational pictures: pipes owned by the host on the left, one listening endpoint on the right.*

---

## 1. The question: one message, two ways to move it

This post asks how identical messages physically travel over stdio and over Streamable HTTP, and how the shape of a deployment should choose between them. Part II builds the first real server; this post reads both transports at the wire level first, and a reader who has not written a server yet loses nothing, since [post 05](../05-first-server/index.md) does not assume this post and this post does not assume post 05.

Consider one server and two deployments. On a laptop, a desktop host such as Claude Desktop starts it by running a command, which makes the server a child process with three pipes attached and no network anywhere. A colleague wants the same server available to a team from a browser-based host on another continent, which means a listening socket, a certificate, and a load balancer. Same file, same tools, same JSON.

That gap is what a transport is for. [Post 03](../03-wire-protocol/index.md) covered the messages themselves: the JSON-RPC 2.0 envelope (JSON remote procedure call), `server/discover`, the `_meta` contract, `resultType`, and the split between protocol errors and tool execution errors. It said almost nothing about how the bytes get from one process to another, and this post is only that.

MCP defines two standard transports.

**stdio**, short for standard input and output, is the one a desktop host uses. The client spawns the server, writes JSON-RPC requests to its standard input, and reads JSON-RPC responses from its standard output. There is no network layer at all.

**Streamable HTTP** is the one a shared deployment uses. The server exposes a single HTTP endpoint that accepts POST. Each JSON-RPC message is one POST, and the reply is either a single JSON object or a Server-Sent Events (SSE) stream scoped to that one request.

Two properties are worth fixing before the details.

**Neither transport adds protocol semantics.** The Specification Enhancement Proposal (SEP) that made MCP stateless, SEP-2575, requires its statelessness rules to apply identically on stdio and on HTTP so the two do not diverge. A tool that works on one works on the other, unchanged. The difference is where request metadata lives, and even there the body is the source of truth on both.

**Neither transport is the advanced one.** stdio is not a toy and HTTP is not the production upgrade. A server that reads the user's local filesystem has no business listening on a socket, and a server that ten people share has no business being spawned ten times. Section 9 turns that into a table.

The specification leaves one door open: the stdio framing works unchanged over Unix domain sockets, Transmission Control Protocol (TCP) connections, or any similar channel, and a custom transport should reuse it rather than invent something new. A host supports only the transports it implements, so a custom transport is a note here rather than a plan.

## 2. stdio: the host spawns the process

A parent process starts a child and keeps hold of the three streams every process is born with: standard input, standard output, and standard error. MCP's stdio transport is exactly that, with a framing rule on top. The specification's stdio page states the rules:

- The server reads JSON-RPC messages from `stdin` and writes JSON-RPC messages to `stdout`.
- Each message is a single JSON-RPC request, notification, or response.
- Messages are delimited by newlines, and **must not** contain embedded newlines.
- The server **may** write UTF-8 (8-bit Unicode Transformation Format) strings to `stderr` for any logging purposes.
- The client **may** capture, forward, or ignore `stderr`, and **should not** assume that output on `stderr` indicates an error.
- The server **must not** write anything to `stdout` that is not a valid MCP message.
- The client **must not** write anything to the server's `stdin` that is not a valid MCP message.
- The client **must not** write JSON-RPC responses.
- The server **must not** write JSON-RPC requests to `stdout`.

The last two restate [post 03](../03-wire-protocol/index.md)'s message-direction rules at the transport layer. Requests travel one way in this revision, so a server writes exactly three kinds of message: responses correlated by `id`; notifications relating to a request currently in flight, such as `notifications/progress` and `notifications/message`; and notifications belonging to an active `subscriptions/listen` request, which a client correlates using `_meta["io.modelcontextprotocol/subscriptionId"]`.

There is no header layer here. The specification puts it in one sentence: all request metadata for the stdio transport is carried inline in the JSON-RPC message body. Everything section 5 says about `MCP-Protocol-Version` and `Mcp-Method` does not apply.

Here is the whole server, the top of [snippets/stdio_hello.py](snippets/stdio_hello.py):

```python
import argparse
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
```

The bottom of the same file decides what a run does. With `--serve` it is the server a host spawns; with no arguments it runs the demonstration this section quotes, which spawns the file itself with `--serve` and drives it:

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run as a stdio server, as a host spawns it")
    args = parser.parse_args()
    if args.serve:
        mcp.run()
    else:
        demo()
```

`run()` takes the transport as its first positional argument and defaults to `"stdio"`, so a bare `mcp.run()` is the stdio server. It is synchronous; inside an event loop the caller already owns, `await mcp.run_stdio_async()` does the same job.

**What it looks like on the pipe.** The first half of the demonstration uses nothing but `subprocess` and `json`. Lines marked `>>>` went into the child's standard input and lines marked `<<<` came back on its standard output; the script shortens the first two replies to fit and prints the third in full, broken after commas for width:

```text
=== by hand: subprocess and json, one frame per line
>>> {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {...}}}
<<< {"jsonrpc":"2.0","id":1,"result":{"cacheScope":"private","capabilities":{"prompts":{"...
>>> {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {...}}}
<<< {"jsonrpc":"2.0","id":2,"result":{"cacheScope":"private","resultType":"complete","too...
>>> {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "whoami", "arg...
<<< {"jsonrpc":"2.0","id":3,"result":{"content":[{"text":"Windows 10","type":"text"}],
    "isError":false,"resultType":"complete","structuredContent":{"result":"Windows 10"},
    "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"hello","version":""}}}}
exit code 0 after stdin was closed
stderr carried: ['INFO whoami called']
```

That is the entire transport: one JSON object per line in, one JSON object per line back. The `Windows 10` is the machine these transcripts were produced on; elsewhere the string names that system. The log record never touched standard output. It traveled on standard error, which is the arrangement section 3 exists to protect.

**Cancellation.** stdio is a single shared bidirectional channel, so there is no per-request stream to close. To cancel work in flight the client sends a `notifications/cancelled` notification naming the request's `id`. It is the only notification a client sends anywhere in the core protocol, and it is used only on this transport.

**Shutdown.** The client should close the child's standard input, wait for it to exit, and only then escalate: `SIGTERM` then `SIGKILL` on systems that follow the Portable Operating System Interface (POSIX), `TerminateProcess` or a Job Object on Windows. The server's side of that contract is one line: servers should exit promptly when standard input is closed or reads return end of file. The transcript above reports exit code 0 because closing the pipe was enough.

**Lifetime.** A crashed server costs the requests that happened to be in flight and nothing else, because the protocol is stateless: the client can retry them against a fresh process, and must re-establish any `subscriptions/listen` stream. One rule constrains the host rather than the server, and it explains host behavior that would otherwise look arbitrary: clients should not use an individual task, thread, or conversation as the lifetime boundary for the stdio process. An open connection, the specification's base protocol page says, is not a conversation and not a session, so the process can outlive many conversations.

The second half of the demonstration hands the same file to the software development kit's (SDK) own stdio client, which spawns it as a real subprocess. The script routes the child's standard error into a temporary file and prints it last, so the output is the same on every run:

```text
=== with the SDK's stdio client
protocol_version 2026-07-28
server_info      name='hello' version=''
tools            ['whoami']
whoami           Windows 10
server stderr    INFO whoami called
```

The SDK client sent `server/discover` first, found revision 2026-07-28, and never needed a handshake. The server's identity travels in each result's `_meta` under `io.modelcontextprotocol/serverInfo`, as the third frame above shows, and its `version` is empty because this file passes no `version=` to `MCPServer`.

## 3. The stdout rule

One line in the stdio specification is especially easy for a first server to break.

> The server **must not** write anything to `stdout` that is not a valid MCP message.

Read literally it is obvious. In practice it collides with the most reflexive act in Python development, which is reaching for `print()` to see what the code is doing.

![On the left, a server calling print writes a bare word onto the same standard output pipe that carries JSON-RPC frames, and the client parser reports a JSON parse failure at line 1 column 1. On the right, the same server logs to standard error, so standard output carries only frames and the log line reaches the host log on a separate arrow.](diagrams/02-stdout-trap.svg)

*Left, one line of prose in a stream that must contain only JSON. Right, the same diagnostic on the pipe that was built for it.*

[snippets/stdout_trap.py](snippets/stdout_trap.py) is the server of section 2 with two bugs, one in each tool:

```python
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
```

**What the SDK does about it.** The SDK's stdio transport keeps handlers off the wire. While `mcp.run()` serves, file descriptor 1, the process's standard output, points at standard error, and the frames travel through a private duplicate of the pipe that nothing else in the process writes to; when the transport stops, descriptor 1 points at the pipe again. Run with no arguments, the script spawns itself under the SDK's stdio client three times, calls both tools each time, and reports what the client logged and which stray lines reached the server's standard error. The first run leaves standard output as Python leaves it for a pipe:

```text
=== SDK client, server stdout block-buffered (the default for a pipe)
at connect      client logged nothing
whoami          result 'Windows 10'
                client logged nothing
python_version  result '3.12.12'
                client logged nothing
after shutdown  client logged: Failed to parse JSONRPC message from server
                  1 validation error for union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
                  Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='whoami called\r', input_type=str]
server stderr   ['Python 3.12.12']
```

Both calls succeed, and the child interpreter's `Python 3.12.12` went to the server's log, because the child inherited descriptor 1 while it pointed at standard error. The `print()` escaped the diversion. When standard output is a pipe rather than a terminal, Python buffers it in blocks, so `whoami called` waited in that buffer through the whole session, and Python flushed it at exit, after the transport had pointed descriptor 1 back at the pipe. The client read it as one more line after the last response.

Nothing there mentions `print`, `stdout`, or the tool. The client read a line that was not JSON, which is true and unhelpful. The trailing `\r` is a Windows detail: Python's text layer on that system turns each `\n` written to standard output into a carriage return and a line feed, and the client strips only the line feed. A host with a graphical interface may hide even this much, and the detail sits in a log file.

The second run line-buffers the server's standard output, which is what `print(..., flush=True)` does for one call. Every write now happens while descriptor 1 points at standard error, and nothing reaches the pipe:

```text
=== SDK client, server stdout line-buffered
at connect      client logged nothing
whoami          result 'Windows 10'
                client logged nothing
python_version  result '3.12.12'
                client logged nothing
after shutdown  client logged nothing
server stderr   ['whoami called', 'Python 3.12.12']
```

The third run prints and flushes one line before `mcp.run()` starts, as a dependency that announces itself when it is imported would. At that moment descriptor 1 is still the pipe, so the first line the client reads is the banner:

```text
=== SDK client, server stdout block-buffered, a banner flushed before mcp.run()
at connect      client logged: Failed to parse JSONRPC message from server
                  1 validation error for union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
                  Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='somelib 1.0 ready\r', input_type=str]
```

**Three details make this harder to catch than it should be.**

*Where a stray write lands depends on when it happens.* A write made while the transport serves goes to standard error; text written before `mcp.run()`, or held in Python's buffer until exit, reaches the pipe. The identical `print()` is harmless when line-buffered and corrupts the stream at shutdown when block-buffered, so a bug can pass under one configuration and fail under another. The protection also belongs to this SDK alone. Its source calls the diversion best-effort: where descriptors cannot be duplicated, it serves the pipe in place, as the SDK's 1.x line did, and then every stray write lands in the frame stream. A server written with another SDK, or by hand, has whatever protection its author built, and the rule binds it all the same.

*Recovery is a client's choice, not a guarantee.* The SDK client above logged each parse failure, skipped the line, and still delivered every tool result. That is generous behavior and a server must not rely on it. The requirement is on the server, and a client is within its rights to treat an unparseable frame as a broken connection.

*It is not always a `print()` in the server's own code.* Anything that writes to file descriptor 1 at the wrong moment does the same damage: a dependency printing a startup banner or a deprecation notice, a progress bar, a `breakpoint()` left in, since the debugger prompt goes to standard output. Another is a `subprocess.run(...)` with no `stdout=` argument, as in `python_version`: a child process inherits the parent's descriptor 1 and writes wherever it points. Under this SDK that was standard error, as all three runs show; in a server that serves the pipe in place it is the frame stream itself, and the child's output arrives whatever the parent's buffering.

**The fix is two lines, and they belong at the top of the file.** They are the logging lines of section 2:

```python
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("hello")
```

Python's `logging` already defaults to standard error, but naming the stream states the intent where the next reader will see it. From then on, `log.info(...)` instead of `print(...)`, nothing written to standard output before `mcp.run()`, and `subprocess.run(..., stdout=subprocess.DEVNULL)`, or `stdout=subprocess.PIPE` when the output is wanted, for anything the server shells out to. None of that relies on the SDK's diversion, so it holds on every SDK and in every configuration.

Standard error is not a consolation prize. The specification explicitly permits any UTF-8 on it, for any logging purpose, and a desktop host can collect it into a per-server log file, as post 05 shows for one host. Two facts about it are worth carrying: a client may capture, forward, or ignore that stream, so it is not a delivery mechanism, and a client should not read output there as a sign of failure, so logging at `info` is fine.

On Streamable HTTP, standard output carries nothing and a stray `print()` is merely untidy. The habit is still worth keeping, because the same file should run both ways (section 9), and nobody remembers six months later which functions were safe on which transport.

**A cheap check before shipping:** run the server with the exact command the host's configuration names, feed it one request, close its standard input, and parse every line it wrote to standard output until end of file. The last two lines of the demonstration run that check on the broken server, once as a host would start it and once under `python -u`, which turns Python's buffering of standard output off:

```text
cheap check, python stdout_trap.py --serve       2 stdout line(s), not JSON: ['whoami called']
cheap check, python -u stdout_trap.py --serve    1 stdout line(s), not JSON: []
```

Run as a host would start it, the check catches the stray line, and only because it keeps reading after the response: the line arrives once standard input has closed. Under `-u` the same check passes the broken server, because every unbuffered write happened while descriptor 1 pointed at standard error. A check run under a debugging variant of the command tests a different server.

## 4. Streamable HTTP: one endpoint, ordinary POSTs

Nothing spawns an HTTP server. It listens, and any number of clients talk to it. The specification's summary of the transport is five bullets, and each one deletes a piece of machinery that earlier revisions had:

- The server exposes a single HTTP endpoint, the **MCP endpoint**, that accepts POST.
- The client sends every JSON-RPC request or notification as its own HTTP POST.
- The server answers each request with either a single JSON object or an SSE stream scoped to that request, carrying request-related notifications followed by the final response.
- Server-to-client interactions are embedded in results as input requests, per Multi Round-Trip Requests (MRTR).
- Long-lived change notifications are delivered on the response stream of a `subscriptions/listen` request.

One path. Not a path per method, and not a second path for streaming.

**Sending, exactly.** Six rules govern the client side.

1. The client **must** use HTTP POST to send JSON-RPC messages.
2. The client **must** include an `Accept` header listing both `application/json` and `text/event-stream`.
3. The client **must** include the request metadata headers on every POST (section 5).
4. The body **must** be a single JSON-RPC request or notification. The client **must not** send JSON-RPC responses, and there is no batching.
5. A notification the server accepts earns `202 Accepted` with no body. Otherwise the server returns an HTTP error status whose body **may** be a JSON-RPC error with no `id`.
6. For a request, the server **must** answer with either `Content-Type: application/json` or `Content-Type: text/event-stream`, and the client **must** support both.

Rule 5 has almost nothing to apply to. The specification notes that this revision of the core protocol defines **no client-to-server notifications over Streamable HTTP**. The one client-sent notification, `notifications/cancelled`, belongs to stdio. On HTTP, closing the response stream *is* the cancellation signal: the server **must** treat the disconnect as cancellation of that request, **should** stop work promptly, and **must not** send anything further for it. Because each request has its own stream, the disconnect is unambiguous.

**Receiving, exactly.** When the server answers with `text/event-stream`:

- It **may** send JSON-RPC notifications before the final response, typically `notifications/progress` or `notifications/message`. They **must** relate to the request that opened the stream.
- It **must not** send independent JSON-RPC requests on that stream. The specification flags this as a change from revisions 2025-03-26 through 2025-11-25, where servers could.
- The final response **should** terminate the stream.
- The server **should** send `X-Accel-Buffering: no` when it opens the stream, so a reverse proxy does not hold the events back.
- On a long-lived stream, notably `subscriptions/listen`, servers are encouraged to emit an SSE comment line periodically as a keep-alive, meaning a line beginning with a colon. Clients must ignore those lines and must not treat them as malformed.
- Resumable streams via `Last-Event-ID` **are not supported** (section 7).

Here is a real one. [snippets/progress_stream.py](snippets/progress_stream.py) serves one tool, `slow_count`, which reports progress at each of its steps, and sends it a hand-composed `tools/call` with a `progressToken` in `_meta`. The response headers come first (the script leaves out `date` and `server`), then the raw stream, with the long `data:` lines broken after commas for width:

```text
HTTP 200
  content-type: text/event-stream
  cache-control: no-cache, no-transform
  connection: keep-alive
  x-accel-buffering: no
  transfer-encoding: chunked

event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"count-1",
       "progress":1,"total":3,"message":"counted 1 of 3"}}

event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"count-1",
       "progress":2,"total":3,"message":"counted 2 of 3"}}

event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"count-1",
       "progress":3,"total":3,"message":"counted 3 of 3"}}

event: message
data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"3","type":"text"}],
       "isError":false,"resultType":"complete","structuredContent":{"result":3},
       "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"progress","version":""}}}}

4 events; SSE id: fields 0; the last carries id 1 and ends the stream
```

Four things in that capture are the specification made visible. `x-accel-buffering: no` is the reverse-proxy hint. The notifications carry no `id`, so they are notifications rather than requests, and they name the `progressToken` of the request that opened the stream. The final message carries `"id":1` and ends the stream. And no event carries an SSE `id:` field, because there is nothing to resume to.

The same SDK answers `tools/list`, and any call that finishes without sending a notification, with one plain JSON object and `Content-Type: application/json`. It commits to an event stream only when the handler emits a notification, or when the handler has run silently for 15 seconds, at which point it opens the stream so that a `: ping` comment line can keep an idle proxy from closing the connection. A client does not get to choose which, which is why the `Accept` header lists both.

**The status codes**, from the Streamable HTTP page:

| Situation | Status | Body |
|---|---|---|
| Request handled, single JSON reply | `200 OK` | `application/json`, one JSON-RPC response |
| Request handled, streamed reply | `200 OK` | `text/event-stream`, notifications then the response |
| Notification accepted | `202 Accepted` | none |
| Notification not accepted | HTTP error, e.g. `400` | **may** be a JSON-RPC error with no `id` |
| `Origin` present and invalid | `403 Forbidden` | **may** be a JSON-RPC error with no `id` |
| Header and body disagree, or a required header is missing or malformed | `400 Bad Request` | JSON-RPC `-32020` (`HeaderMismatch`) |
| Protocol revision not implemented | `400 Bad Request` | JSON-RPC `-32022` (`UnsupportedProtocolVersion`), with `data.supported` and `data.requested` |
| Required client capability not declared | `400 Bad Request` | JSON-RPC `-32021`, with `data.requiredCapabilities` |
| Required `_meta` field missing | `400 Bad Request` | JSON-RPC `-32602` (`Invalid params`) |
| Method not implemented | `404 Not Found` | JSON-RPC `-32601` (`Method not found`) |
| `GET` or `DELETE` to the MCP endpoint | `405 Method Not Allowed` | none |

The `404` deserves a note, because a `404` from an HTTP endpoint usually means "wrong address". Here it means "right endpoint, unknown method", and the JSON-RPC error body is what tells the two apart. The specification says so explicitly: the body distinguishes this case from a `404` returned by an older HTTP+SSE server that does not host a modern MCP endpoint.

[snippets/http_hello.py](snippets/http_hello.py) is the server of section 2 on this transport, and the same tool. Run with no arguments, it starts itself as a server on a free loopback port, sends it hand-composed POSTs with `httpx` and no MCP client at all, prints what came back, and stops the server. Two rows of the table, from that run:

```text
--- unknown method
HTTP 404  application/json
{"jsonrpc":"2.0","id":4,"error":{"code":-32601,"message":"Method not found",
 "data":"does/notexist"}}

--- unsupported protocol revision
HTTP 400  application/json
{"jsonrpc":"2.0","id":5,"error":{"code":-32022,"message":"Unsupported protocol version",
 "data":{"supported":["2026-07-28"],"requested":"1900-01-01"}}}
```

The serving path is the last block of the file, behind the same `--serve` flag as before:

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true", help="run as a Streamable HTTP server until interrupted")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.serve:
        mcp.run("streamable-http", host="127.0.0.1", port=args.port, streamable_http_path="/mcp")
    else:
        demo()
```

`host` and `port` belong on `run()`, not on the `MCPServer` constructor. Started with `--serve`, the file announces itself on standard error and then behaves like any other web service; these are the last two lines it prints:

```text
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Pointed at `http://127.0.0.1:8000/mcp`, the no-SDK client of [post 03](../03-wire-protocol/index.md), `raw_discover.py`, reads the server's discovery result and tool list with nothing but an HTTP client.

## 5. The headers, one by one

stdio carries its metadata in the body. Streamable HTTP mirrors part of it into headers so an intermediary can route a request without parsing JSON. Every header below is required unless the text says otherwise, and every one must agree with the body.

**`Accept`.** Must list both `application/json` and `text/event-stream`. The server picks per request, as section 4 showed.

**`MCP-Protocol-Version`.** Required on every POST, and for this series always `2026-07-28`. The value **must** match `io.modelcontextprotocol/protocolVersion` in the body's `_meta`. If they disagree the server **must** reject the request with `400 Bad Request` and a `HeaderMismatch` error, code `-32020`. One legacy allowance: a server that supports clients older than revision 2025-06-18, which did not define this header, **may** treat a request without it as 2025-03-26. A server that does not support such clients **must** reject a request that omits it.

**`Mcp-Method` and `Mcp-Name`.** Added by SEP-2243, whose status is Final.

| Header | Source field | Required for |
|---|---|---|
| `Mcp-Method` | `method` | all requests |
| `Mcp-Name` | `params.name` or `params.uri` | `tools/call`, `resources/read`, `prompts/get` |

`Mcp-Name` takes `params.name` for `tools/call` and `prompts/get`, and `params.uri` for `resources/read`. Both headers are required for compliance. A complete `tools/call`, as the specification's Streamable HTTP page prints it:

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

Every routing-relevant value appears twice: once in a header a proxy can read cheaply, once in the body the server executes. That duplication is the design, and it is also its only real hazard, which is why validation is mandatory. The safe way to produce the headers is to derive them from the finished body rather than to write them separately. That is what `headers_for` in `http_hello.py` does, where `NAME_BEARING` maps the three name-bearing methods to the field `Mcp-Name` copies:

```python
def headers_for(body: dict) -> dict[str, str]:
    """Every header a 2026-07-28 POST needs, derived from the body it carries."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"],
        "Mcp-Method": body["method"],
    }
    if body["method"] in NAME_BEARING:
        headers["Mcp-Name"] = encode(body["params"][NAME_BEARING[body["method"]]])
    return headers
```

For the demonstration's own `tools/call` of `whoami`, it prints:

```text
--- the headers this demonstration derives for a tools/call body
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: whoami
```

One ambiguity to flag, for readers of the proposal. SEP-2243's own table says `Mcp-Method` is required for "all requests and notifications". The published Streamable HTTP page says "all requests", and adds that header requirements for notification POSTs are not defined by this revision. The page is authoritative, so notification headers are undefined for now.

**`Mcp-Param-{Name}`, and the `x-mcp-header` annotation.** This one is easy to misread, so it is worth stating precisely: **`x-mcp-header` is not an HTTP header.** It is a JSON Schema annotation placed on a property inside a tool's `inputSchema`, and its *value* supplies the name portion of a resulting header called `Mcp-Param-{Name}`. The specification's example:

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

A client calling that tool must lift `region` into a header. The argument still appears in `arguments` in the body; the header is a mirror, not a move. The header lines of the specification's resulting request:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: execute_sql
Mcp-Param-Region: us-west1
```

The constraints on an `x-mcp-header` value are strict, and all are requirements. It **must not** be empty, **must** be a valid HTTP field-name token per Request for Comments (RFC) 9110 section 5.1, **must not** contain control characters including carriage return or line feed, and **must** be unique case-insensitively within that schema. It **may** only sit on a parameter of primitive type, meaning integer, string, or boolean, and **`number` is not permitted**; integers **must** lie within $\pm(2^{53}-1)$. And it **may** only sit on a property statically reachable from the schema root through a chain of `properties` keys alone, never through `items`, another array keyword, `oneOf`, `anyOf`, `allOf`, `not`, `if`, `then`, `else`, or `$ref`. Nested objects are fine as long as every step is a `properties` key.

Support is asymmetric: optional for servers and **mandatory for clients**. A client on Streamable HTTP **must** reject any tool definition whose `x-mcp-header` value violates those constraints, and "reject" has a specific meaning: exclude that tool from the result of `tools/list`, and **should** log a warning naming the tool and the reason. Clients on other transports, stdio included, **may** ignore `x-mcp-header` entirely.

Presence is exact. If the argument has a value the client **must** send the header and the server **must** validate it against the body. If the value is `null`, or the parameter is absent from `arguments`, the client **must** omit the header and the server **must not** expect it. A client that omits the header while sending the value in the body is non-conforming, and the server **must** reject the request.

One warning belongs on a wall: server authors **should not** mark passwords, application programming interface (API) keys, tokens, or personal data with `x-mcp-header`. Header values are visible to every intermediary on the path.

**Encoding values that are not plain text.** A string goes as-is, an integer as its decimal form, a boolean as lowercase `true` or `false`. When a value cannot be safely represented in American Standard Code for Information Interchange (ASCII), because it contains non-ASCII characters, control characters, or leading or trailing whitespace, the client **must** send the Base64 encoding of the value's UTF-8 bytes wrapped in a sentinel, and the same applies to `Mcp-Name`:

```text
Mcp-Param-{Name}: =?base64?{Base64EncodedValue}?=
```

The markers are lowercase and case-sensitive and must appear exactly as shown. Anything that inspects these values **must** decode before comparing. Because a plain value could itself look like the sentinel, a client **must** also Base64-encode any ASCII value that happens to match the pattern. The specification's table:

| Value | Why | Header value |
|---|---|---|
| `us-west1` | plain ASCII | `Mcp-Param-Region: us-west1` |
| `Hello, 世界` | non-ASCII | `Mcp-Param-Greeting: =?base64?SGVsbG8sIOS4lueVjA==?=` |
| `" padded "` | leading and trailing spaces | `Mcp-Param-Text: =?base64?IHBhZGRlZCA=?=` |
| `"line1\nline2"` | contains a newline | `Mcp-Param-Text: =?base64?bGluZTEKbGluZTI=?=` |
| `=?base64?literal?=` | matches the sentinel | `Mcp-Param-Val: =?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=` |

The `encode` function in `http_hello.py`, a dozen lines of standard library, reproduces all five values:

```text
--- header values through the Base64 sentinel rule
'us-west1'             -> us-west1
'Hello, 世界'            -> =?base64?SGVsbG8sIOS4lueVjA==?=
' padded '             -> =?base64?IHBhZGRlZCA=?=
'line1\nline2'         -> =?base64?bGluZTEKbGluZTI=?=
'=?base64?literal?='   -> =?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?=
```

Header names are case-insensitive, as everywhere in HTTP. Header *values*, including method names, are case-sensitive.

**Validation, and why it is not optional.** A server that reads the body **must** reject any request whose header values disagree with it. The stated reason is a security one: a load balancer routing on `Mcp-Name` while the server executes on `params.name` means two components acting on different sources of truth. Rejection is `400 Bad Request` with `-32020`, and the failure conditions include a required header that is missing as well as one that disagrees (section 11 shows both). For integers, servers **should** compare numerically rather than as strings, so `42.0` equals `42`. An intermediary **must** return an appropriate HTTP error status when it detects a mismatch but is not obliged to produce a JSON-RPC body, and an intermediary that does not recognize an `Mcp-Param-{Name}` header **must** forward it untouched.

There is a defined recovery path, and it is more thoughtful than a retry loop. If a server rejects a request with `HeaderMismatch` because `Mcp-Param-*` headers are missing or wrong, the client **should** call `tools/list` to see whether the tool's `inputSchema` has changed, then retry with the right headers. A schema that gained an annotation since the client last looked is the expected cause.

## 6. Why round-robin load balancing works now

![One client sends three requests to a round-robin load balancer, which forwards one to each of three identical replicas. A panel shows the three headers a balancer can route on, and a second panel shows the session identifier header struck out as removed.](diagrams/03-behind-a-load-balancer.svg)

*Any request can be answered by any replica, because nothing is left for the balancer to pin.*

[Post 03](../03-wire-protocol/index.md) argued this from the message shape. Here it is from the transport, where it pays off. Four things compose.

**There is no `Mcp-Session-Id`.** SEP-2567 removed it. There is no server-minted identifier for a balancer to pin a client to, because there is no server-side state for it to point at.

**There is no `initialize`.** SEP-2575 removed the handshake. No request depends on the outcome of an earlier one, because every POST carries the protocol revision and the client's capabilities in its own `_meta`.

**There is no cross-request state in flight.** MRTR ends each round trip with a real JSON-RPC result and the client re-sends everything on the retry, so the retry can land anywhere. That is SEP-2322, and [post 08](../08-elicitation-and-mrtr/index.md) is the whole story.

**The balancer no longer has to read the body.** That is SEP-2243, and section 5's headers are the mechanism. Routing on a header is cheap; routing on a JSON body means buffering, parsing, and re-serializing it at every hop.

SEP-2575 states the problem it was solving in one sentence: under the old model a simple stateless load balancer, layer 4 or layer 7 round-robin, could not be used. The release-candidate announcement for 2026-07-28 states the result just as plainly: a remote MCP server that previously needed sticky sessions, a shared session store, and deep packet inspection at the gateway can now run behind a plain round-robin load balancer.

The absence is visible on the wire. These are the names of every response header from the demonstration's `tools/list`:

```text
--- tools/list
HTTP 200  application/json
tools                 ['whoami']
response headers      date, server, content-length, content-type
```

No session identifier, because there is no session. Nothing for a balancer to remember, and nothing for it to get wrong. [Post 21](../21-deploying/index.md) turns that into a deployment.

## 7. What was removed, and how to meet an older server

Revisions 2025-03-26 through 2025-11-25 also called their transport Streamable HTTP, and it was a different thing. In a tutorial, an SDK changelog, or a server written in that window, this table is the translation:

| Removed | Replacement |
|---|---|
| `Mcp-Session-Id`, and HTTP `DELETE` to end a session | Nothing. Explicit state handles passed as ordinary tool arguments |
| A standalone `GET` SSE stream for server-initiated messages | `subscriptions/listen`, a POST whose response *is* the stream |
| Server-initiated JSON-RPC requests on an SSE stream | MRTR, and an `InputRequiredResult` returned from the call |
| `Last-Event-ID` and SSE event ids, for resumability and redelivery | Nothing. Re-issue the request with a new request id |

The specification is unambiguous that none of these are part of this revision, and it tells a server that supports only this revision how to answer an older client that tries them. `GET` or `DELETE` to the MCP endpoint: answer `405 Method Not Allowed`. An `Mcp-Session-Id` header on a request: **ignore it**, and neither mint nor echo a session identifier. A `Last-Event-ID` header: **ignore it**, because streams are not resumable. All three, from the `http_hello.py` run:

```text
--- GET to the MCP endpoint
HTTP 405  (no content-type)
(empty body)

--- DELETE to the MCP endpoint
HTTP 405  (no content-type)
(empty body)

--- Mcp-Session-Id: a91f and Last-Event-ID: 7 sent on a tools/list
HTTP 200  application/json
tools                 ['whoami']
mcp-session-id echoed False
```

The two headers were simply not there as far as the server was concerned, and nothing came back in their place. The `GET` and `DELETE` carried `MCP-Protocol-Version: 2026-07-28`; this SDK decides which era's code handles a request from that header alone, a point that matters at the end of this section.

**Losing resumability has a cost, and it is worth designing for.** A broken response stream loses the in-flight request, and clients **must** re-issue it as a new request with a **new** request id. A long tool call over a flaky connection now starts again from the beginning. When that is unacceptable, the answer is the tasks extension, which turns long work into a pollable object with its own lifecycle, and that is [post 09](../09-tasks/index.md).

**The `GET` stream became a POST.** Everything the standalone stream used to carry, list changes and resource updates, now arrives on the response stream of a `subscriptions/listen` request: an ordinary POST whose reply happens to stay open. Request-scoped notifications like `notifications/progress` never appear there; they flow on the response stream of the request they belong to. [Post 07](../07-resources-and-prompts/index.md) covers the method.

**HTTP+SSE is deprecated.** The two-endpoint transport of revision 2024-11-05, with a `GET` endpoint that held the event stream open and a separate POST endpoint for messages, was replaced by Streamable HTTP in revision 2025-03-26 and is listed in the specification's deprecated features registry. It is still met in practice, because published servers outlive specifications.

Meeting one is a probing problem, and the specification defines the probes. First the vocabulary, from the versioning page: a **modern** implementation carries revision, identity, and capabilities as per-request metadata, meaning 2026-07-28 and later. A **legacy** implementation establishes a session with `initialize`, meaning 2025-11-25 and earlier. A **dual-era** implementation supports both. Era is a property of the server, not of a request, and clients **should** cache the answer for the lifetime of the server process on stdio, or of the origin on HTTP, re-probing only if a cached assumption later fails.

**The stdio probe.** Send `server/discover` first. Three outcomes:

1. A `DiscoverResult` comes back. The server is modern.
2. A recognizable modern JSON-RPC error comes back. The server is modern. Retry with a revision from the error's `data.supported`, and do **not** fall back.
3. Anything else, including a timeout. Treat the server as legacy and fall back to `initialize`.

One requirement deserves emphasis: the fallback **must not** be keyed to one specific error code. A legacy server can fail in any number of ways, including silence.

**The HTTP probe.** Attempt a modern request. On `400 Bad Request`, look at the body. A recognizable modern JSON-RPC error means the server is modern and the error is the thing to read. An empty or unrecognizable body means fall back to `initialize`.

The compatibility outcomes, condensed from the versioning page:

| Client | Server | Outcome |
|---|---|---|
| modern | modern | Works |
| modern | legacy | **Fails.** The server may reject, stay silent, or process an ambiguous method under legacy rules |
| dual-era | modern | Works, staying modern |
| dual-era | legacy | Works, falling back to `initialize` and possibly to HTTP+SSE |
| legacy | modern | **Fails.** Legacy clients have no fall-forward mechanism |
| legacy | dual-era | Works, under the negotiated legacy revision |

That last pair is why the specification asks a modern-only server to name the protocol revisions it supports in whatever error it returns to an `initialize` request, on any transport. A legacy client cannot recover, and that message may be the only diagnostic a user ever sees.

**The SDK used here is dual-era.** The `mcp==2.3.0` server used throughout this post serves both eras. Sent a legacy `initialize` naming 2025-06-18, with no `MCP-Protocol-Version` header, it answered under that revision, on an event stream, and minted a session as a legacy server would:

```text
--- a legacy initialize naming 2025-06-18
HTTP 200  text/event-stream
protocolVersion answered  2025-06-18
mcp-session-id minted     True
```

A probe against it therefore reports "modern" from `server/discover` and "legacy" from `initialize`, which is exactly why era is defined as a property of the server and cached rather than inferred per request. That is a property of this implementation, not a protocol fact: a modern-only server answers the same `initialize` with an error that names the revisions it supports.

## 8. Security that lives at this layer

Most MCP security belongs to later posts. Three items belong here, because they are transport configuration and nothing else can fix them.

**Bind to `127.0.0.1`.** When running locally, servers **should** bind only to loopback rather than to `0.0.0.0`. A local server on all interfaces is reachable by anything that can route to the machine, including everything else on a coffee-shop network, and a typical local server can reach the user's files, credentials, and processes. In this SDK the choice has a second effect: binding to `127.0.0.1`, `localhost`, or `::1` turns on its protection against Domain Name System (DNS) rebinding, which checks the `Host` and `Origin` headers of every request. Bound to any other address with no `transport_security=` argument, the SDK's source switches those checks off entirely.

**Validate `Origin` on every incoming connection.** Servers **must** do this to prevent DNS rebinding attacks, in which a page in the user's browser is made to resolve a hostname to `127.0.0.1` and then talk to the local server from the user's own network position. If `Origin` is present and invalid the server **must** answer `403 Forbidden`; the body **may** be a JSON-RPC error with no `id`. The demonstration server, sent `Origin: http://evil.example`:

```text
--- Origin: http://evil.example
HTTP 403  (no content-type)
Invalid Origin header
```

The body is plain text rather than a JSON-RPC error, which the **may** permits.

**Never put a token in a query string.** Servers **should** implement proper authentication for all connections, and OAuth 2.1 is [post 20](../20-authorization/index.md). The transport-level rule is simpler than any of that: credentials belong in the `Authorization` header, never in a uniform resource locator (URL). Query strings are written to access logs by the default log formats of common proxies and web servers, survive in browser history, and leak through the `Referer` header. A token in a header is not encrypted either, but it is not routinely written to disk by intermediaries that never meant to keep it. The same reasoning is why section 5 warns against marking secrets with `x-mcp-header`.

One line for the other transport. A stdio server is not sandboxed. It runs as the user who started the host, with that user's environment, filesystem, and credentials, and the only thing between a malicious server and the machine is the host's permission gate. [Post 19](../19-security/index.md) is about what that gate does and does not stop.

## 9. Choosing between them

The rule is deployment shape, not preference, and one question settles the common cases: **who is allowed to run this process?** If the answer is "the person sitting at the machine", the host should spawn it, and that is stdio. If it is "a service account, once, for everybody", it listens, and that is Streamable HTTP.

| Situation | Transport |
|---|---|
| A desktop host on the user's own machine | stdio |
| The server needs local files, local processes, or local credentials | stdio |
| Distributed as an MCP Bundle (MCPB) or run with `uvx`, the `uv` tool runner | stdio |
| One deployment serving a team or the public | Streamable HTTP |
| The host runs in a browser | Streamable HTTP |
| OAuth 2.1, rate limiting, or an audit trail at the edge | Streamable HTTP |
| Scaling horizontally, or deploying without restarting hosts | Streamable HTTP |
| Tests | Neither. Connect in memory, [post 12](../12-testing-and-debugging/index.md) |

Two corrections to the reflex that "stdio is local, HTTP is remote".

**Streamable HTTP on `127.0.0.1` is an excellent local choice**, and often the better one during development. Restarting a listening server does not require restarting the host, and it can be driven with `curl` or the no-SDK client of [post 03](../03-wire-protocol/index.md) while the real host is connected.

**stdio is not automatically safer.** It has no network exposure, which removes one attack class, but it hands the process the user's full local privilege, which adds another. Loopback HTTP with `Origin` validation is a defensible local posture too.

The reason this choice stays cheap is that it is one line. `stdio_hello.py` and `http_hello.py` register the same tool and differ only in the `run()` call behind `--serve`, and the server in [code/05-first-server/](../../code/05-first-server/) runs either way from one file: `mcp.run("streamable-http", host=args.host, port=args.port)` when it is given `--http`, and a bare `mcp.run()` otherwise. Tools written against the protocol, with logging kept on standard error, leave the transport a deployment decision that can be revisited later.

## 10. Make it run: four scripts

Every transcript in this post is printed by one of four scripts in `snippets/`. Each runs from the series root with no arguments, needs only the root `pyproject.toml` environment (the pinned `mcp[cli]==2.3.0` and `httpx`), starts whatever server it needs on standard input and output or on a free loopback port, stops it, and exits:

```text
uv run python posts\04-transports\snippets\stdio_hello.py
uv run python posts\04-transports\snippets\stdout_trap.py
uv run python posts\04-transports\snippets\http_hello.py
uv run python posts\04-transports\snippets\progress_stream.py
```

Each takes between about 3 and 10 seconds, most of it starting Python processes; `stdout_trap.py`, which starts five servers, is the slowest. The serving path of each file sits behind `--serve`, which the no-argument run never takes. These two commands run the post's server for real:

```text
uv run python posts\04-transports\snippets\stdio_hello.py --serve
uv run python posts\04-transports\snippets\http_hello.py --serve
```

The first is the command a host's configuration names; post 05 covers that configuration. Started by hand in a terminal it writes nothing to standard output and waits for JSON-RPC lines on standard input, which is correct behavior for a stdio server, not a hang; end of file (Ctrl+D, or Ctrl+Z then Enter on Windows) stops it. The second listens on `http://127.0.0.1:8000/mcp` until Ctrl+C, and the no-SDK client of post 03 can be pointed at it from a second terminal:

```text
uv run python posts\03-wire-protocol\snippets\raw_discover.py http://127.0.0.1:8000/mcp
```

| Section | Script | What it prints |
|---|---|---|
| 2 | `stdio_hello.py` | three frames by hand, exit code 0, then the SDK client's view |
| 3 | `stdout_trap.py` | where the stray writes land with buffered output, line-buffered output, and a startup banner, then the cheap check twice |
| 4 | `progress_stream.py` | the headers and raw event stream of one `tools/call` with progress |
| 4 to 8, 11 | `http_hello.py` | derived headers, the Base64 table, discovery, and every status this server returns |

## 11. What can go wrong?

Each failure below is printed by `http_hello.py`, which sends the faulty request on purpose.

**The headers are written separately from the body, and drift.** A client that builds `Mcp-Name` from a stale variable, or forgets `Mcp-Method`, gets `-32020` either way, and the message names the header:

```text
--- Mcp-Name does not match the body
HTTP 400  application/json
{"jsonrpc":"2.0","id":6,"error":{"code":-32020,
 "message":"mcp-name header does not match the request body's 'name' parameter"}}

--- Mcp-Method omitted
HTTP 400  application/json
{"jsonrpc":"2.0","id":7,"error":{"code":-32020,
 "message":"mcp-method header does not match the request body's method"}}
```

Deriving every header from the finished body, as `headers_for` does, removes the class of bug.

**`Accept` lists `application/json` only.** The specification requires both types, and this SDK enforces it before reading the body: every such request gets `406 Not Acceptable` with an empty body, not just the first one that would have streamed. A status the specification's table never mentions, with nothing to read, is the symptom.

```text
--- Accept lists application/json only
HTTP 406  (no content-type)
(empty body)
```

**Code ported from stdio sends `notifications/cancelled` over HTTP.** There is no such notification on this transport; closing the response stream is the cancellation. This SDK takes the specification's accepting branch for a notification body, `202 Accepted` with no body, and its source states that the notification is then dropped. The client sees success, and nothing is cancelled:

```text
--- a notification body
HTTP 202  (no content-type)
(empty body)
```

A `202` proves only that the POST arrived. To stop work over HTTP, the client closes the response stream of the request it wants stopped.

**The header names a legacy revision while the body names 2026-07-28.** The specification prescribes `-32020` for a disagreement between `MCP-Protocol-Version` and the body. This SDK, at 2.3.0, still routes requests to its legacy or modern code by the header alone, so the request reached the legacy path, which looked for a session and answered with a different error:

```text
--- header names 2025-06-18, body _meta names 2026-07-28
HTTP 400  application/json
{"jsonrpc":"2.0","id":null,"error":{"code":-32600,
 "message":"Bad Request: Missing session ID"}}
```

A "Missing session ID" from a server that has no sessions means the header and the body disagree about the revision. The divergence from the specification is the SDK's, and a comment in its source describes header-only routing as the arrangement "for now".

**A reverse proxy forwards a real hostname to a server bound to loopback.** The rebinding protection of section 8 checks `Host` as well as `Origin`, so every request forwarded with the public hostname is refused until `transport_security=TransportSecuritySettings(...)` names that host:

```text
--- Host: mcp.example.com, as a reverse proxy might forward it
HTTP 421  (no content-type)
Invalid Host header
```

---

## Common pitfalls

1. **Calling `print()` anywhere in a stdio server.** Even under an SDK that diverts writes made during the session, a banner flushed before `mcp.run()` or text buffered until exit reaches the pipe, and the parse error names neither the file nor the function; configure `logging` to `sys.stderr` and run the cheap check with the host's exact command, reading to end of file.
2. **Letting a child process inherit standard output.** `subprocess.run(["git", "log"])` with no `stdout=` argument writes wherever descriptor 1 points, which is the frame stream in any server without this SDK's diversion; pass `stdout=subprocess.PIPE` or `stdout=subprocess.DEVNULL` for every subprocess a stdio server launches.
3. **Retrying a broken stream with the same request id.** There is no resumability and no `Last-Event-ID`, and the specification requires the re-issued request to carry a new id.
4. **Expecting a `GET` stream or a session.** A modern server answers the `GET` with `405` and ignores the session header; code that opened a `GET /mcp` stream wants `subscriptions/listen` instead.
5. **Binding a local server to `0.0.0.0` because an example did.** It exposes the server to the network, and in this SDK it also switches off the `Host` and `Origin` checks that loopback turns on.

---

## Further reading

- Specification, *Transports*, revision 2026-07-28: the Streamable HTTP page (the endpoint rule, the send and receive rules, the header set, the status-code table, cancellation by stream close, what earlier revisions had, and the HTTP probe) and the stdio page (the framing rules of section 2, the stdout requirement, shutdown, and the stdio probe). <https://modelcontextprotocol.io/specification/draft/basic/transports>
- Specification, *Versioning and compatibility*, revision 2026-07-28. The modern, legacy, and dual-era model, and the compatibility matrix of section 7.
- Specification, *Deprecated features registry*, revision 2026-07-28. Where HTTP+SSE now lives. <https://modelcontextprotocol.io/specification/draft/deprecated>
- SEP-2243, the `Mcp-Method` and `Mcp-Name` routing headers. `Mcp-Method`, `Mcp-Name`, the `x-mcp-header` annotation, `Mcp-Param-{Name}`, the Base64 sentinel, and the reasoning behind mandatory server-side validation.
- SEP-2575, *Stateless MCP*, and SEP-2567, *Sessionless MCP*. Why the session and the handshake were removed, and the load-balancing argument of section 6.
- Model Context Protocol, *"The 2026-07-28 MCP Specification Release Candidate"* (2026). The round-robin sentence quoted in section 6.
- RFC 9110 section 5.1, *HTTP Semantics*. The field-name token syntax an `x-mcp-header` value must satisfy. <https://datatracker.ietf.org/doc/html/rfc9110#section-5.1>
- MCP Python SDK, release 2.3.0. The server and clients of every snippet, the stdio transport's diversion of standard output in section 3, and the HTTP behavior quoted in sections 4 to 11. <https://github.com/modelcontextprotocol/python-sdk>

Full citations are in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 05 - Your first MCP server](../05-first-server/index.md):** the same two `run()` calls on a server that does something worth calling, connected to a real host.
- **[Post 21 - Deploying to production: containers, scaling, and observability](../21-deploying/index.md):** the round-robin deployment that section 6 argues for, with containers and health checks.
