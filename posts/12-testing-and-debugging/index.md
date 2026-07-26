# 12 · Testing and debugging MCP

> **TL;DR.** A Model Context Protocol (MCP) server is an ordinary library with a protocol in
> front of it, and `Client(server_object)` puts a real client on the other end of that
> protocol with no subprocess, no socket, and no host. This post builds three levels of test
> around that fact, then covers the two async failures that will cost you an afternoon,
> driving the multi-round trip with no human in the loop, reading a wire trace, the Inspector,
> and the conformance suite. Every result line quoted here came from running the suites in
> [code/](../../code/), and it closes with the five failures that account for most bug
> reports.
>
> **After reading this you will be able to:**
> - Write in-memory protocol tests that drive a real client against your server object.
> - Assert on the published schema, not just the returned value, so a silent `outputSchema: null` fails a test.
> - Drive the elicitation round trip through accept, decline, and cancel without a human.
> - Read a wire trace and find the two places a failure hides.

![Three stacked panels. The top panel is unit tests calling your own functions directly with no protocol involved. The middle panel is in-memory protocol tests, where a real client object talks to a real server object through the full protocol path with no process boundary between them. The bottom panel is a live host running the server as a separate process over standard input and output. Each panel lists what that level catches, what it cannot see, and roughly what it costs to run.](diagrams/01-three-levels-of-test.svg)
*Each level catches what the level above it structurally cannot, and each one costs more to run.*

---

## 1. The server works and the host says it does not

Here is the report you will get, and it will not include a stack trace.

"Your server is broken." Sometimes: "the model keeps saying it can't find the tool."
Sometimes nothing at all, just a user who tried it once and moved on. Meanwhile the server
runs fine on your machine, and the function the tool wraps has unit tests, and they pass.

The gap is the protocol. A tool is a Python function plus a published schema plus a result
shape, and only the function is in your test suite. The schema is generated for you, the
result shape is assembled for you, and both are capable of being wrong in ways that raise
nothing. [Post 06](../06-tools-in-depth/index.md) showed the sharpest example: a return
annotation without class-body annotations publishes `outputSchema: null`, and the tool then
ships the object's memory address to the model as a successful answer.

The reason so few MCP servers have protocol tests is not laziness. It is that the obvious way
to write one looks expensive. Spawn the server as a child process over standard input and
output (stdio), speak JavaScript Object Notation Remote Procedure Call (JSON-RPC) at it, wait
for frames, tear it down. That is slow, it is awkward on Windows, and it turns a unit test
suite into an integration suite. So people test the functions, skip the protocol, and ship.

There is a third option, and the whole post rests on it:

```python
async with Client(mcp) as c:
    tools = (await c.list_tools()).tools
```

`Client(mcp)` connects a real client straight to the server object. No subprocess, no socket,
no port. The request still travels the genuine protocol path, so schema generation, argument
validation, result assembly, and the elicitation round trip all really happen. Both suites
this post quotes are built on that one line.

## 2. Three levels, and what each one catches

Three levels, and the useful question about each is not "is this a unit test" but "what can
this level structurally not see".

| Level | What it drives | Catches | Cannot see |
|---|---|---|---|
| **1. Unit** | your own functions, no MCP imports | ranking, parsing, clamping, validation, every branch of your logic | the schema, the result shape, the protocol, anything the SDK generates |
| **2. In-memory protocol** | `Client(server_object)` | published schemas, annotations, `isError`, structured content, resource Uniform Resource Identifier (URI) matching, the multi-round trip, capability errors | the transport, stdout corruption, packaging, host configuration |
| **3. Live host** | the real server process in a real host | stdout corruption, the launch command, the config file, packaging, what a model actually does with your descriptions | nothing much, but it is manual, slow, and not deterministic |

Level 2 is the one that is usually missing, and it is also cheaper than almost anyone expects.
Here are two runs of the same project, split by level. The knowledge-base project in
[code/23-knowledge-base/](../../code/23-knowledge-base/) keeps its retrieval layer free of
any SDK import, so `test_index.py` is pure level 1 and `test_server.py` is pure level 2:

```
$ pytest tests/test_index.py -q
23 passed in 1.48s

$ pytest tests/test_server.py -q
28 passed in 1.47s
```

Twenty-eight protocol tests cost the same as twenty-three plain function tests. There is no
process to start and nothing to wait for, so the protocol layer adds almost nothing to the
clock. Whatever you assumed testing MCP would cost, this is the real number.

