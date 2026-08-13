# 06 · Tools in depth: schemas, structured output, and annotations

> **TL;DR.** A tool's schema is the only thing a model reads before deciding to call it, so
> schema design, not implementation, decides whether a tool gets used correctly. This post
> reads the complete `Tool` object of Model Context Protocol (MCP) revision 2026-07-28 field
> by field, then covers input schemas, `outputSchema` and `structuredContent`, the five
> content block types, annotations, and naming. It gives real space to the return annotation
> that silently publishes no output schema at all, because that failure is invisible until a
> client complains. And it settles the error question: a tool that runs and fails returns a
> successful response carrying `isError: true`, never a JSON-RPC error.
>
> **After reading this you will be able to:**
> - Read and write every field of a `Tool` object, and say which two are required.
> - Design an input schema a model fills in correctly on its first attempt.
> - Publish an `outputSchema` deliberately, and prove with a test that you did.
> - Decide, for any failure, whether it belongs in a JSON-RPC `error` object or in a result with `isError: true`.

![A tools/call request on the left and its result on the right, with every field labeled: method, name, arguments and the metadata object on the request side, and resultType, content, structuredContent and isError on the result side.](diagrams/01-anatomy-of-a-tool-call.svg)
*One request and the result it produced, both captured from the server built in
[Post 05](../05-first-server/index.md).*

---

## 1. The schema is the interface

Here is a symptom you will recognize once you have shipped a server. The tool works. You
call it from a test and it returns exactly what it should. Then you connect it to a host, ask
the obvious question, and the model either does not call the tool at all or calls it with
arguments that make no sense.

Nothing is broken. The model never saw your Python. It saw four things: a name, a
description, an input schema, and, if you published one, an output schema. That is the
entire surface. The specification is blunt about the status of the description: it "can be
thought of like a 'hint' to the model."

So the work in this post is interface design, and the interface is JavaScript Object Notation
(JSON) Schema. [Post 05](../05-first-server/index.md) showed that type hints become a schema
and left it there. This post is about what that schema should contain, what the result should
carry back, and the handful of places where the Python software development kit (SDK) will do
something surprising and not tell you.

Every piece of output in this post came from the server in
[code/05-first-server/](../../code/05-first-server/), which reports central processing unit
(CPU), memory, and disk usage for the machine it runs on. It is pinned to `mcp==2.0.0b2`,
which implements revision 2026-07-28, and its test suite is 19 tests that pass. Where a
capture needed a tool the project does not have, the throwaway server that produced it is
printed alongside.

## 2. The complete `Tool` object, field by field

Wire format first. A `tools/list` result carries an array of these:

```typescript
export interface Tool extends BaseMetadata, Icons {
  name: string;                    // REQUIRED
  title?: string;
  icons?: Icon[];
  description?: string;
  inputSchema: { $schema?: string; type: "object"; [key: string]: unknown };  // REQUIRED
  outputSchema?: { $schema?: string; [key: string]: unknown };
  annotations?: ToolAnnotations;
  _meta?: MetaObject;
}
```

Two fields are required and the rest are not.

| Field | Required | What it is for |
|---|---|---|
| `name` | **Yes** | The identifier a `tools/call` names. Unique within one server. |
| `inputSchema` | **Yes** | JSON Schema for the arguments object. `type: "object"` at the root. |
| `title` | No | A human-readable label for a user interface. |
| `description` | No | Free text the model reads. In practice the most important optional field. |
| `outputSchema` | No | JSON Schema for `structuredContent`. Any valid JSON Schema 2020-12. |
| `annotations` | No | Behavioral hints. Never enforcement. Section 8. |
| `icons` | No | Icon references for a user interface. |
| `_meta` | No | Your own metadata, under a reverse Domain Name System prefix. |

A display name can come from three places, and `Tool` is the one type whose precedence is
unusual: `title` first, then `annotations.title`, then `name`. Nothing else in the schema has
an `annotations.title` at all, and the specification calls `Tool` out as the exception.

