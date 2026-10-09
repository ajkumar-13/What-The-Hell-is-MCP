# 06 - Tools in depth: schemas, structured output, and annotations

> **TL;DR.** A tool's schema is the only thing a model reads before deciding to call it, so schema design, not implementation, decides whether a tool gets called correctly. This post reads the complete `Tool` object of Model Context Protocol (MCP) revision 2026-07-28 field by field, then covers input schemas, `outputSchema` and `structuredContent`, the five content block types, annotations, naming and ordering, with every schema and result printed by a script in `snippets/`. It gives real space to the return annotation that silently publishes no output schema at all, because that failure is invisible until a client complains. And it settles the error question: a tool that runs and fails returns a successful response carrying `isError: true`, and a JSON-RPC (JavaScript Object Notation Remote Procedure Call) error is kept for a request the server cannot act on at all.
>
> **Prerequisites:** [Post 05](../05-first-server/index.md).
> **Safe to skip?** Skip it if the reader can already name the two required fields of a `Tool`, predict the `outputSchema` the Python software development kit (SDK) publishes for a given return annotation, and say which failures travel as a JSON-RPC `error` and which as a result with `isError: true`.
>
> **After reading, you will be able to:**
>
> - Read and write every field of a Tool object and name the two fields the specification requires.
> - Design an input schema with per-property descriptions, bounds and enumerations that a model fills in correctly on its first attempt.
> - Publish an outputSchema deliberately from a return annotation, and prove with an in-memory test that every tool publishes one.
> - Route any tool failure to a JSON-RPC error object or to a result with isError true, and write the message a model needs to retry.

![A tools/call request on the left and its result on the right, with every field labeled: method, name, arguments and the metadata object on the request side, and resultType, content, structuredContent and isError on the result side.](diagrams/01-anatomy-of-a-tool-call.svg)

*The parts of one `tools/call` to the first server's `find_process` and of its result. Section 4 prints a real exchange.*

---

## 1. The question: why does the schema, not the code, decide the call?

A server author meets this symptom soon after shipping. The tool works. A test calls it and it returns exactly what it should. Then the server is connected to a host, someone asks the obvious question, and the model either does not call the tool at all or calls it with arguments that make no sense.

Nothing is broken. The model never saw the Python. It saw four things: a name, a description, an input schema, and, if one was published, an output schema. That is the entire surface. The specification's Tools page is blunt about the status of the description: it "can be thought of like a 'hint' to the model."

So the work in this post is interface design, and the interface is JSON Schema. [Post 05](../05-first-server/index.md) showed that type hints become a schema and left it there. This post is about what that schema should contain, what the result should carry back, and the handful of places where the Python SDK does something surprising without saying so.

The tools come from the server in [code/05-first-server/](../../code/05-first-server/), which reports central processing unit (CPU), memory, and disk usage for the machine it runs on. Every schema, result, log line and error quoted below that the specification did not print is printed by one of eight scripts in `snippets/`, run through the series root environment, which pins `mcp==2.3.0` (the SDK release that implements revision 2026-07-28) on Python 3.12.12. Where a capture needs a tool the project does not have, the script registers it on a throwaway server and the post prints that code alongside; section 11 lists the commands.

## 2. The complete `Tool` object, field by field

Wire format first. A `tools/list` result carries an array of these, condensed here from the specification's TypeScript schema with the members `Tool` inherits written out:

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
| `_meta` | No | Metadata; a key's optional prefix should be in reverse Domain Name System (DNS) form. |

A display name can come from three places, and `Tool` is the one type whose precedence is unusual: `title` first, then `annotations.title`, then `name`. Nothing else in the schema has an `annotations.title` at all, and the specification calls `Tool` out as the exception.

Here is one as `snippets/tool_object.py` prints it from the server's `tools/list`, in the wire spelling. This is the `terminate_process` tool from [interactive.py](../../code/05-first-server/src/system_info/interactive.py):

```json
{
  "name": "terminate_process",
  "title": "Terminate a process",
  "description": "Terminate a running process, after the user confirms.\n\n    Args:\n        pid: The process id to terminate.\n    ",
  "inputSchema": {
    "type": "object",
    "properties": {
      "pid": {"title": "Pid", "type": "integer"}
    },
    "required": ["pid"],
    "title": "terminate_processArguments"
  },
  "outputSchema": {
    "properties": {
      "result": {"title": "Result", "type": "string"}
    },
    "required": ["result"],
    "title": "terminate_processOutput",
    "type": "object"
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

The description keeps the docstring's indentation because Python 3.12 does; post 05 notes that 3.13 strips it. Now the decorator and signature that produced the entry, from `src/system_info/interactive.py`:

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
def terminate_process(
    pid: int,
    approval: Annotated[
        ElicitationResult[TerminateConfirmation],
        Resolve(_confirm_terminate),
    ],
) -> str:
    """Terminate a running process, after the user confirms.

    Args:
        pid: The process id to terminate.
    """
```