Level 3 does not disappear. It catches a specific and nasty class of bug that levels 1 and 2
cannot reach by construction, because both of them run inside one process and the bug is
*about* the process boundary. A `print()` in a tool body is invisible in memory and fatal over
stdio. Keep level 3, run it by hand before you publish, and do not try to make it your
regression suite.

## 3. In-memory, without a subprocess

Wire format first, since that is the order that lets you debug.

A `tools/call` is one JSON-RPC request with a `name`, an `arguments` object, and a mandatory
`_meta` block. Revision 2026-07-28 has no `initialize` handshake and no session, so every
request carries its own protocol version and its own declared capabilities. This is the
shape:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "get_system_info",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": { "name": "ExampleClient", "version": "1.0.0" },
      "io.modelcontextprotocol/clientCapabilities": { "elicitation": { "form": {} } }
    }
  }
}
```

`Client(server_object)` builds exactly that request and hands it to the server's request
handler. What it skips is the serialization and the pipe. The software development kit (SDK)
dispatches on the shape of the argument you pass: an `MCPServer` or a low-level `Server`
becomes an in-process pair of direct dispatchers, a string becomes a Streamable Hypertext
Transfer Protocol (HTTP) client, and anything else is treated as a transport. The negotiated
revision in the in-process case is `2026-07-28`.

So the same client class covers all three of these, and only the argument changes:

```python
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

Client(mcp)                                          # in memory, level 2
Client(stdio_client(StdioServerParameters(...)))     # a real subprocess, level 3
Client("http://127.0.0.1:8000/mcp")                  # a running HTTP server
```

There is no `Client("stdio://...")` form. A plain string always means an HTTP Uniform
Resource Locator.

The test file needs one piece of configuration and no fixtures. Both projects put this in
`pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`asyncio_mode = "auto"` is what lets a bare `async def test_...` run without a decorator on
every function. With that in place a complete protocol test is a handful of lines:

```python
async def test_system_snapshot_is_structured():
    async with Client(mcp) as c:
        result = await c.call_tool("get_system_info", {})
        assert result.result_type == "complete"
        data = result.structured_content
        assert 0 <= data["cpu_percent"] <= 100
```

That is from [tests/test_server.py](../../code/05-first-server/tests/test_server.py), and it
asserts on three things a unit test could not have reached: that the call completed rather
than asking for input, that the result carried structured content at all, and that the
structured content has the keys the output schema promises.

**Running the suites.** These are the two commands, run on Windows, where `PYTHONPATH`
separates with a semicolon:

```bash
cd code/05-first-server && PYTHONPATH=src pytest tests -q
```
```
...................                                                      [100%]
19 passed in 6.24s
```

```bash
cd code/10-mcp-client && PYTHONPATH="src;../05-first-server/src" pytest tests -q
```
```
....................................................................     [100%]
68 passed in 6.58s
```

The sixty-eight are the client and host project in
[code/10-mcp-client/](../../code/10-mcp-client/), and most of them drive the real
system-information server in memory. Note the second `PYTHONPATH` entry: the client suite
imports the server package directly, because the honest way to test a client is against a
server you did not write for the occasion.

The nineteen take six seconds, which looks slow next to the knowledge-base numbers above
until you notice `watch_cpu`, a tool that samples the processor once a second for two
seconds. That is the tool's own work, not the protocol's. Time in an in-memory suite is
always your own code.

## 4. Two rules that make async tests work

These two will cost you more time than anything else in this post. Neither is documented
anywhere you would look, and both produce error messages that point at the wrong thing.

### Do not hand the client over from a yield fixture

The reflex is right and the result is wrong:

```python
@pytest.fixture
async def client():
    async with Client(mcp) as c:
        yield c          # every test in the file now errors
```

Run two ordinary tests against that fixture and this is what comes back, verbatim:

```
.E.E                                                                     [100%]
=========================== short test summary info ===========================
ERROR test_yield_fixture.py::test_list_tools - RuntimeError: Attempted to exi...
ERROR test_yield_fixture.py::test_call_a_tool - RuntimeError: Attempted to ex...
2 passed, 2 errors in 1.65s
```

Read the counts. **The tests passed.** The errors are in teardown, which is why the first
thing you will do is stare at test bodies that are not the problem. The full message is:

```
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

The client owns an `anyio` task group, and a task group must be exited by the same task that
entered it. A yield fixture enters the context in the fixture's task and exits it later,
after the test function has run, in a different one. Nothing about your test is wrong; the
lifetime is.

The fix is to open the client inside the test body, every time:

```python
async def test_every_tool_declares_annotations():
    async with Client(mcp) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations is not None, f"{tool.name} has no annotations"
```

It looks repetitive and it is worth it. Every test file in
[code/](../../code/) is written this way, and each one says so in its module docstring,
because the failure is so unhelpful that a future reader deserves the warning.

What you *can* share through a fixture is anything that is not an async context: a loaded
corpus, a temporary directory, a fake API. The knowledge-base project does exactly that,
with a module-scoped `corpus` fixture feeding the level 1 tests and no fixture at all in the
level 2 file.

### An exception escaping the client arrives wrapped

The second rule follows from the same task group. When an exception leaves an
`async with Client(...)` block, the task group re-raises it wrapped:

```python
try:
    async with Client(mcp) as c:
        await c.list_tools()
        raise ZeroDivisionError("boom")
except ZeroDivisionError:
    print("A: caught as ZeroDivisionError")
except BaseException as exc:
    print(f"A: caught as {type(exc).__name__}: {exc}")
    print(f"A: .exceptions -> {getattr(exc, 'exceptions', None)}")
```

prints:

```
A: caught as ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
A: .exceptions -> (ExceptionGroup('unhandled errors in a TaskGroup', [ZeroDivisionError('boom')]),)
```

The first `except` clause never fires. `pytest.raises(ZeroDivisionError)` does not match
either. And note the nesting: it is a group inside a group, so one level of unwrapping is not
enough. The client project ships the helper that handles it, in
[tests/test_client.py](../../code/10-mcp-client/tests/test_client.py):

```python
def flatten(exc: BaseException) -> list[BaseException]:
    """Unwrap nested ExceptionGroups."""
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [inner for e in nested for inner in flatten(e)]
```

used as:

```python
with pytest.raises(BaseException) as excinfo:
    async with open_connection(spec()) as conn:
        await conn.list_tools()
        raise ZeroDivisionError("boom")

assert any(isinstance(e, ZeroDivisionError) for e in flatten(excinfo.value))
```

This is not only a testing concern. Host code that wraps a client call in
`except ConnectionError` has the same hole, and the symptom in production is an exception
handler that silently never runs.

## 5. Assert on the schema, not just the result

A test that calls a tool and checks the answer proves the function works. It does not prove
the model can find the tool, fill it in, or read what comes back. Those live in the published
surface, and the published surface deserves its own tests.

Four of them earn their place in every server. The first three are from the
system-information server's
[tests/test_server.py](../../code/05-first-server/tests/test_server.py), and the fourth is
from the knowledge-base project.

**Every tool publishes an output schema.** This one exists because of the silent failure in
[Post 06](../06-tools-in-depth/index.md): a return class with no class-body annotations
yields `outputSchema: null`, with no exception, no warning, and no log line.

```python
async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"
```

The `assert tools` line is not padding. A server that registered nothing at all would pass a
`for` loop over an empty list, and "my tools do not appear" is a real and common bug.

**Every tool declares annotations, and the destructive one says so.** Annotations are hints
and never enforcement, so this test is not a security control. It catches your own omission,
which is what a host reads when it decides whether to prompt the user.

```python
async def test_destructive_tool_is_marked_destructive():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        ann = tools["terminate_process"].annotations
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False
```

**A parameter resolved by elicitation stays out of the published schema.** This is the
security test, and it is the one worth copying into any server that confirms anything.
`terminate_process` takes an `approval` parameter that the server fills in by asking the user.
If that parameter reached `inputSchema`, a model could supply its own approval and the
confirmation step would be decorative.

```python
async def test_resolved_parameter_is_hidden_from_the_model():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        props = tools["terminate_process"].input_schema["properties"]
        assert set(props) == {"pid"}
```

The equality is deliberate. `assert "approval" not in props` would pass forever while a
second resolved parameter you add next year quietly leaks.

**Every capability is reachable through the primitive the poorest client supports.** The
knowledge-base project claims one server works on every client, and the honest form of that
claim is that a tools-only client loses presentation and never capability. So the claim is a
test rather than a sentence, in
[tests/test_server.py](../../code/23-knowledge-base/tests/test_server.py):

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

Add a resource without adding a tool path to it and the suite fails. That is a design rule
enforced by the build rather than by review.

One more, from the same project, that is not about the protocol at all and is the single
highest-value test in the file:

```python
def test_the_corpus_is_actually_committed(corpus):
    assert len(corpus) >= 5, "knowledge/ should contain the committed handbook"