Here is one as the server sent it over standard input and output, with the keys reordered to
match the table above. This is the `terminate_process` tool from
[interactive.py](../../code/05-first-server/src/system_info/interactive.py):

```json
{
  "name": "terminate_process",
  "title": "Terminate a process",
  "description": "Terminate a running process, after the user confirms.\n\nArgs:\n    pid: The process id to terminate.\n",
  "inputSchema": {
    "type": "object",
    "properties": { "pid": { "title": "Pid", "type": "integer" } },
    "required": ["pid"],
    "title": "terminate_processArguments"
  },
  "outputSchema": {
    "type": "object",
    "properties": { "result": { "title": "Result", "type": "string" } },
    "required": ["result"],
    "title": "terminate_processOutput"
  },
  "annotations": {
    "title": "Terminate a process",
    "readOnlyHint": false,
    "destructiveHint": true,
    "idempotentHint": false,
    "openWorldHint": false
  }
}
```

Read the input schema against the Python. The function signature is
`terminate_process(pid: int, approval: Annotated[ElicitationResult[...], Resolve(...)])`, and
only `pid` reached the wire. A `Resolve` parameter is excluded from the published schema on
purpose, so the model cannot supply its own approval.
[Post 08](../08-elicitation-and-mrtr/index.md) is about that mechanism; here it is a reminder
that the signature and the schema are not the same object, and the schema is the one that
matters.

Now the decorator that produced it:

```python
@mcp.tool(
    title="Terminate a process",
    annotations=ToolAnnotations(
        title="Terminate a process",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def terminate_process(pid: int, approval: ...) -> str:
    """Terminate a running process, after the user confirms.

    Args:
        pid: The process id to terminate.
    """
```

The full decorator signature accepts `name`, `title`, `description`, `annotations`, `icons`,
`meta`, and `structured_output`. The parentheses are not optional: bare `@mcp.tool` raises a
`TypeError` telling you so.

## 3. Input schemas a model gets right

Look again at what `find_process` publishes:

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

Notice what is missing. The docstring says `limit` should be "between 1 and 50", and that
sentence went into the tool's `description`, not into the `limit` property. The SDK does not
parse a Google-style `Args:` block into per-property descriptions. A model reading only the
properties sees an integer with no bounds and no explanation.

The fix is `Annotated` with a Pydantic `Field`. Same tool, redesigned:

```python
from typing import Annotated, Literal
from pydantic import Field

@mcp.tool(name="find_process")
def find_process(
    name: Annotated[str, Field(description="Case-insensitive substring of the process name, for example 'chrome'.")],
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum processes to return.")] = 25,
    sort_by: Annotated[Literal["memory", "pid", "name"], Field(description="Sort key for the returned list.")] = "memory",
) -> str:
    """Find running processes by name."""
```

which publishes:

```json
{
  "type": "object",
  "properties": {
    "name": {
      "description": "Case-insensitive substring of the process name, for example 'chrome'.",
      "title": "Name", "type": "string"
    },
    "limit": {
      "default": 25, "description": "Maximum processes to return.",
      "maximum": 50, "minimum": 1, "title": "Limit", "type": "integer"
    },
    "sort_by": {
      "default": "memory", "description": "Sort key for the returned list.",
      "enum": ["memory", "pid", "name"], "title": "Sort By", "type": "string"
    }
  },
  "required": ["name"],
  "title": "find_processArguments"
}
```

Three things changed and all three are load-bearing. Every property now carries a
`description`, so the model can read the constraint where it is looking. `limit` carries
`minimum` and `maximum`, so a value out of range is a fact about the schema rather than a
surprise inside your function. And `sort_by` is an `enum`, which is the single highest-value
schema move there is: a closed set of strings is the one thing a model essentially never gets
wrong, whereas free text invites invention.

Four more rules worth having.