Read the input schema against the Python. The function takes `pid` and `approval`, and only `pid` reached the wire. A `Resolve` parameter is excluded from the published schema on purpose, so the model cannot supply its own approval. [Post 08](../08-elicitation-and-mrtr/index.md) is about that mechanism; here it is a reminder that the signature and the schema are not the same object, and the schema is the one that matters. The `-> str` return became an `outputSchema` that wraps a string in a `result` field, a rule section 4 tabulates.

The same script prints the parameters the decorator accepts: `name, title, description, annotations, icons, meta, structured_output`. `structured_output` is the one this post leans on, in section 5.

## 3. Input schemas a model gets right

Look again at what `find_process` publishes, as `snippets/input_schema.py` prints it:

```json
{
  "type": "object",
  "properties": {
    "name": {"title": "Name", "type": "string"},
    "limit": {"default": 25, "title": "Limit", "type": "integer"}
  },
  "required": ["name"],
  "title": "find_processArguments"
}
```

Notice what is missing. The docstring says `limit` should be "between 1 and 50", and that sentence went into the tool's `description`, not into the `limit` property. The SDK does not parse a Google-style `Args:` block into per-property descriptions. A model reading only the properties sees an integer with no bounds and no explanation.

The fix is `Annotated` with a Pydantic `Field`. The same script registers the tool again on a throwaway server, redesigned, with `Annotated` and `Literal` imported from `typing` and `Field` from `pydantic`:

```python
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
      "title": "Name",
      "type": "string"
    },
    "limit": {
      "default": 25,
      "description": "Maximum processes to return.",
      "maximum": 50,
      "minimum": 1,
      "title": "Limit",
      "type": "integer"
    },
    "sort_by": {
      "default": "memory",
      "description": "Sort key for the returned list.",
      "enum": ["memory", "pid", "name"],
      "title": "Sort By",
      "type": "string"
    }
  },
  "required": ["name"],
  "title": "find_processArguments"
}
```

Three things changed and all three are load-bearing. Every property now carries a `description`, so the model can read the constraint where it is looking. `limit` carries `minimum` and `maximum`, so a value out of range is a fact about the schema rather than a surprise inside the function. And `sort_by` is an `enum`, which is the single highest-value schema move there is: a closed set of strings leaves the model nothing to invent, whereas free text invites invention.

The bounds are also checked before the function runs. Calling the redesigned tool with `{"name": "python", "limit": 500}` never reaches the body; the script prints a result with `isError: true` whose text names the rule:

```
Error executing tool find_process: 1 validation error for find_processArguments
limit
  Input should be less than or equal to 50 [type=less_than_equal, input_value=500, input_type=int]
```

A `sort_by` of `"cpu"` gets `Input should be 'memory', 'pid' or 'name'` the same way. Both messages tell the model what to send next, which is the subject of section 7.

Four more rules worth having.

**The root must be an object.** `type: "object"` at the root is required. Beyond that, Specification Enhancement Proposal 2106 (SEP-2106) opened the schema to any JSON Schema 2020-12 keyword, including `oneOf`, `anyOf`, `allOf`, `not`, `if`/`then`/`else`, `$ref`, `$defs`, and `$anchor`. The Base protocol page makes 2020-12 the dialect whenever a schema declares no `$schema`.

**For a tool with no parameters, the strict form is the recommended one.** The Tools page names `{"type": "object", "additionalProperties": false}` as the recommended shape and `{"type": "object"}` as the permissive alternative. The SDK emits the permissive kind: `get_system_info`, which takes nothing, publishes `{"type": "object", "properties": {}, "title": "get_system_infoArguments"}`. That is legal, and none of the decorator's parameters changes it.

**A `$ref` is never fetched over the network.** Implementations **must not** automatically dereference a `$ref` that resolves to a network Uniform Resource Identifier (URI). A schema that fails to validate because of an unresolved external `$ref` **should** be rejected rather than quietly treated as permissive. A shared schema hosted somewhere and pointed at is therefore a schema nobody will read. A local reference is fine: the project's `find_process` output schema keeps `ProcessInfo` under `$defs` and points at it with `"$ref": "#/$defs/ProcessInfo"`.

**Composition keywords are a denial-of-service surface.** Implementations **should** bound schema depth, subschema count, or validation time. That applies to whoever validates somebody else's schema, which is the client's job in [post 10](../10-mcp-client/index.md).

## 4. `outputSchema` and `structuredContent`