```

A server whose data directory is missing starts cleanly, answers every search with nothing,
and looks exactly like a ranking bug. Asserting that your fixtures exist turns twenty minutes
of confusion into one red line.

## 6. Testing the multi-round trip without a human

Under revision 2026-07-28 there is no server-to-client request channel. A server that needs an
answer from the user does not call into the client and block; it returns an
`InputRequiredResult` carrying the questions and an opaque `requestState`, and the client
calls the same tool again with the answers attached. That mechanism is Multi Round-Trip
Requests (MRTR), and [Post 08](../08-elicitation-and-mrtr/index.md) covers it in full.

Its testing story is unusually good, because the human is already an injectable callback.
Pass an `elicitation_callback` to `Client` and the SDK drives the whole loop for you:

```python
def answering(action: str, content: dict | None = None, seen: list | None = None):
    """A client whose user always responds to elicitation the same way."""

    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        return ElicitResult(action=action, content=content)

    return Client(mcp, elicitation_callback=callback)
```

Four tests then cover the four outcomes, each only a few lines long:

```python
async def test_declining_does_not_terminate():
    async with answering("decline") as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "declined" in result.content[0].text.lower()


async def test_answering_no_does_not_terminate():
    async with answering("accept", {"confirm": False}) as c:
        result = await c.call_tool("terminate_process", {"pid": 999999})
        assert "answered no" in result.content[0].text.lower()
```

with `cancel` and `accept {"confirm": True}` alongside them. Three of those four are the
paths a manual test would never bother with, and they are precisely the paths where a server
either terminates something it should not or crashes on a `None` content field.

**Passing a callback is also what declares the capability.** This catches people out because
the callback is not invoked on the manual path, so it looks optional. It is not. A client with
no `elicitation_callback` declares no elicitation capability, and the server refuses before it
ever asks:

```
MCPError: Client did not declare the form elicitation capability required by resolver
'system_info.interactive:_confirm_terminate'
```

with code `-32021` (`MissingRequiredClientCapability`). Worth a test of its own, because the
failure is otherwise reported as "the tool is broken":

```python
async def test_without_an_elicitation_callback_the_call_fails_cleanly():
    async with open_connection(spec()) as conn:
        outcome = await conn.call_tool("terminate_process", {"pid": 999999})
        assert outcome.ok is False
        assert "elicitation" in outcome.for_model().lower()
```

**And test that the question converges.** This is the MRTR bug you will not see coming. The
SDK matches a recorded answer against a digest of the question the server asked. If the
question text varies between rounds, because it contains a timestamp or a live reading or a
random identifier, the answer never matches, the server asks again, and the call spins until
it hits `input_required_max_rounds` and raises. The test is to count the questions:

```python
async def test_the_question_is_asked_once_and_names_the_process():
    seen: list[str] = []
    async with answering("decline", seen=seen) as c:
        await c.call_tool("terminate_process", {"pid": 4242})

    assert len(seen) == 1
    assert "4242" in seen[0]