**The root must be an object.** `type: "object"` at the root is required. Beyond that,
SEP-2106 (Specification Enhancement Proposal 2106) opened the schema to any JSON Schema
2020-12 keyword, including `oneOf`, `anyOf`, `allOf`, `not`, `if`/`then`/`else`, `$ref`,
`$defs`, and `$anchor`.

**For a tool with no parameters, prefer the strict form.** The specification names
`{"type": "object", "additionalProperties": false}` as the recommended shape and
`{"type": "object"}` as the permissive alternative. The SDK emits the permissive one:
`get_system_info`, which takes nothing, publishes
`{"type": "object", "properties": {}, "title": "get_system_infoArguments"}`. That is legal,
and if you want the strict form you have to build the tool on the low-level server yourself.

**A `$ref` is never fetched over the network.** Implementations **must not** automatically
dereference a `$ref` that resolves to a network Uniform Resource Identifier (URI). A schema
that fails to validate because of an unresolved external `$ref` **should** be rejected rather
than quietly treated as permissive. If you were planning to host a shared schema and point at
it, do not.

**Composition keywords are a denial-of-service surface.** Implementations **should** bound
schema depth, subschema count, or validation time. That applies to you if you are the one
validating somebody else's schema, which is the client's job in
[Post 10](../10-mcp-client/index.md).

## 4. `outputSchema` and `structuredContent`

A tool result carries two representations of the same answer.

```typescript
export interface CallToolResult extends Result {
  content: ContentBlock[];        // REQUIRED (may be empty)
  structuredContent?: unknown;
  isError?: boolean;
}
```

`content` is prose and media for the model to read. `structuredContent` is data for a program
to use. The specification is explicit that `structuredContent` can be **any JSON value**,
object, array, string, number, boolean, or null, loosened by SEP-2106 from an
object-only rule in earlier revisions. It also asks that a tool returning structured content
**should** still put the serialized JSON in a text block, for clients that do not read the
structured field.

Here is a real `find_process` result, as the server sent it, with only the long duplicated
text block shortened:

```json
{
  "content": [
    { "type": "text", "text": "{\n  \"query\": \"explorer\",\n  \"total_matches\": 1, ... }" }
  ],
  "structuredContent": {
    "query": "explorer",
    "total_matches": 1,
    "returned": 1,
    "processes": [ { "pid": 8852, "name": "explorer.exe", "memory_mb": 141.8 } ]
  },
  "isError": false,
  "resultType": "complete"
}
```

The text block is the serialized structured content. The SDK does that duplication for you.

![Two columns showing the same tool returning a formatted string on the left and a dataclass on the right, with the published output schema and the data the model receives under each.](diagrams/02-structured-vs-unstructured.svg)
*The return annotation is the only difference. Everything below it follows from that one line.*

In the SDK, the return annotation drives `outputSchema`. The mapping is worth memorizing:

| Return annotation | Resulting `outputSchema` |
|---|---|
| Pydantic `BaseModel` subclass | Used directly |
| `TypedDict` | Converted to a model |
| Dataclass or class **with class-body annotations** | Converted through `get_type_hints()` |
| `str`, `int`, `float`, `bool`, `bytes`, `None` | Wrapped as `{"result": ...}` |
| `dict[str, X]` | A root model, no wrapping |
| `list[...]`, unions, `Optional`, `dict` with non-string keys | Wrapped as `{"result": ...}` |
| `CallToolResult` | None; you own the result |
| No annotation | None |

The wrapping row surprises people. A tool annotated `-> list[str]` does not produce a
top-level JSON array; it produces `{"result": ["a", "b"]}`. The protocol allows a top-level
array, and the specification prints one as an example, but reaching it through the high-level
API means returning a `CallToolResult` yourself:

```python
@mcp.tool()
def raw_array() -> CallToolResult:
    """Top-level array structuredContent, built by hand."""
    return CallToolResult(
        content=[TextContent(type="text", text='[{"id":"1"},{"id":"2"}]')],
        structuredContent=[{"id": "1"}, {"id": "2"}],
    )
```