A tool result carries two representations of the same answer. The specification's schema declares the result as:

```typescript
export interface CallToolResult extends Result {
  content: ContentBlock[];        // REQUIRED (may be empty)
  structuredContent?: unknown;
  isError?: boolean;
}
```

`content` is prose and media for the model to read. `structuredContent` is data for a program to use. The Tools page is explicit that `structuredContent` can be **any JSON value**, object, array, string, number, boolean, or null, loosened by SEP-2106 from an object-only rule in earlier revisions. It also asks that a tool returning structured content **should** still put the serialized JSON in a text block, for clients that do not read the structured field. On the wire the field is `structuredContent`; the Python SDK exposes it as `structured_content`.

Here is a real `find_process` result, read straight off the server's standard output by `snippets/wire_by_hand.py`, with the text block shortened by the script:

```json
{
  "content": [
    {
      "text": "{\n  \"query\": \"python\",\n  \"total_matches\"...",
      "type": "text"
    }
  ],
  "isError": false,
  "resultType": "complete",
  "structuredContent": {
    "query": "python",
    "total_matches": 4,
    "returned": 1,
    "processes": [
      {
        "pid": 7080,
        "name": "python.exe",
        "memory_mb": 66.7
      }
    ]
  },
  "_meta": {
    "io.modelcontextprotocol/serverInfo": {
      "name": "system-info",
      "version": ""
    }
  }
}
```

The numbers are one reading of the author's machine. Before shortening the text block, the script checks it and prints `True`: parsed, it equals the structured content, a duplication the SDK does without being asked. The `_meta` entry is the server's name and version, which every result of this SDK carries and which later listings in this post leave out; the version is empty because the project does not set one.

![Two columns showing the same tool returning a formatted string on the left and a dataclass on the right, with the published output schema and the data the model receives under each.](diagrams/02-structured-vs-unstructured.svg)

*The return annotation is the only difference. Everything below it follows from that one line.*

In the SDK, the return annotation drives `outputSchema`. `snippets/output_schema.py` registers one tool per kind of annotation and prints what each publishes:

| Return annotation | Resulting `outputSchema` |
|---|---|
| Pydantic `BaseModel` subclass, `TypedDict`, dataclass | The type's own schema |
| Any other class **with class-body annotations** | The type's own schema, built through `get_type_hints()` |
| `str`, `None` and the other primitives | Wrapped as `{"result": ...}` |
| `dict[str, X]` | A schema of its own, no wrapping |
| `list[...]`, `X \| None`, `dict` with non-string keys | Wrapped as `{"result": ...}` |
| A content block such as `TextContent`, or the `Image` helper | None |
| `CallToolResult` | None; the tool owns the result |
| No annotation | None |

The content-block row is recent: since SDK release 2.1.0 a tool that returns content blocks publishes no schema unless it passes `structured_output=True`, because a block is something for the model to read rather than data for a program.

The wrapping rows surprise people. A tool annotated `-> list[str]` does not produce a top-level JSON array; the script's `letters` tool returns `["a", "b"]` and the call carries `{"result": ["a", "b"]}`. The protocol allows a top-level array, and the Tools page prints one as an example, but reaching it through the high-level API means returning a `CallToolResult` built by hand, as the same script does:

```python
@mcp.tool()
def raw_array() -> CallToolResult:
    """Top-level array structuredContent, built by hand."""
    return CallToolResult(
        content=[TextContent(type="text", text='[{"id": "1"}, {"id": "2"}]')],
        structured_content=[{"id": "1"}, {"id": "2"}],
    )
```

The script prints `structuredContent: [{"id": "1"}, {"id": "2"}]` and `outputSchema: null`: a bare `-> CallToolResult` trades the schema for the shape. Annotating it as `Annotated[CallToolResult, Reading]` keeps publishing the schema of `Reading`, and section 12 shows the server then holding the hand-built result to it.

**Publishing a schema is a promise, and somebody checks.** If `outputSchema` is present, servers **must** return conforming structured results and clients **should** validate them. The Python client does validate, automatically, on every result that is not marked `isError`; section 12 shows what that check prints when it fails.

## 5. The return type that publishes nothing, silently

This is the trap the whole post builds toward, and nothing warns about it.

The SDK builds an output schema from an ordinary class by calling `get_type_hints()` on it. A class whose attributes are only ever assigned inside `__init__`, with no annotations in the class body, has no type hints. `snippets/silent_schema.py` prints the branch of the installed SDK that handles the case, and its last comment says what happens next:

```
# Case 4: Other class types (dataclasses, regular classes with annotations)
else:
    type_hints = get_type_hints(type_annotation)
    if type_hints:
        # Classes with type hints can be converted to Pydantic models
        model = _create_model_from_class(type_annotation, type_hints)
    # Classes without type hints are not serializable - model remains None
```