```

One question means the digest matched. The recorded text, from
[verify/RESULTS.md](../../verify/RESULTS.md), is
`Terminate process 4242 (unknown)? This cannot be undone.`, which is derived from the
arguments and nothing else. That is the property the test is really pinning.

## 7. Reading a wire trace

At some point the tests pass and the host still misbehaves, and you need to look at the
messages. Two things make that quicker: knowing which fields to read in order, and knowing
that a failure can hide in a response that looks entirely successful.

![A client box on the left sends one tools/call request to a server box on the right. The full request is printed below with its mirrored headers and its params and metadata fields, and four numbered annotations name the checks to make, in order. Below that, three possible responses sit side by side: a successful result carrying isError false, a successful result carrying isError true which is a tool that ran and failed and which nothing raises on, and a JSON-RPC error object with a numeric code and no result member. The second and third are the two places a failure hides.](diagrams/02-annotated-wire-trace.svg)
*One request, three responses. Two of them are failures and only one of them looks like one.*

**On the request, read these four in this order.** The example is the specification's own
annotated trace for a `tools/call`.

```http
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: create_weather_gist
Mcp-Param-Region: us-west1
```

1. `MCP-Protocol-Version` against `_meta["io.modelcontextprotocol/protocolVersion"]`. They
   must be identical or the server answers `-32020` (`HeaderMismatch`) with HTTP `400`. A
   mismatch here is the most common cause of a server that rejects every request.
2. `_meta["io.modelcontextprotocol/clientCapabilities"]`. It is re-sent on every request,
   because servers must not infer capabilities from earlier ones. A missing `elicitation` key
   is why a tool that asks questions fails with `-32021`.
3. `params.name` against `Mcp-Name`, and each `Mcp-Param-*` header against the property it
   mirrors. The headers exist so a load balancer can route without parsing the body, and the
   server must verify every one of them.
4. `params.arguments`. Compare it against `inputSchema`, not against your function signature.
   They are not the same object, and the schema is the one the model saw.

**On the response, read `resultType` first, then `isError`.** A `resultType` of
`"input_required"` means the request is complete and closed and the server is waiting for a
retry with `inputResponses` and the echoed `requestState`. A `resultType` of `"complete"`
means the call is finished, and it says nothing at all about whether the tool succeeded.

Here is the successful shape, from the same trace:

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "resultType": "complete",
    "content": [ { "type": "text", "text": "{\"url\":\"https://gist.github.com/octocat/a1b2c3\"}" } ],
    "structuredContent": { "url": "https://gist.github.com/octocat/a1b2c3" },
    "isError": false
  }
}
```

**The two places a failure hides.** The first is the obvious one: a JSON-RPC `error` object,
which has a numeric `code` and **no `result` member at all**. Unknown method, malformed
request, header mismatch, missing capability. Your client raises on these, so you find them.

The second is not obvious, and it is the defect this series fixed most often. A tool that ran
and failed returns a **successful response** carrying `isError: true`, and the specification
is explicit that this is correct, so the model can read the reason and self-correct. Here is a
real one, captured from the system-information server by calling `find_process` with a string
where an integer belongs:

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

Nothing raised. `resultType` is `"complete"`. A client that only wraps the call in
`try`/`except` hands that text to the model as the answer to its question. The client project
has a test whose entire job is to make that visible:

```python
async def test_the_raw_result_would_have_looked_successful():
    async with Client(system_info_server) as client:
        raw = await client.call_tool("find_process", {"limit": "not-a-number"})

        assert raw.is_error is True
        assert raw.result_type == "complete"  # a *successful* response
        assert read_result("find_process", raw).ok is False
```

Two details worth pinning while you are here. `is_error` is a plain `bool` defaulting to
`False`, not `bool | None`, so passing `None` raises a pydantic `ValidationError`; absent and
`false` mean the same thing and your client must treat them that way. And an unknown tool name
comes back the same way from this SDK, as `isError: true` with the text
`Unknown tool: no_such_tool`, even though the specification lists unknown tool as a protocol
error. That is an SDK behavior rather than a protocol one, and it is worth re-checking when
stable 2.0 ships.

## 8. The Inspector

The Inspector is the official interactive client: a local web interface that lists your
tools, resources, and prompts, calls them with arguments you type, and shows the messages.

```bash
npx @modelcontextprotocol/inspector
```

The Python SDK also wires it up for you against a single file:

```bash
uv run mcp dev server.py
```

Use it for the things a test suite is bad at. Reading a tool description the way a model
would. Noticing that your `enum` has a typo in it. Watching an elicitation form render. Level
3 work, done by hand, once.

**Two honest cautions.**

The first is that this tool is moving. Version 1 of the Inspector is being replaced by a
different version 2 codebase alongside this protocol revision, so exact commands, flags, and
screenshots age quickly. Nothing in this post depends on a particular Inspector build, and
that is deliberate. Check the repository before you follow any tutorial's flags, including
this one's.

The second is security. CVE-2025-49596 (Common Vulnerabilities and Exposures) is an
unauthenticated remote code execution flaw in the Inspector before version 0.14.1, rated 9.4
critical, reachable from a web page the developer merely visits. Keep it current, keep it
bound to localhost, and never expose it on a network. It is a development tool that runs your
code on request, which is the whole point of it and also the whole problem.

## 9. The conformance suite

Your own tests check that your server does what you meant. They cannot check that what you
meant matches the specification, because you wrote both.