which really does put `[{"id": "1"}, {"id": "2"}]` on the wire as `structuredContent`. The
cost is that `-> CallToolResult` publishes no `outputSchema` at all, so you have traded the
schema for the shape.

**Publishing a schema is a promise, and somebody checks.** If `outputSchema` is present,
servers **must** return conforming structured results and clients **should** validate them.
The Python client does validate, automatically, on every non-error result. Corrupting a
published schema so that the returned data no longer fits it produces this on the client:

```
RuntimeError: Invalid structured content returned by tool reading: 'units' is a required property
```

That error surfaces in the host, not in your server logs, which is exactly the kind of bug
that gets reported to you as "your server is broken" with no detail.

## 5. The return type that publishes nothing, silently

This is the trap the whole section exists for, and it has no warning attached to it.

The SDK builds an output schema from a class by calling `get_type_hints()` on it. A class
whose attributes are only ever assigned inside `__init__`, with no annotations in the class
body, has no type hints. The SDK's own comment says what happens next:

```python
# Case 4: Other class types (dataclasses, regular classes with annotations)
else:
    type_hints = get_type_hints(type_annotation)
    if type_hints:
        model = _create_model_from_class(type_annotation, type_hints)
    # Classes without type hints are not serializable - model remains None
```

`model remains None`. No exception, no warning, no log line. Here is the demonstration, and
the two classes differ only in where the attribute names are written:

```python
class Snapshot:
    """Looks like a fine return type. It is not."""
    def __init__(self, cpu_percent: float, memory_percent: float):
        self.cpu_percent = cpu_percent
        self.memory_percent = memory_percent

@dataclass
class AnnotatedSnapshot:
    """The same data, with class-body annotations."""
    cpu_percent: float
    memory_percent: float

@mcp.tool()
def silent() -> Snapshot: ...

@mcp.tool()
def loud() -> AnnotatedSnapshot: ...
```

Listing that server gives this, with the second schema wrapped to fit the page:

```
silent     outputSchema = null
loud       outputSchema = {"properties": {"cpu_percent": {"title": "Cpu Percent", "type": "number"},
                                          "memory_percent": {...}}, "required": [...],
                           "title": "AnnotatedSnapshot", "type": "object"}
```

Note that the annotations on the `__init__` parameters were not enough. They are annotations
on a function, not on the class.

The tool still registers. It still runs. And calling it returns this:

```json
{
  "content": [
    { "type": "text", "text": "\"<__main__.Snapshot object at 0x000001C4F447AF90>\"" }
  ],
  "isError": false,
  "resultType": "complete"
}
```

A memory address, marked as a success. That is what your model gets. Nobody raised anything,
nothing looked wrong on your machine, and the only person who finds out is the user watching
a model try to reason about a pointer.

There are two ways to make the failure loud, and you want both.

**Ask the SDK to insist.** `structured_output=True` converts the silence into a registration
error, which means an import-time crash rather than a runtime surprise:

```
InvalidSignature: Function silent: return type <class '__main__.Snapshot'> is not
serializable for structured output
```

The flag also has an inverse. `structured_output=False` disables structured output
unconditionally, which is the honest way to say "this tool returns prose on purpose".

**Assert on it in a test.** This is one test that catches every future instance of the bug,
and it is why [tests/test_server.py](../../code/05-first-server/tests/test_server.py) opens
with it:

```python
async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"
```

`Client(mcp)` connects to the server object in memory, with no subprocess and no socket, so
this runs in milliseconds and still goes through the real protocol machinery.
[Post 12](../12-testing-and-debugging/index.md) explains the pattern properly.

Two more return annotations that produce `outputSchema: null` and are easy to reach by
accident: `-> Image`, the SDK's image helper, and any function you simply forgot to annotate.
Neither is a mistake in itself. Both are worth being deliberate about.