`model remains None`. No exception, no warning, no log line. Here is the demonstration from the same script, and the two classes differ only in where the attribute names are written:

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
def silent() -> Snapshot:
    return Snapshot(cpu_percent=12.5, memory_percent=40.0)

@mcp.tool()
def loud() -> AnnotatedSnapshot:
    return AnnotatedSnapshot(cpu_percent=12.5, memory_percent=40.0)
```

Listing that server prints this:

```
silent     outputSchema = null
loud       outputSchema = {"properties": {"cpu_percent": {"title": "Cpu Percent", "type": "number"}, "memory_percent": {"title": "Memory Percent", "type": "number"}}, "required": ["cpu_percent", "memory_percent"], "title": "AnnotatedSnapshot", "type": "object"}
```

The annotations on the `__init__` parameters were not enough. They are annotations on a function, not on the class.

The tool still registers. It still runs. And calling it returns this:

```json
{
  "content": [
    {
      "type": "text",
      "text": "\"<__main__.Snapshot object at 0x000001956AFD8080>\""
    }
  ],
  "isError": false,
  "resultType": "complete"
}
```

A memory address, marked as a success; the address differs on every run. That is what the model gets. The script counts the SDK's log records at INFO or above across registering, listing and calling, and prints `0`. Nothing looked wrong on the author's machine, and the only person who finds out is the user watching a model try to reason about a pointer.

There are two ways to make the failure loud, and a server wants both.

**Ask the SDK to insist.** `structured_output=True` converts the silence into a registration error, which means an import-time crash rather than a runtime surprise. The script registers the same tool with the flag on a second server and prints:

```
InvalidSignature: Function silent: return type <class '__main__.Snapshot'> is not serializable for structured output
```

The flag also has an inverse. `structured_output=False` publishes no schema whatever the annotation, which `snippets/output_schema.py` confirms for a dataclass; it is the honest way to say that a tool returns prose on purpose.

**Assert on it in a test.** One test catches every future instance of the bug, and it is why the project's suite opens with it. From `tests/test_server.py` in [code/05-first-server/](../../code/05-first-server/):

```python
async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"
```

`Client(mcp)` connects to the server object in memory, with no subprocess and no socket, so this runs in milliseconds and still goes through the real protocol machinery. `snippets/silent_schema.py` ends by running the same loop against its own server and prints `AssertionError: silent has no output schema`; against the project the assertion passes, as part of the 19 tests `uv run --extra dev pytest` runs inside `code/05-first-server/`. [Post 12](../12-testing-and-debugging/index.md) explains the pattern properly.

The table in section 4 lists the other annotations that publish no schema and are easy to reach by accident: `-> Image`, any content block, and any function nobody annotated. None of them is a mistake in itself. Each is worth being deliberate about, and a project that asserts as above has to mark such a tool as an exception on purpose.

## 6. Content blocks

`content` is an array, and there are exactly five block types. `snippets/content_blocks.py` prints the union as the installed `mcp_types` package defines it: `ContentBlock = TextContent | ImageContent | AudioContent | ResourceLink | EmbeddedResource`.

**Text.** `type` and `text` are required. A `-> str` return becomes one, and so does the serialized JSON the SDK writes beside a structured return value.

**Image.** `type`, `data` (Base64), and `mimeType` are required. The SDK's `Image` helper produces one; for a one-pixel Portable Network Graphics (PNG) file the script prints, with `data` shortened:

```json
{"type": "image", "data": "iVBORw0KGgoAAAANSUhEUgAA...", "mimeType": "image/png"}
```

**Audio.** The same three fields, with an audio media type.

**Resource link.** A pointer instead of a payload. It carries the full resource field set, so `uri` and `name` are required and `title`, `description`, `mimeType`, `size`, and `icons` are available. The Tools page warns that resource links returned by a tool are not guaranteed to appear in `resources/list`.

**Embedded resource.** `type: "resource"` wrapping a nested `resource` with either text or a Base64 blob, plus its URI and media type. A server that uses these **should** implement the resources capability, which is [post 07](../07-resources-and-prompts/index.md).

All five accept optional `annotations` with `audience`, `priority`, and `lastModified`, which is how a server tells a host that a block is meant for the user's eyes rather than the model's context.

Mixing them is the point. A tool that produces a large result should return a short summary the model can read and a link to the bulk, rather than 12,000 rows of context:

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
                mime_type="text/csv",
            ),
        ],
        structured_content={"rows": 12000},
    )
```

The script calls it and prints the second block as it travels, `{"name": "report.csv", "uri": "file:///tmp/report.csv", "description": "Full result set, 12000 rows", "mimeType": "text/csv", "type": "resource_link"}`, with `"structuredContent": {"rows": 12000}` beside it.