The conformance suite at <https://github.com/modelcontextprotocol/conformance> is the
independent half. It is maintained alongside the specification and drives an implementation
through the behaviors the specification requires, which is the only way to find out that you
have quietly invented a dialect. If you are writing a client or a host, it matters more than
anything in this post. A server has to be right about its own primitives; a client has to be
right about everything every server it meets might do.

Treat it as a release gate rather than a per-commit check. It moves with the specification,
and a revision bump will change what it expects.

## 10. Continuous integration, and ground truth

Two habits, both cheap.

**Run the suites on every push.** Level 1 and level 2 need no services, no containers, and no
network, which is the practical payoff of the in-memory pattern. A workflow for a repository
laid out like this one is the commands from section 3 with a matrix around them:

```yaml
# .github/workflows/test.yml
name: test
on: [push, pull_request]
jobs:
  pytest:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.10", "3.13"]
        project: [05-first-server, 23-knowledge-base]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --extra dev --python ${{ matrix.python }}
        working-directory: code/${{ matrix.project }}
      - run: uv run pytest -q
        working-directory: code/${{ matrix.project }}
```

`uv sync` installs each project into its own environment, so `uv run pytest` needs no
`PYTHONPATH` at all; the explicit variable in section 3 is only there because those runs used
a shared interpreter.

Include Windows in the matrix even if you do not use it. Path separators, console encodings,
and process teardown all differ there, and a server that only ever ran on Linux will meet a
Windows user eventually. The `PYTHONPATH` separator in this post's own commands is a semicolon
for exactly that reason.

**Regenerate the numbers you quote.** Anything a document claims about your server should be
produced by a script rather than typed by a person. This repository's version is
[verify/capture.py](../../verify/capture.py), which opens an in-memory client, walks the
published surface, drives the four elicitation outcomes, runs the test suite, and prints
Markdown to [verify/RESULTS.md](../../verify/RESULTS.md):

```python
async with Client(mcp) as c:
    for t in (await c.list_tools()).tools:
        props = ", ".join((t.input_schema or {}).get("properties", {})) or "none"
        out = "yes" if t.output_schema else "**null**"
        print(f"| `{t.name}` | {props} | {out} | ... |")
```

```bash
PYTHONPATH=code/05-first-server/src python verify/capture.py > verify/RESULTS.md
```

The committed output is diffed like any other file. If a change makes an output schema
disappear, the word `**null**` shows up in a pull request in a place a reviewer is already
looking. The rule is one line: if a document and the generated file disagree, the document is
wrong.

## 11. The five failures that account for most bug reports

Every one of these is a defect this series actually shipped and fixed. They are ordered by how
long each takes to find, shortest first.

**1. A `print()` under stdio.** Standard output is the protocol channel, and the server must
not write anything to it that is not a valid MCP message. One `print()` in a tool body puts a
bare word into the frame stream. The error the client reports names neither `print` nor your
tool:

```
Failed to parse JSONRPC message from server
pydantic_core._pydantic_core.ValidationError: 1 validation error for
  union[JSONRPCRequest,JSONRPCNotification,JSONRPCResponse,JSONRPCError]
  Invalid JSON: expected value at line 1 column 1
  [type=json_invalid, input_value='whoami called\r', input_type=str]
```

Worse, it is intermittent, because a short write can sit in the pipe buffer until the process
exits and corrupt nothing. It is also frequently not your `print`: a dependency's banner, a
progress bar, a stray `breakpoint()`, or a `subprocess.run(...)` with no `stdout=` argument,
since a child inherits your standard output. Configure logging to standard error on line one
of your first module. [Post 04](../04-transports/index.md) has the full anatomy.
**Caught by:** level 3, and only level 3.

**2. A missing output schema from a class with no class-body annotations.** The SDK builds an
output schema by calling `get_type_hints()` on your return type. A class whose attributes are
assigned only inside `__init__` has none, so the model is `None`, `outputSchema` is `null`, no
exception is raised, and the tool ships the object's `repr` to the model. Annotations on the
`__init__` parameters do not help; they are annotations on a function. Use a dataclass, a
`TypedDict`, or a Pydantic model, pass `structured_output=True` so the failure moves to
registration time, and keep `test_every_tool_publishes_an_output_schema`.
**Caught by:** level 2.