## 6. Content blocks

`content` is an array, and there are exactly five block types.

**Text.** `type` and `text` are required. This is the overwhelming majority of all traffic.

**Image.** `type`, `data` (Base64), and `mimeType` are required. The SDK's `Image` helper
produces one:

```json
{ "type": "image", "data": "iVBORw0KGgoAAAANSUhEUgAA...", "mimeType": "image/png" }
```

**Audio.** The same three fields, with an audio media type.

**Resource link.** A pointer instead of a payload. It carries the full resource field set, so
`uri` and `name` are required and `title`, `description`, `mimeType`, `size`, and `icons` are
available. Resource links returned by a tool are not guaranteed to appear in
`resources/list`.

**Embedded resource.** `type: "resource"` wrapping a nested `resource` with either text or a
Base64 blob, plus its URI and media type. A server that uses these **should** implement the
resources capability, which is [Post 07](../07-resources-and-prompts/index.md).

All five accept optional `annotations` with `audience`, `priority`, and `lastModified`, which
is how you tell a host that a block is meant for the user's eyes rather than the model's
context.

Mixing them is the point. A tool that produces a large result should return a short summary
the model can read and a link to the bulk, rather than 12,000 rows of context:

```python
@mcp.tool()
def multi() -> CallToolResult:
    """Return several content blocks at once."""
    return CallToolResult(
        content=[
            TextContent(type="text", text="Rendered 1 chart and saved the raw data."),
            ResourceLink(
                type="resource_link",
                uri="file:///tmp/report.csv",
                name="report.csv",
                description="Full result set, 12000 rows",
                mimeType="text/csv",
            ),
        ],
        structuredContent={"rows": 12000},
    )
```

One trap for readers coming from a model provider's application programming interface (API):
`tool_use` and `tool_result` are **not** MCP content blocks. They belong to the sampling
message type only, and sampling is deprecated in this revision.

## 7. Errors that teach the model to retry

[Post 03](../03-wire-protocol/index.md) drew the taxonomy: a protocol error is a JSON-RPC
`error` object, a tool execution error is a successful response with `isError: true`, and MCP
has never defined an error code for a tool that ran and failed. An earlier edition of this
series said otherwise. It was wrong.

![One tools/call splitting into two paths: a protocol error returning a JSON-RPC error object that the client handles, which does not always arrive as HTTP 200 because several codes are pinned to 400 or 404, and an execution error returning a successful HTTP 200 result with isError true that is fed back to the model so it can retry.](diagrams/03-error-paths.svg)
*Left, the server could not act on the request. Right, the request was fine and the work did not succeed.*

This section is about what that means when you are the one writing the tool.

**Start from what the model will read.** The content block of a failed tool call is not a log
line. It is an instruction to a reader who is about to try again. Compare:

```
Error: invalid input
```

with

```
seconds must be between 1 and 30, got 500. Retry with a value in range.
```

The second names the constraint, names the offending value, and says what to do. That is a
tool call that succeeds on the second attempt. The first is a tool call that fails forever.
The specification's reasoning is that clients **should** hand execution errors to the model to
enable self-correction, and **may** hand it protocol errors, "though these are less likely to
result in successful recovery".

**In the SDK, almost everything becomes an execution error.** Raise `ToolError`, or let any
ordinary exception escape, and you get the same shape:

```python
from mcp.server.mcpserver.exceptions import ToolError

@mcp.tool(title="Sample CPU for a window")
def sample_window(seconds: int) -> str:
    """Sample CPU for `seconds` seconds, between 1 and 30."""
    if not 1 <= seconds <= 30:
        raise ToolError(
            f"seconds must be between 1 and 30, got {seconds}. "
            "Retry with a value in range."
        )
    ...
```

Calling it with `{"seconds": 500}` returns:

```json
{
  "content": [
    { "type": "text",
      "text": "Error executing tool sample_window: seconds must be between 1 and 30, got 500. Retry with a value in range." }
  ],
  "isError": true,
  "resultType": "complete"
}
```

An uncaught `ZeroDivisionError` produces the same shape, with
`Error executing tool <name>: division by zero` as the text. That is a reasonable default and
a poor message. Catch what you can predict and write the sentence yourself.

**To raise a real protocol error, raise `MCPError`.** It is the one exception the server does
not convert, and it travels as a JSON-RPC `error` object with the code you give it:

```python
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

raise MCPError(INVALID_PARAMS, "Unknown tool: nope")
```

The client receives `code=-32602`. Reach for this rarely. The cases the specification puts on
the protocol side are unknown tool, a malformed request, and a server error, and none of them
are things a well-written tool body decides.

**Two honest notes, both worth checking again when stable 2.0 ships.**

The first is that the pinned SDK does not put an unknown tool name on the protocol side. A
hand-written request naming a tool that does not exist gets:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [{ "type": "text", "text": "Unknown tool: find_procss" }],
    "isError": true,
    "resultType": "complete"
  }
}
```

The specification lists "unknown tool" as a protocol error. The cause is that the high-level
server catches every exception except `MCPError` and converts it, and the tool manager raises
an ordinary `ToolError` for a missing name. An unknown *method*, by contrast, does behave as
the specification says, from the very same server:

```json
{ "jsonrpc": "2.0", "id": 4,
  "error": { "code": -32601, "message": "Method not found", "data": "tools/nonexistent" } }
```

The second is that an argument violating your tool's `inputSchema` also comes back as
`isError: true`, carrying the validation message. Whether that is correct is genuinely
ambiguous in the specification: "input validation errors" appear on both the protocol-error
and the execution-error list, and SEP-1303 pushed them toward the execution side precisely so
a model can fix its own mistake. The distinction the specification does draw cleanly is
between a request that violates the shape of `CallToolRequest`, which is a protocol error, and
one that is well formed but semantically wrong for the tool, which is not. An argument of the
wrong JSON type sits on the line between those two, and the SDK has picked a side.

Neither note changes what you should write. Give the model a sentence it can act on, and let
the framework place it.

## 8. Annotations are hints, and only hints

There are five annotation fields, and the defaults are not what most people assume:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `title` | string | none | Display name, consulted after the tool's own `title` and before `name` |
| `readOnlyHint` | boolean | `false` | The tool does not modify its environment |
| `destructiveHint` | boolean | **`true`** | Updates are destructive. Meaningful only when `readOnlyHint` is `false` |
| `idempotentHint` | boolean | `false` | Repeat calls with the same arguments have no additional effect. Also meaningful only when `readOnlyHint` is `false` |
| `openWorldHint` | boolean | **`true`** | The tool touches an external entity, for example the open web |

Two of those default to the cautious answer. A tool that declares nothing is, as far as a
host is concerned, a destructive open-world tool. That is the correct default and it is also
a reason to declare, because "read-only" is a claim only you can make.

**One casing rule, stated once, because it is a constant source of bugs. The Python
attributes are snake_case; the wire is camelCase.** You write `read_only_hint=True` and the
server sends `"readOnlyHint": true`. The camelCase spellings are accepted as constructor
keyword arguments but they are not attribute names, so `annotations.readOnlyHint` will not
work in your tests and `annotations.read_only_hint` will. This is the same rule
[notation_guide.md](../../notation_guide.md) states for the whole protocol: camelCase on the
wire, snake_case in Python.

The system-information server declares one constant and reuses it, which is a pattern worth
copying because it makes an unannotated tool obvious in review:

```python
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
```

and it goes out as:

```json
{ "readOnlyHint": true, "destructiveHint": false,
  "idempotentHint": true, "openWorldHint": false }