One trap for readers coming from a model provider's application programming interface (API): `tool_use` and `tool_result` are **not** MCP content blocks, and the union above has neither. In the specification they belong to the sampling message type only, and sampling is deprecated in this revision.

## 7. Errors that teach the model to retry

[Post 03](../03-wire-protocol/index.md) drew the taxonomy: a protocol error is a JSON-RPC `error` object, a tool execution error is a successful response with `isError: true`, and MCP defines no error code for a tool that ran and failed.

![One tools/call splitting into two paths: a protocol error returning a JSON-RPC error object that the client handles, which does not always arrive with status 200 because several codes are pinned to 400 or 404, and an execution error returning a successful HTTP 200 result with isError true that is fed back to the model so it can retry.](diagrams/03-error-paths.svg)

*Left, the server could not act on the request. Right, the request was fine and the work did not succeed. Over Hypertext Transfer Protocol (HTTP), the Base protocol page pins some protocol errors to status 400 or 404.*

This section is about what that means for the person writing the tool.

**Start from what the model will read.** The content block of a failed tool call is not a log line. It is an instruction to a reader who is about to try again. Compare:

```
Error: invalid input
```

with

```
seconds must be between 1 and 30, got 500. Retry with a value in range.
```

The second names the constraint, names the offending value, and says what to do. That is a tool call that can succeed on the second attempt; the first gives the model nothing to change. The Tools page's reasoning is that clients **should** hand execution errors to the model to enable self-correction, and **may** hand it protocol errors, "though these are less likely to result in successful recovery".

**In the SDK, raise `ToolError` for a failure the tool anticipates.** `snippets/error_paths.py` registers this tool on a throwaway server:

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
```

Calling it with `{"seconds": 500}` returns:

```json
{
  "content": [
    {
      "type": "text",
      "text": "Error executing tool sample_window: seconds must be between 1 and 30, got 500. Retry with a value in range."
    }
  ],
  "isError": true,
  "resultType": "complete"
}
```

and the server logs one line at INFO, `Tool 'sample_window' failed: ...` with the same text.

**Any other exception is a crash, and its message stays on the server.** The same script registers `average(total: int, count: int) -> float`, which divides without a guard, and calls it with a zero count. The model receives `isError: true` and nothing more than:

```
Error executing tool average
```

while the server logs `Tool 'average' raised an unexpected exception` at ERROR, with a traceback ending in `ZeroDivisionError('division by zero')`. Keeping an exception's text off the wire is a sound default, since it can carry paths and data the model should not see, but it also means the model learns nothing it can correct. Catch what can be predicted and raise `ToolError` with the sentence written out.

**To raise a real protocol error, raise `MCPError`.** The tool layer re-raises it untouched, and it travels as a JSON-RPC `error` object with the code it was given:

```python
@mcp.tool()
def strict() -> str:
    """Refuse on the protocol side."""
    from mcp.shared.exceptions import MCPError
    from mcp_types import INVALID_PARAMS

    raise MCPError(INVALID_PARAMS, "Unknown tool: nope")