**3. Treating `isError` as a transport exception.** The call returns normally and carries a
failure, so a client built on `try`/`except` reports a validation error to the model as the
tool's answer. Every call site has to check `is_error`, and the cleanest way to guarantee it
is to funnel raised exceptions and `isError` results into one outcome type, so there is only
one shape to handle. That is what `read_result` does in
[code/10-mcp-client/](../../code/10-mcp-client/), and
`test_the_raw_result_would_have_looked_successful` is the test that keeps it honest.
**Caught by:** level 2.

**4. A non-deterministic elicitation question.** The server asks "Terminate process 4242,
started at 14:32:07?" and the timestamp changes between rounds. The recorded answer no longer
matches the digest of the question, so the server asks again, and the loop runs to
`input_required_max_rounds` and raises `InputRequiredRoundsExceededError`. From the outside it
looks like the user's answer was ignored. Derive the question text from the tool arguments and
nothing else, and count the questions in a test.
**Caught by:** level 2, and essentially never by hand.

**5. A yield-fixture cancel-scope error in the test suite itself.** Not a bug in your server
at all, which is what makes it expensive: you go looking in the wrong file. The tests pass and
the teardown errors, with a message about cancel scopes and tasks that mentions neither MCP
nor your code. Open the client with `async with` inside the test body. Section 4 has the
transcript.
**Caught by:** running your tests once and reading the summary line carefully.

---

## Common pitfalls

- **Testing only the function and not the published surface.** A tool is a function plus a
  schema plus a result shape, and the SDK generates two of those three. `list_tools()` in a
  test is how you find out what the model will actually see, and it costs milliseconds.
- **Handing the client to tests through a yield fixture.** The client owns an `anyio` task
  group, a task group must be exited by the task that entered it, and a yield fixture exits it
  somewhere else. Every test errors in teardown with a cancel-scope `RuntimeError` that names
  nothing you wrote. Use `async with` in the test body.
- **Writing `pytest.raises(SomeError)` around a client block.** The task group re-raises,
  wrapped, and sometimes doubly wrapped. Catch `BaseException`, flatten the group recursively,
  and assert on the members. The same hole exists in any host code that catches a specific
  exception type around a client call.
- **Assuming an `elicitation_callback` is optional because the manual path never calls it.**
  Passing one is what declares the capability. Without it a server that needs an answer
  returns `-32021` before it ever asks, and the report you get is "your tool is broken".
- **Writing `assert "approval" not in props` instead of asserting the whole property set.**
  The negative test passes forever while the next resolved parameter you add leaks into the
  published schema. Assert equality against the exact set you intend to publish.
- **Treating the Inspector as your test suite.** It is a level 3 tool: manual, stateful, and
  moving between major versions right now. It is also a development server with a critical
  remote code execution history, so keep it current and keep it on localhost.
- **Quoting a number in your documentation that no script can regenerate.** Schemas, counts,
  and result strings drift. Generate them into a committed file and let the diff do the
  review.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The `isError` rule, and why an execution
  failure is a successful response.
  <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Base protocol"* § Error codes, revision 2026-07-28. `-32020`, `-32021`, and
  `-32022`, their required `data` payloads, and the retirement of `-32002`.
- MCP Inspector. <https://github.com/modelcontextprotocol/inspector>. Check the repository for
  the current major version before following any command line, including this post's.
- CVE-2025-49596, *"MCP Inspector unauthenticated remote code execution before 0.14.1"*, 9.4
  critical. <https://nvd.nist.gov/vuln/detail/CVE-2025-49596>
- Conformance suite. <https://github.com/modelcontextprotocol/conformance>. The independent
  check that your implementation matches the specification rather than your reading of it.
- MCP Python SDK, `mcp==2.0.0b2`. Every transcript in this post came from this version, driving
  [code/05-first-server/](../../code/05-first-server/),
  [code/10-mcp-client/](../../code/10-mcp-client/), and
  [code/23-knowledge-base/](../../code/23-knowledge-base/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 13 — Project 1 · A secure database analyst](../13-database-analyst/index.md)**: the
  first project, where the tests in this post become the security tests that decide whether a
  model may touch a production database.
- **[Post 11 — Building a host: the tool loop, many servers, and permissions](../11-building-a-host/index.md)**:
  the permission gate whose tests section 5 borrows from, and the loop the client suite drives.
- **[Post 06 — Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md)**:
  the silent `outputSchema: null` failure that motivates half the assertions here.