```

Now the part that matters more than the fields. The schema comment says it plainly:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide
> a faithful description of tool behavior (including descriptive properties like `title`).
> Clients should never make tool use decisions based on `ToolAnnotations` received from
> untrusted servers.

And the tools page adds that clients **must** consider annotations untrusted unless they come
from a trusted server. Nothing in the protocol stops a tool that deletes your files from
declaring `readOnlyHint: true`. The enforcement in
[tools.py](../../code/05-first-server/src/system_info/tools.py) is not the annotation; it is
that the functions genuinely do not write anything. Annotations are a hint to a host about how
to present a tool, and a security control they are not.

Assert on them anyway, because the mistake they catch is your own:

```python
async def test_destructive_tool_is_marked_destructive():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        ann = tools["terminate_process"].annotations
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False
```

## 9. Naming, titles, and descriptions

The naming rules are all **should**, not **must**, which is worth knowing because it means you
will meet names that break them.

- Between 1 and 128 characters.
- Case-sensitive.
- Only uppercase and lowercase American Standard Code for Information Interchange (ASCII)
  letters, digits, underscore, hyphen, and dot.
- No spaces, no commas, no other special characters.
- Unique within a server.

`getUser`, `DATA_EXPORT_v2`, and `admin.tools.list` are the specification's own valid
examples. **A slash is not in the allowed set.** That matters beyond your own naming, because
when a host has to disambiguate two servers that both expose `search`, the separator has to be
a dot, `files.search`, never a slash. And the server's self-reported name is not guaranteed
unique across servers, so it **should not** be the disambiguation key. A careful host
qualifies only the contested name, and qualifies it on both sides, so a tool whose name is
unique across the connected servers keeps the bare form a model may already know.
[Post 11](../11-building-a-host/index.md) builds that catalog.

The SDK checks and warns rather than refusing. Registering `files/search` logs six warning
lines from `mcp.shared.tool_name_validation`, of which these are the substance:

```
Tool name validation warning for "files/search":
  - Tool name contains invalid characters: '/'
  - Allowed characters are: A-Z, a-z, 0-9, underscore (_), dash (-), and dot (.)