```

The client raises `MCPError: code=-32602 message='Unknown tool: nope'`, which is `-32602` (`Invalid params`). Reach for this rarely. The cases the Tools page puts on the protocol side are an unknown tool, a malformed request, and a server error, and none of them are things a well-written tool body decides.

The project's own server shows where the SDK places the rest. `snippets/wire_by_hand.py` starts it the way a host does and writes requests to its standard input by hand. An unknown *method* gets `-32601` (`Method not found`), as the specification says:

```json
{"jsonrpc":"2.0","id":5,"error":{"code":-32601,"message":"Method not found","data":"tools/nonexistent"}}
```

An `arguments` value that is a JSON array rather than an object breaks the shape of `CallToolRequest` itself, and gets `-32602`:

```json
{"jsonrpc":"2.0","id":7,"error":{"code":-32602,"message":"Invalid request parameters","data":""}}
```

> **A note on the SDK.** The Tools page lists an unknown tool among the protocol errors and prints its example as `-32602`. `mcp==2.3.0` answers a misspelled tool name with a successful response instead, because its tool manager raises `ToolError` for a missing name:
>
> ```json
> {"jsonrpc":"2.0","id":4,"result":{"content":[{"text":"Unknown tool: find_procss","type":"text"}],"isError":true,"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"system-info","version":""}}}}
> ```
>
> An argument of the wrong JSON type, `"limit": "many"`, also comes back as `isError: true`, carrying the validation message `Input should be a valid integer, unable to parse string as an integer`. For that case the specification is ambiguous rather than contradicted: "input validation errors" appear on the Tools page's list of execution errors, SEP-1303 moved them there precisely so a model can fix its own mistake, and the schema's comment on `-32602` still lists invalid tool arguments. The line the specification does draw cleanly is between a request that violates the shape of `CallToolRequest`, a protocol error, and one that is well formed but wrong for the tool, which is not.

Neither case changes what a tool author should write. Give the model a sentence it can act on, and let the framework place it.

## 8. Annotations are hints, and only hints

There are five annotation fields, and the defaults matter as much as the fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `title` | string | none | Display name, consulted after the tool's own `title` and before `name` |
| `readOnlyHint` | boolean | `false` | The tool does not modify its environment |
| `destructiveHint` | boolean | **`true`** | Updates may be destructive rather than only additive. Meaningful only when `readOnlyHint` is `false` |
| `idempotentHint` | boolean | `false` | Repeat calls with the same arguments have no additional effect. Also meaningful only when `readOnlyHint` is `false` |
| `openWorldHint` | boolean | **`true`** | The tool may interact with an open world of external entities, for example the web |

Two of those default to the cautious answer. A tool that declares nothing is, as far as a host is concerned, a destructive open-world tool. That is the correct default, and it is also a reason to declare, because "read-only" is a claim only the server's author can make. The Python model leaves an undeclared hint as `None` and sends nothing for it, so a host applies the specification's defaults above.

The system-information server declares one constant in [tools.py](../../code/05-first-server/src/system_info/tools.py) and reuses it, which is a pattern worth copying because it makes an unannotated tool obvious in review:

```python
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
```

and `snippets/tool_object.py` prints it in its wire spelling as:

```json
{"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false}
```

The Python attributes are snake_case and the wire is camelCase, the rule [notation_guide.md](../../notation_guide.md) states for the whole protocol; section 12 shows the one place it bites.

Now the part that matters more than the fields. The schema comment says it plainly:

> NOTE: all properties in `ToolAnnotations` are **hints**. They are not guaranteed to provide a faithful description of tool behavior (including descriptive properties like `title`). Clients should never make tool use decisions based on `ToolAnnotations` received from untrusted servers.

And the Tools page adds that clients **must** consider annotations untrusted unless they come from a trusted server. Nothing in the protocol stops a tool that deletes files from declaring `readOnlyHint: true`. The guarantee in `tools.py` is not the annotation; it is that the functions genuinely do not write anything. Annotations help a host present a tool and decide how loudly to ask; the same page's advice that clients **should** prompt the user before a sensitive operation is, in this series, the host's permission gate, and it is never a control a server's own boolean can satisfy.

Assert on them anyway, because the mistake they catch is the author's own. From `tests/test_server.py`:

```python
async def test_destructive_tool_is_marked_destructive():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        ann = tools["terminate_process"].annotations
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False
```

## 9. Naming, titles, and descriptions

The naming rules are all **should**, not **must**, which is worth knowing because it means real servers ship names that break them.

- Between 1 and 128 characters.
- Case-sensitive.
- Only uppercase and lowercase American Standard Code for Information Interchange (ASCII) letters, digits, underscore, hyphen, and dot.
- No spaces, no commas, no other special characters.
- Unique within a server.

`getUser`, `DATA_EXPORT_v2`, and `admin.tools.list` are the specification's own valid examples. **A slash is not in the allowed set.** That matters beyond a server's own naming. When a host has to disambiguate two servers that both expose `search`, the Tools page asks for a strategy such as a server prefix, and the qualified name has to stay inside the set: `files.search` with a dot, or `files_search` where a model provider forbids the dot, never `files/search`. And a server's self-reported name is not guaranteed unique across servers, so it **should not** be the disambiguation key; the prefix comes from the host's own configuration. [Post 11](../11-building-a-host/index.md) builds that catalog, qualifying only the names that collide.

The SDK checks and warns rather than refusing, a case section 12 prints. Because the character set is only a should, a name may legitimately fall outside it, which is why the `Mcp-Name` routing header of Streamable HTTP has a Base64 escape for names that are not header-safe.

On `title` against `name`: `name` is an identifier and should read like one. `title` is for humans and can contain spaces and punctuation. The server here uses `title="Get system snapshot"` with the name `get_system_info`, and a host is free to show either.

On `description`: it is the field the model actually reads, and it is also, for the same reason, an attack surface. Text in a description enters the model's context before any tool is called, which is the mechanism behind tool poisoning and line jumping. [Post 19](../19-security/index.md) covers what that means when the description belongs to someone else. For a server's own tools, three habits pay: say what the tool does and when to use it rather than how it is implemented, state constraints that are not expressible in the schema (a handle's lifetime, a rate limit), and say what the tool does *not* do, because the failure being guarded against is a model reaching for the nearest plausible tool.

## 10. Deterministic ordering and caching

New in this revision, and easy to skip past, from the Tools page:

> Servers **SHOULD** return tools in a deterministic order (i.e., the same ordering across requests when the underlying set of tools has not changed).

The reason is given in the same paragraph: deterministic ordering lets clients cache the tool list reliably, and it "improves LLM prompt cache hit rates when tools are included in model context". A large language model (LLM) provider that caches prompts by prefix, with the tool definitions near the front of that prefix, loses the cache whenever the definitions move. Shuffle them between requests, by iterating a set or by sorting on something that changes, and every request misses it.

Two `tools/list` calls against the system-information server, compared by `snippets/wire_by_hand.py` on the raw bytes after the request id, return identical results, and the order is registration order:

```
results byte-identical after the id: True
order: ['get_system_info', 'find_process', 'terminate_process', 'watch_cpu']
```

That order comes from the SDK keeping its tools in a Python dictionary, which preserves insertion order; it is not something the SDK documents as a promise. A server that builds its tool list from a set, a directory scan, or a database query should sort it.

`tools/list` is also a cacheable method, which means its result **must** carry `ttlMs` and `cacheScope`. This server declines to be cached, as the same script prints:

```
ttlMs: 0   cacheScope: private
```

`tools/call` is not cacheable, and the `find_process` result in section 4 carries neither field. [Post 07](../07-resources-and-prompts/index.md) covers the caching fields properly.

One rule constrains what the list may contain at all: the set of tools **must not** vary per connection or as a side effect of another request. A `connect_database` tool that makes `query` appear in later listings is no longer conformant. The list **may** vary by the authorization presented on the request, so returning only the tools a caller's scopes permit is fine. Filtering by credential is allowed; filtering by history is not.

## 11. Make it run: eight scripts

The project is unchanged by this post; post 05's commands run it and its tests. Every schema, result and log line above that the specification did not print comes from one of eight scripts in `snippets/`. Each runs from the series root with no arguments, in a second or two, through the root environment that pins `mcp[cli]==2.3.0` and `psutil`. Seven build their servers in memory; two of those, `tool_object.py` and `input_schema.py`, also import the project from `code/05-first-server/src`. `wire_by_hand.py` starts the project as a child process and closes its standard input so that it exits. None needs a host or any network.

```text
uv run python posts\06-tools-in-depth\snippets\tool_object.py
uv run python posts\06-tools-in-depth\snippets\input_schema.py
uv run python posts\06-tools-in-depth\snippets\output_schema.py
uv run python posts\06-tools-in-depth\snippets\silent_schema.py
uv run python posts\06-tools-in-depth\snippets\content_blocks.py
uv run python posts\06-tools-in-depth\snippets\error_paths.py
uv run python posts\06-tools-in-depth\snippets\wire_by_hand.py
uv run python posts\06-tools-in-depth\snippets\naming.py
```

| Section | Script | What it prints |
|---|---|---|
| 2, 8, 12 | `tool_object.py` | the `terminate_process` entry, the decorator's parameters, the bare decorator's error, the annotations in both spellings |
| 3 | `input_schema.py` | the project's input schemas, the redesigned one, and two calls that break it |
| 4, 12 | `output_schema.py` | the return annotation table, a hand-built array, and both schema checks |
| 5 | `silent_schema.py` | the SDK's branch, `silent` against `loud`, the registration error, the failing assertion |
| 6 | `content_blocks.py` | the content block union, an image block, and a mixed result |
| 7 | `error_paths.py` | `ToolError`, a crash, and `MCPError`, with the server's log lines |
| 4, 7, 10 | `wire_by_hand.py` | the first server over standard input and output: ordering, caching, one result, four failures |
| 12 | `naming.py` | the warnings for `files/search` and the name the server publishes |

The project's suite, run inside `code/05-first-server/` with `uv run --extra dev pytest`, passes its 19 tests, including the two quoted in sections 5 and 8.

## 12. What can go wrong?

Four mistakes come up once a server has more than one tool, and the scripts print each of them.

**Writing `@mcp.tool` without parentheses.** The decorator must be called. `snippets/tool_object.py` prints what the bare form raises at import:

```
TypeError: The @tool decorator was used incorrectly. Did you forget to call it? Use @tool() instead of @tool
```

**Reading an annotation by its wire name in Python.** The camelCase spelling is accepted as a constructor keyword, so `ToolAnnotations(readOnlyHint=True)` works, but it is not an attribute. The same script prints:

```
annotations.read_only_hint -> True
annotations.readOnlyHint -> AttributeError: 'ToolAnnotations' object has no attribute 'readOnlyHint'
```

The trap is that both spellings appear on the same documentation pages, and a test that reaches for the wire name fails for a reason unrelated to the server.

**Returning data that its published schema does not describe.** Two checks catch it, on different sides. A tool annotated `Annotated[CallToolResult, Reading]` whose hand-built `structured_content` leaves out the required `units` field is caught by the server: `snippets/output_schema.py` prints `isError: true` with only `Error executing tool reading`, and the server's log line `Tool 'reading' raised an unexpected exception` at ERROR. When the published schema itself no longer fits the data, for instance a schema the client cached from an earlier listing, the server cannot know, and the client's check fails instead. The script corrupts the published schema of a conforming tool to require `units`, and the client raises:

```
RuntimeError: Invalid structured content returned by tool celsius: 'units' is a required property
```

That error surfaces in the host, not in the server's logs, which is exactly the kind of bug that gets reported to the server's author as "the server is broken" with no detail.

**Putting a slash in a tool name.** Registering `files/search` logs six warning lines from `mcp.shared.tool_name_validation`; `snippets/naming.py` prints all six, and these four carry the substance:

```
WARNING: Tool name validation warning for "files/search":
WARNING:   - Tool name contains invalid characters: '/'
WARNING:   - Allowed characters are: A-Z, a-z, 0-9, underscore (_), dash (-), and dot (.)
WARNING: Tool registration will proceed, but this may cause compatibility issues.
```

The tool then appears in `tools/list` as `['files/search']`, slash and all, so the warning on the server's standard error is the only sign.

## Common pitfalls

1. **Returning a class whose attributes are set only in `__init__`.** The SDK publishes `outputSchema: null` without a warning and ships the object's `repr` as text; use a dataclass, a `TypedDict`, or a Pydantic model, pass `structured_output=True`, and assert that every tool publishes a schema.
2. **Writing constraints only in the docstring's `Args:` block.** That text lands in the tool's `description`, not in the property; bounds, enumerations, and examples belong in `Annotated[..., Field(...)]`, next to the field they constrain.
3. **Treating an annotation as a control.** `readOnlyHint: true` is the server's claim about itself, and clients must treat it as untrusted; a tool that must not write needs that guarantee in its code, its credential, or its database role.
4. **Letting a tool's failure become a crash or a protocol error.** An unexpected exception reaches the model as `Error executing tool <name>` and an `MCPError` reaches only the client; raise `ToolError` with the constraint, the offending value, and the correction.
5. **Expecting a top-level array from `-> list[str]`.** The SDK wraps it as `{"result": [...]}`; a top-level array needs a hand-built `CallToolResult`, which publishes no `outputSchema` unless it is annotated with a type.
6. **Building the tool list from a set or a directory scan.** The order drifts, clients refetch, and upstream prompt caching misses on every request; sort it.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28. The complete `Tool` object, `ToolAnnotations` and its defaults, the naming rules, deterministic ordering, the content block types, `structuredContent`, and the error split quoted throughout. <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- Specification, *"Base protocol"*, revision 2026-07-28, the JSON Schema usage section: 2020-12 as the default dialect, and the error codes with their HTTP statuses. <https://modelcontextprotocol.io/specification/2026-07-28/basic>
- SEP-2106, *"Arbitrary JSON Schema 2020-12 keywords in `inputSchema`"*. The proposal that opened `inputSchema` to every 2020-12 keyword, added the `$ref` and composition-keyword safety rules, and loosened `structuredContent` from an object to any JSON value. Index at <https://modelcontextprotocol.io/seps>.
- SEP-986, *"Tool naming guidance"*. The character set the SDK warns about, and why it is a should rather than a must.
- SEP-1303, *"Input validation errors as tool execution errors"*. The proposal behind the ambiguity the note in section 7 flags.
- Hungerford, O., Morrow, S., and Chang, L., *"Tool Annotations as Risk Vocabulary: What Hints Can and Can't Do"* (2026-03-16), Model Context Protocol blog. What annotation hints are for, and what they cannot guarantee. <https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/>
- MCP Python SDK, release 2.3.0. `@mcp.tool()`, `structured_output`, `ToolError`, `MCPError`, and the in-memory `Client`. <https://github.com/modelcontextprotocol/python-sdk>
- [code/05-first-server/](../../code/05-first-server/). The project whose tools, annotations and tests this post reads.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 07 - Resources and prompts: the primitives that are not tools](../07-resources-and-prompts/index.md):** when the thing about to be built should not be a tool at all, plus the caching fields section 10 deferred.
- **[Post 12 - Testing and debugging MCP](../12-testing-and-debugging/index.md):** the in-memory client behind every assertion in this post, and how to catch a silent schema failure before a user does.