Tool registration will proceed, but this may cause compatibility issues.
```

The tool then appears in `tools/list` under the name you gave it, slash and all.

Because the character set is only a should, a name may legitimately fall outside it, which is
why the `Mcp-Name` routing header has a Base64 escape for names that are not header-safe.

On `title` against `name`: `name` is an identifier and should read like one. `title` is for
humans and can contain spaces and punctuation. The server here uses
`title="Get system snapshot"` with `name="get_system_info"`, and a host is free to show either.

On `description`: it is the field the model actually reads, and it is also, for the same
reason, an attack surface. Text you put in a description enters the model's context before
any tool is called, which is the mechanism behind tool poisoning and line jumping.
[Post 19](../19-security/index.md) covers what that means when the description is not yours.
For your own server, three habits pay: say what the tool does and when to use it rather than
how it is implemented, state constraints that are not expressible in the schema (a handle's
lifetime, a rate limit), and say what the tool does *not* do, because the failure mode you are
guarding against is a model reaching for the nearest plausible tool.

## 10. Deterministic ordering and caching

New in this revision, and easy to skip past:

> Servers **SHOULD** return tools in a deterministic order (i.e., the same ordering across
> requests when the underlying set of tools has not changed).

The reason is given in the same paragraph: deterministic ordering lets clients cache the tool
list reliably, and it "improves LLM prompt cache hit rates when tools are included in model
context". A large language model (LLM) prompt cache is keyed on a prefix of the tokens, and
tool definitions usually sit near the front of that prefix. Shuffle them between requests, by
iterating a set or by sorting on something that changes, and every request misses the cache.

Two `tools/list` calls against the system-information server return byte-identical results,
and the order is registration order:

```
byte-identical: True
order: ['get_system_info', 'find_process', 'terminate_process', 'watch_cpu']
```

That is a property of dictionary insertion order in Python rather than something the SDK
promises, so if you build your tool list from a set, a directory scan, or a database query,
sort it.

`tools/list` is also a cacheable method, which means its result **must** carry `ttlMs` and
`cacheScope`. This server declines to be cached:

```
ttlMs: 0   cacheScope: private
```

`tools/call` is not cacheable and carries neither. [Post 07](../07-resources-and-prompts/index.md)
covers the caching fields properly.

One rule that constrains what you can put in the list at all: the set of tools **must not**
vary per connection or as a side effect of another request. A `connect_database` tool that
makes `query` appear in later listings is no longer conformant. The list **may** vary by the
authorization presented on the request, so returning only the tools a caller's scopes permit
is fine. Filtering by credential is allowed; filtering by history is not.

---

## Common pitfalls

- **Returning a class whose attributes are set only in `__init__`.** The SDK reads class-body
  annotations, finds none, and publishes `outputSchema: null` with no warning at all. The tool
  then ships the object's `repr` as its text content. Use a dataclass, a `TypedDict`, or a
  Pydantic model, pass `structured_output=True` so the failure happens at registration, and
  assert that every tool publishes a schema.
- **Writing constraints in the docstring's `Args:` block and assuming the model sees them per
  parameter.** That text lands in the tool's `description`, not in the property. Bounds,
  enumerations, and examples belong in `Annotated[..., Field(...)]` where a model reads them
  next to the field they constrain.
- **Reading `annotations.readOnlyHint` in Python.** The attribute is `read_only_hint`. The
  camelCase form is a constructor alias and an attribute error. Nothing warns you, because the
  wire format really is camelCase and both spellings appear in the same documentation page.
- **Treating an annotation as a control.** `readOnlyHint: true` is a claim by the server about
  itself, and clients are required to treat it as untrusted. If a tool must not write, the
  guarantee has to be in the code, the credential, or the database role, not in a boolean.
- **Raising a JSON-RPC error when a tool fails.** The model never sees a protocol error as
  something to fix. Put the reason in `content` with `isError: true`, and name the constraint,
  the offending value, and the correction.
- **Expecting a top-level array from `-> list[str]`.** The SDK wraps it as
  `{"result": [...]}`. The protocol permits a top-level array, but you have to return a
  `CallToolResult` to send one, and then you have no `outputSchema`.
- **Building the tool list from a set or a directory scan.** The order will drift, clients will
  refetch, and upstream prompt caching will miss on every request. Sort it.

---

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The complete `Tool` object, `ToolAnnotations`
  and its defaults, the naming rules, deterministic ordering, the content block types,
  `structuredContent`, and the error split quoted throughout.
  <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Base protocol"* § JSON Schema usage, revision 2026-07-28. The 2020-12
  default dialect and the `$ref` resolution requirements in section 3.
- SEP-2106. The proposal that opened `inputSchema` to arbitrary 2020-12 keywords, added the
  `$ref` and composition-keyword safety rules, and loosened `structuredContent` from an object
  to any JSON value. Index at <https://modelcontextprotocol.io/seps>.
- SEP-986, *"Tool naming guidance"*. The character set the SDK warns about, and the reasoning
  for making it a should rather than a must.
- SEP-1303, *"Input validation errors as tool execution errors"*. The proposal behind the
  ambiguity section 7 flags.
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026).
  <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
- MCP Python SDK, `mcp==2.0.0b2`. Every capture in this post came from this version, driving
  [code/05-first-server/](../../code/05-first-server/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 07 — Resources and prompts: the primitives that are not tools](../07-resources-and-prompts/index.md)**:
  when the thing you are about to build should not be a tool at all, plus the caching fields
  section 10 deferred.
- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**:
  the mechanism that kept `approval` out of the published input schema in section 2.
- **[Post 12 — Testing and debugging MCP](../12-testing-and-debugging/index.md)**: the
  in-memory client pattern behind every assertion in this post, and how to catch a silent
  schema failure before a user does.
