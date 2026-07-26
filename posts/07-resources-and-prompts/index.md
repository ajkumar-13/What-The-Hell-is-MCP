# 07 · Resources and prompts: the primitives that are not tools

> **TL;DR.** The Model Context Protocol (MCP) gives a server three ways to expose something,
> and they differ by who decides to fetch it: the model calls tools, the application reads
> resources, and the user invokes prompts. This post builds a static resource, a templated
> one guarded by an allowlist, a prompt, and a completion handler, then covers
> `subscriptions/listen`, the `ttlMs` and `cacheScope` caching fields, and the places where
> the high-level Python server quietly does less than the specification allows. It
> ends with a decision table, because reaching for a tool when a resource belongs is the most
> common design mistake in MCP servers.
>
> **After reading this you will be able to:**
> - Decide, for any piece of data, whether it belongs behind a tool, a resource, or a prompt.
> - Register a Uniform Resource Identifier (URI) template and defend its variables, which are untrusted input.
> - Write a completion handler, including the prefix match the software development kit (SDK) does not do for you.
> - Say precisely what `subscriptions/listen`, `ttlMs`, and pagination do and do not give you.

![Three columns, each headed by the actor that decides: a hexagon labelled the model above tools, a window labelled the application above resources, and a person labelled the user above prompts, with the specification's own words model-controlled, application-driven and user-controlled printed under each figure.](diagrams/01-three-primitives.svg)
*The primitive under each figure is a detail. The figure is the decision.*

---

## 1. Why not make everything a tool

Here is a server that works and is still built wrong.

It fronts an internal wiki, and it exposes three tools: `search_pages`, `get_page`, and
`list_recent`. Every one of them does its job. Then a user who knows exactly which document
they mean has to describe it in prose, so that the model can invent a search query, so that
the search can find the thing the user could have pointed at. A read that nobody needed the
model's judgment for now depends entirely on the model's judgment.

That is the symptom. The cause is that a tool is **model-controlled**. It is the primitive
whose trigger is pulled by the model and by nothing else, which is the right design when the
model has to decide, and the wrong one when somebody has already decided.

The protocol backs this up with four concrete differences, none of which are matters of
taste.

**Resource reads are cacheable and tool calls are not.** The caching page names six methods
whose complete results **must** carry `ttlMs` and `cacheScope`, and `resources/read`,
`resources/list`, and `resources/templates/list` are three of them. `tools/call` is not on the
list and carries neither field, so a conforming client can never cache a tool result. Section
6 covers what that buys you.

**Resources have per-item change notification.** A client can name individual URIs in the
`resourceSubscriptions` filter of a `subscriptions/listen` request and be told when exactly
those change. Tools get one bit for the whole list, `toolsListChanged`, and nothing per tool.

**Resource and prompt arguments can be auto-completed, tool arguments cannot.** The
`completion/complete` method takes exactly two reference types, `ref/prompt` and
`ref/resource`. There is no `ref/tool`. If you want the host to offer a user a picker of valid
values, the thing being picked has to be a resource template variable or a prompt argument.

**Only resources have a place in the interface that is not the model's turn.** Application-driven
means a host may put them in an attachment picker, a sidebar, or a paperclip menu, and read
one because a person clicked it.

That last point is also where the honest caveat lives, and it is the reason this mistake is so
common: host support for resources is uneven, and a tool works everywhere today. Section 10
has the bridge for when you need both.

Everything in this post is built in
[code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py),
the system-information server that [Post 05](../05-first-server/index.md) started and
[Post 06](../06-tools-in-depth/index.md) filled with tools. It is pinned to `mcp==2.0.0b2`,
which implements protocol revision 2026-07-28, and its suite reports `19 passed in 6.75s`.
Every string quoted below as output came from a run recorded in
[verify/RESULTS.md](../../verify/RESULTS.md).

## 2. Resources, and who decides to read one

The specification's own three words, which this series uses and does not paraphrase, are
**model-controlled** for tools, **application-driven** for resources, and **user-controlled**
for prompts. The hero diagram above is that sentence.

A server declares the capability in its `server/discover` result:

```json
{
  "capabilities": {
    "resources": {
      "listChanged": true,
      "subscribe": true
    }
  }
}
```

Both flags are optional and a server with neither may declare `"resources": {}`. `listChanged`
says the server will announce changes to the list. `subscribe` says the server supports
"resource-specific update notifications for resources requested through
`subscriptions/listen` using the `resourceSubscriptions` filter", which is section 5.

Wire format before code, as always. A `resources/list` result carries an array of these:

```typescript
export interface Resource extends BaseMetadata, Icons {
  uri: string;                 // REQUIRED
  name: string;                // REQUIRED
  title?: string;
  icons?: Icon[];
  description?: string;
  mimeType?: string;
  annotations?: Annotations;
  size?: number;
  _meta?: MetaObject;
}
```

| Field | Required | What it is for |
|---|---|---|
| `uri` | **Yes** | The identifier a `resources/read` names. Your namespace, your rules. |
| `name` | **Yes** | A programmatic identifier for the resource. |
| `title` | No | A human-readable label for a user interface. |
| `description` | No | Free text. A model may read it when the host offers the resource. |
| `mimeType` | No | The media type of what a read will return. |
| `size` | No | Raw bytes before encoding. Section 9. |
| `annotations` | No | `audience`, `priority`, `lastModified`. Section 9. |
| `icons` | No | Icon references for a user interface. |
| `_meta` | No | Your own metadata, under a reverse Domain Name System prefix. |

Reading one is a `resources/read` naming the URI, and the result is an array of contents:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "resources/read",
  "params": { "uri": "file:///project/src/main.rs" }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "resultType": "complete",
    "contents": [
      {
        "uri": "file:///project/src/main.rs",
        "mimeType": "text/x-rust",
        "text": "fn main() {\n    println!(\"Hello world!\");\n}"
      }
    ],
    "ttlMs": 60000,
    "cacheScope": "private"
  }
}
```

Those two are the specification's own examples rather than captures from this server, because
the shape is what matters here. Three things about them are easy to miss. `contents` is a
list, and servers **may** return several entries from one read, "for example, a server could
return the contents of several files when a directory resource is read". Each entry carries
either `text` or a Base64 `blob`, never both. And `resources/read` is one of the three methods
that participate in Multi Round-Trip Requests (MRTR), so it may answer with an
`input_required` result instead of contents, which is [Post 08](../08-elicitation-and-mrtr/index.md).

Now the code. A resource is a decorated function, and the return value is the content:

```python
@mcp.resource(
    "system://processes/top",
    title="Top processes by memory",
    mime_type="text/markdown",
)
def top_processes() -> str:
    """The ten processes currently using the most memory, as a Markdown table."""
    ...
    safe_name = name.replace("|", "\\|")
    lines.append(f"| {pid} | {safe_name} | {rss / (1024 * 1024):.1f} |")
```

That escaping line is not decoration. Process names are attacker-controlled on any machine
where an attacker can start a process, and a name containing a pipe character would otherwise
forge extra columns in a table that a model is about to read as fact. Untrusted text entering
a formatted document is where injection starts, whichever primitive is carrying it.

Listing the finished server prints, from [verify/RESULTS.md](../../verify/RESULTS.md):

```
- resource `system://processes/top` (text/markdown)
- template `system://disk/{disk}`
```

**A static resource takes no parameters at all.** This surprises people arriving from the tool
decorator, where parameters are the whole point. A static URI has nothing to bind, so a handler
that declares an argument is rejected when the decorator runs:

```
ValueError: Resource 'static://y' has no URI template variables,
but the handler declares parameters {'a'}.
```

**And that includes `Context`.** Context injection on resources is template-only. A static
resource that asks for a `Context` parameter raises `ValueError` at registration exactly like
any other unexpected argument. If a static resource needs to log, report progress, or publish
a notification, it cannot, and the work belongs somewhere that can.

Two error rules close the section, and one of them changed in this revision. A resource that
does not exist is `-32602` (`Invalid params`), not the `-32002` older code and older tutorials
use; clients **should** still accept `-32002` for compatibility. And servers **must not**
return an empty `contents` array for a missing resource, because "an empty array is ambiguous:
it could mean the resource exists but has no content, or that it doesn't exist at all". In the
Python SDK, raising `ResourceNotFoundError` from
`mcp.server.mcpserver.exceptions` produces the `-32602` for you.

## 3. URI design and templates

The rule for which kind of resource you just registered is mechanical, and it is worth stating
because nothing in the decorator call says it: **static versus template is decided purely by
whether the URI contains `{...}` variables.** No flag, no separate decorator. The SDK parses
the string as an RFC 6570 (Request for Comments 6570) URI template at decoration time and
branches on whether it found any variables.

Three working shapes:

```python
@mcp.resource("config://app")                        # static
def config() -> str: ...

@mcp.resource("weather://{city}/current")            # one variable
def weather(city: str) -> str: ...

@mcp.resource("notes://{name}{?limit}")              # query expansion
def notes(name: str, limit: int = 10) -> str: ...
```

The variables and the handler's parameters must correspond by name, and the checks all run at
registration rather than at read time. A mismatch raises `ValueError`. A `{?x}` or `{&x}` query
variable whose Python parameter has no default raises
`ValueError: query parameter(s) ['b'] have no default value.`. And `@mcp.resource` without
parentheses raises `TypeError`, the same trap the tool decorator has.

On the wire, templates are listed separately by `resources/templates/list`, and the object is
a `ResourceTemplate` whose `uriTemplate` replaces `uri`. The specification's summary is short:
"Resource templates allow servers to expose parameterized resources using URI templates.
Arguments may be auto-completed through the completion API." That second sentence is section 4.

**One honest gap.** The specification cites RFC 6570 and marks the
field `@format uri-template`, but it does not say which RFC 6570 *level* an implementation must
support, nor what to do with a template that fails to parse. Both are undefined in the release
candidate text, so treat matching behavior as an implementation detail rather than a contract.
That matters more than it sounds, and the next paragraphs are why.

**Design the URI scheme deliberately.** The standard schemes are `https://` for things a client
could fetch directly, `file://`, and `git://`, and a custom scheme **must** conform to RFC 3986.
A custom scheme, `system://` here, is usually the right answer for a server-specific namespace,
because it says plainly that the server is the only thing that knows how to resolve it. Keep the
hierarchy readable, `system://disk/{disk}` rather than `system://d?x={disk}`, because the URI is
the part a user sees.

### A template variable is untrusted input

Here is the templated resource in full:

```python
_ALLOWED_DISKS = {
    "root": pathlib.Path.home().anchor or "/",
}

@mcp.resource("system://disk/{disk}", title="Disk usage", mime_type="text/plain")
def disk_usage(disk: str) -> str:
    """Usage for one named disk. `disk` is a key from the server's allowlist."""
    path = _ALLOWED_DISKS.get(disk)
    if path is None:
        known = ", ".join(sorted(_ALLOWED_DISKS))
        raise ResourceNotFoundError(f"Unknown disk {disk!r}. Known disks: {known}.")

    usage = psutil.disk_usage(path)
    return f"disk: {disk}\npath: {path}\n..."
```

The allowlist is the defense. Not validation, not escaping, not a regular expression: a fixed
map of names the server is willing to answer for, with everything else refused. The
specification requires servers to validate all resource URIs, and to sanitize file paths to
prevent directory traversal when serving `file://` resources, and an allowlist is the only
version of that which is easy to review.

![Three resources/read requests entering from the left and travelling through a matcher stage and an allowlist gate before reaching the handler; the first passes both, the second is stopped at the gate with error -32602, and the third never matches the template at all and is rejected one stage earlier.](diagrams/02-uri-template.svg)
*Two rejections, two different stages. Only one of them is a defense you wrote.*

Now the part worth slowing down for. Two hostile-looking reads fail, and they fail for
completely different reasons. Both strings and both messages come from
[verify/RESULTS.md](../../verify/RESULTS.md):

```
system://disk/etc                  -> Unknown disk 'etc'. Known disks: root.
system://disk/../../etc/passwd     -> Unknown resource: system://disk/../../etc/passwd
```

The first read **matched the template**. `{disk}` bound to `"etc"`, the SDK called
`disk_usage("etc")`, the function ran, the allowlist said no, and the client got `-32602` with
a message that names the offending value and lists the legal ones. That is your code working.

The second read **never matched the template**. A simple `{disk}` expansion does not match a
value containing a slash, so the SDK found no registered resource for that URI and failed
before any handler existed to call. The message is `Unknown resource`, not `Unknown disk`,
and `disk_usage` was never entered.

The temptation is to read the second line as evidence that traversal is handled. It is not.
It is evidence that this particular template is shaped in a way that happens to reject slashes.
RFC 6570 also defines a reserved expansion operator whose values *may* contain slashes, and
since the specification pins neither the level nor the failure behavior, the matcher is exactly
the layer you should not be relying on. Widen the template, or move to an SDK version that
supports more of RFC 6570, and that same string walks straight into your handler with
`disk = "../../etc/passwd"`.

The test suite asserts both messages precisely so that a future widening of the template breaks
a test rather than a machine:

```python
async def test_traversal_shaped_uri_does_not_match_the_template_at_all():
    """A URI with path separators fails to match before the handler is reached."""
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("system://disk/../../etc/passwd")
        assert "Unknown resource" in str(excinfo.value)
```

`pytest.raises(Exception)` rather than a specific class is deliberate: an exception escaping
`async with Client(...)` arrives wrapped in an `ExceptionGroup`, because the underlying task
group re-raises, so a narrow `pytest.raises(MCPError)` would not catch it.

## 4. Completions

A template variable is only usable if the user can find out what to put in it. That is what
`completion/complete` is for, and it is the one method in this post whose whole job is the
typing experience.

The capability is declared as `{ "capabilities": { "completions": {} } }`, and the request
names a reference, the argument being typed, and optionally the arguments already filled in:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "completion/complete",
  "params": {
    "ref": { "type": "ref/resource", "uri": "system://disk/{disk}" },
    "argument": { "name": "disk", "value": "r" },
    "context": { "arguments": {} }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "complete",
    "completion": { "values": ["root"], "total": 1, "hasMore": false }
  }
}
```

Four facts about that exchange. `values` is capped at 100 items by the schema. `total` and
`hasMore` let you say "there are more than I sent". `context.arguments` carries the values of
*other* arguments, which is how you narrow a region list by an already-chosen country. And a
`CompleteResult` is neither cacheable nor allowed to be `input_required`, so it cannot ask the
user a question of its own.

In the Python SDK there is **one completion handler per server**, it **must** be `async def`,
and it takes three positional arguments:

```python
@mcp.completion()
async def complete(ref, argument, context):
    if isinstance(ref, ResourceTemplateReference) and argument.name == "disk":
        prefix = (argument.value or "").lower()
        return Completion(
            values=[d for d in sorted(_ALLOWED_DISKS) if d.startswith(prefix)]
        )

    if isinstance(ref, PromptReference) and argument.name == "symptom":
        prefix = (argument.value or "").lower()
        suggestions = ["general slowness", "high fan noise", "slow disk access",
                       "unresponsive windows"]
        return Completion(values=[s for s in suggestions if s.startswith(prefix)])

    return None
```

**The SDK does not filter for you.** Whatever list you return is the list the user sees, so the
`startswith` is yours to write and yours to forget. This is the single most common completion
bug: a handler that ignores `argument.value` and returns everything, which turns a picker into
a wall. Returning `None` is normalized to an empty `Completion`, which is the correct answer
for a reference you do not handle.

Measured, from [verify/RESULTS.md](../../verify/RESULTS.md):

```
- completion for prefix `r` returns `['root']`
```

and the test pins the other half of the contract, that a prefix matching nothing returns
nothing rather than everything:

```python
miss = await c.complete(
    ref=ResourceTemplateReference(uri="system://disk/{disk}"),
    argument={"name": "disk", "value": "zzz"},
)
assert miss.completion.values == []
```

One design note. Completion values are a disclosure surface. The list above is the same
allowlist the handler enforces, which is fine, but a completion handler over customer
identifiers happily enumerates your customer identifiers to anyone who can type one letter.
Filter by the caller's authorization, not just by the prefix.

## 5. Change notification with `subscriptions/listen`

**Changed in 2026-07-28.** `resources/subscribe` is gone. The subscriptions page says of the
single replacement method that "it replaces the former `resources/subscribe` RPC and the HTTP
GET endpoint", where HTTP is the Hypertext Transfer Protocol, and it now carries all four
server-push notification types on both transports. If you are reading code that calls
`resources/subscribe` or `resources/unsubscribe`, you are reading code written for an older
revision.

A client opts in with one request whose response is a long-lived stream:

```typescript
export interface SubscriptionFilter {
  toolsListChanged?: boolean;
  promptsListChanged?: boolean;
  resourcesListChanged?: boolean;
  resourceSubscriptions?: string[];
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "subscriptions/listen",
  "params": {
    "notifications": {
      "resourcesListChanged": true,
      "resourceSubscriptions": ["system://processes/top"]
    }
  }
}
```

All four filter fields are optional and `notifications` itself is not. Omitting a field means
not subscribing to it, and the rule that follows is a hard prohibition: "the server **MUST NOT**
send notification types the client has not explicitly requested." Nothing is broadcast. A
server with `listChanged: true` in its capabilities still sends nothing at all to a client that
never opened a stream.

![A sequence diagram with a client and a server lifeline: the client sends subscriptions/listen carrying the four filter fields, the server answers with an acknowledgment carrying the subscription id, work on the server changes a resource, notifications/resources/updated travels back down the open stream carrying only the URI, and the client issues a fresh resources/read.](diagrams/03-subscription-flow.svg)
*The notification names the URI. Step 4 is where the content actually arrives.*

Three details make this workable without sessions.

**The acknowledgment is always first.** The server **must** send
`notifications/subscriptions/acknowledged` before any other notification on that subscription,
and its `notifications` field reflects the subset it agreed to honor, with unsupported types
omitted. Clients **should** compare that against what they asked for.

**The subscription id is the request id.** Every notification on the stream carries
`_meta["io.modelcontextprotocol/subscriptionId"]`, and its value is the `id` of the
`subscriptions/listen` request that opened the stream. That is the `id` field of JSON-RPC,
Remote Procedure Call over JavaScript Object Notation, which is the envelope every MCP
message travels in. On standard input and output, where
every subscription shares one channel, that field is the only way to demultiplex.

**Ending it is explicit.** The client closes the stream, or the server sends the ordinary
JSON-RPC response to the long-lived request as a graceful end, or the transport dies. A server
tearing down a stream is the only sanctioned use of `notifications/cancelled` from the server
side, and after a reconnect the client **must** re-send `subscriptions/listen`, because the
server holds no subscription state.

What does **not** travel here: `notifications/progress` and `notifications/message` flow only
on the response stream of the request they belong to, never on a listen stream.

On the Python side, `MCPServer` wires the bus by default, and a server publishes through the
`Context` object:

```python
await ctx.notify_resource_updated("system://processes/top")
await ctx.notify_resources_changed()
```

Because `Context` injection is template-only on resources, the call almost always lives in the
thing that caused the change, which is usually a tool. A client subscribes with an async
context manager:

```python
async with client.listen(
    resources_list_changed=True,
    resource_subscriptions=["system://processes/top"],
) as sub:
    ...
```

Two notes to save you a search. The legacy `resources/subscribe` handlers are **not** available
on the high-level server; if you must serve a pre-2026 client you register them on the private
low-level server or use the low-level `Server` directly. And the specification's published
examples of the three `list_changed` notifications print them with no `params` at all, while
the subscriptions page requires `subscriptionId` in `_meta` on every notification delivered on
a stream. That is a genuine inconsistency in the release candidate. Implement per the
requirement and include the field.

## 6. Caching with `ttlMs` and `cacheScope`

New in this revision, from SEP-2549 (Specification Enhancement Proposal 2549). Both fields are
required, not optional, on the results that carry them:

```typescript
export interface CacheableResult extends Result {
  ttlMs: number;                        // REQUIRED, minimum 0
  cacheScope: "public" | "private";     // REQUIRED
}
```

Six methods must include them on a `resultType: "complete"` result: `server/discover`,
`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and
`resources/read`. Three conspicuous absences: `prompts/get`, `tools/call`, and
`completion/complete` are not cacheable and carry neither field.

`ttlMs` semantics are "analogous to HTTP `Cache-Control: max-age`". A positive value means the
client **should** treat the result as fresh for that many milliseconds after receiving it, so
the freshness test is `now < received_at + ttlMs`. Zero means immediately stale. A missing
value means assume zero, and a negative value means ignore it and assume zero. Time to live
(TTL) is a freshness hint and not a guarantee: servers may change the data before it expires.

The rule people break is the next one. Clients **should not** treat the TTL as a polling
interval that triggers background refetches, and any implementation that does poll **must**
apply jitter and backoff. A TTL is permission to skip a request, not an instruction to make one.

`cacheScope` is the security-relevant half:

| Value | Meaning |
|---|---|
| `"public"` | No user-specific data. Any client, gateway, or caching proxy may serve it to any user. |
| `"private"` | Reusable only within the same authorization context. Caches **must not** be shared across authorization contexts. |

The specification attaches a warning that is worth reading twice: servers **must** be aware
that a `"public"` response may be shared between callers even when it came from an
authenticated endpoint, and servers **must not** rely on `cacheScope` alone to prevent
unauthorized access. Access control is per primitive and lives in your code. Marking a
per-user resource `"public"` is a data leak with a two-word diff.

The cache key is the method plus the parameters that affect the result, the `uri` for a read
or the `cursor` for a list page, and a client **must not** serve a cached response for a request
whose parameters differ. A notification invalidates a fresh cached response immediately, which
is why sections 5 and 6 belong together: the TTL bounds how stale you can get, and a
subscription cuts it short when something actually changes. Results from an MRTR retry are
never cacheable at all.

**What the pinned SDK gives you.** The `@mcp.resource` decorator signature accepts `name`,
`title`, `description`, `mime_type`, `icons`, `annotations`, `meta`, and `security`. There is
no `ttl_ms` or `cache_scope` parameter, and the discovery output
[Post 05](../05-first-server/index.md) captured from this server reads
`cacheable 0 ms, scope private`. Zero and private is the conservative answer, and it is what
you get by default. Choosing a real TTL for a specific resource is not something the high-level
decorator exposes in `mcp==2.0.0b2`, so treat it as low-level territory until the API is
documented.

## 7. Pagination, and what the high-level server does not do

Pagination in MCP is opaque-cursor based, on exactly four operations: `resources/list`,
`resources/templates/list`, `prompts/list`, and `tools/list`. A request may carry `cursor`, a
result may carry `nextCursor`, and page size is the server's business.

Clients **must** treat cursors as opaque. The specification spells out the trap: "Don't make
any determination based on cursor value other than whether a non-null value was provided (e.g.
**an empty string is a valid cursor and thus MUST NOT be treated as the end of results**)." A
missing `nextCursor` means the end. An invalid cursor **should** produce `-32602`.

Now the part to be plain about. **On the high-level Python server, pagination does not
happen.** Every list handler on `MCPServer` ignores `params.cursor` and returns the entire set
with `next_cursor=None`. The SDK's own documentation is direct about the remedy:
"`@mcp.resource()` has no hook for any of that. To page, you write the list handler yourself,
on the low-level `Server`", passing `on_list_resources=` a function that receives the
`PaginatedRequestParams`.

So a server that registers five thousand resources with the decorator answers a
`resources/list` with five thousand resources in one result, whatever cursor the client sent.
That is legal, since page size is server-determined and one page is a page. It is also a
surprise waiting for whoever operates it. If your resource set is large, either build the list
handler on the low-level server or generate the set from a template with a small list, which
is what `system://disk/{disk}` does here.

The client side is complete: `list_tools`, `list_resources`, `list_resource_templates`, and
`list_prompts` all accept `cursor=`, so a client you write against a paginating server works
properly. It is only the server-side high-level API that has the gap.

## 8. Prompts as user-invoked entry points

A prompt is the primitive a person triggers. In practice it is the slash command, the "starter"
button, the menu entry. The host lists them, the user picks one, the server expands it into
messages, and the conversation begins with those messages already in it.

```typescript
export interface Prompt extends BaseMetadata, Icons {
  name: string;                    // REQUIRED
  title?: string;
  icons?: Icon[];
  description?: string;
  arguments?: PromptArgument[];
  _meta?: MetaObject;
}

export interface PromptArgument extends BaseMetadata {
  name: string;                    // REQUIRED
  title?: string;
  description?: string;
  required?: boolean;
}
```

Fetching one is `prompts/get` with a name and arguments, and the result is a list of messages:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "prompts/get",
  "params": {
    "name": "diagnose_performance",
    "arguments": { "symptom": "high fan noise" }
  }
}
```

Two constraints in that snippet are the ones that catch people.

**Prompt arguments are strings, and only strings.** The wire type is
`arguments?: { [key: string]: string }`. There is no schema, no integers, no booleans, no
nested objects. If a prompt needs a number, it receives the digits and parses them. This is a
much poorer argument model than a tool's JSON Schema, and deliberately so, because the thing
filling it in is a text field in a user interface.

**`GetPromptResult` extends plain `Result`, not `CacheableResult`.** No `ttlMs`, no
`cacheScope`. A prompt expansion is never cached, which is precisely what lets a prompt embed
a live reading:

```python
@mcp.prompt(
    title="Diagnose slow machine",
    description="Start an investigation into why this machine feels slow.",
)
def diagnose_performance(symptom: str = "general slowness") -> list[Message]:
    """Build an opening message with a live snapshot already attached."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()

    return [
        UserMessage(
            f"This machine is showing {symptom}.\n\n"
            f"Current readings: CPU {cpu:.1f} percent, "
            f"memory {mem.percent:.1f} percent used.\n\n"
            "Investigate the likely cause. Use the process tools if you need "
            "to see what is running, and tell me what you would check next."
        )
    ]
```

That is the difference between a prompt and a saved text snippet: **a prompt is a template that
runs code.** The user picks "Diagnose slow machine" and the conversation opens with the current
central processing unit (CPU) and memory figures already in the first message, plus an
instruction telling the model which tools exist. No tool call was needed to get there, and the
model did not have to decide to look.

Registration is `@mcp.prompt()`, the handler may return a string, a `Message`, a dict, or a
sequence of those, and a bare string in a list becomes a `UserMessage`. The recorded surface is:

```
- prompt `diagnose_performance`(symptom)
```

and the test asserts both halves of what the handler promises, the caller's symptom and the
live reading:

```python
result = await c.get_prompt("diagnose_performance", {"symptom": "high fan noise"})
text = result.messages[0].content.text
assert "high fan noise" in text
assert "CPU" in text
```

Three closing notes. `prompts/get` participates in MRTR, so a prompt may return
`input_required` and be retried, exactly like a tool call. The SDK's `Resolve` mechanism from
[Post 08](../08-elicitation-and-mrtr/index.md) is honored **on tools only** and is not read on
prompts or resources, so if a prompt needs to ask the user something, it does so through the
raw MRTR types. And prompt errors follow the resource rules: an unknown name or a missing
required argument is `-32602`, an internal failure is `-32603`.

## 9. Titles, icons, and annotations

Three small fields that decide how your primitives look in somebody else's interface.

**`name` against `title`.** `name` is the identifier; `title` is for humans and may contain
spaces and punctuation. For resources, templates, and prompts the display precedence is simply
`title` and then `name`. `Tool` is the one exception in the whole schema, because it also
consults `annotations.title` in between, which [Post 06](../06-tools-in-depth/index.md)
covers.

**`icons`.** An array of `{ src, mimeType, sizes }` objects. Nothing requires a host to render
them, and a host that does not is still conformant.

**`annotations`.** Shared by resources, resource templates, and every content block:

| Field | Type | What it says |
|---|---|---|
| `audience` | array of `"user"` and `"assistant"` | Who this is meant for. |
| `priority` | number, 0.0 to 1.0 | 1 means effectively required, 0 means entirely optional. |
| `lastModified` | ISO 8601 timestamp | For example `"2025-01-12T15:00:58Z"`. |

`audience: ["user"]` on a resource is how you tell a host that a document is for a person to
look at rather than for the model's context window, and `priority` is how you rank several
resources a host might attach. Both are hints. Like the tool annotations in Post 06, nothing
enforces them, and a client is free to ignore every one.

`size` belongs in the same conversation. It is "the size of the raw resource content, in bytes
(i.e., before base64 encoding or any tokenization), if known", and the specification's stated
purpose is that hosts can display file sizes and estimate context window usage. Setting it on
a large resource is a kindness to whoever has to budget tokens.

## 10. A decision table: tool, resource, or prompt

First, what the protocol actually gives each one. Every row below is a mechanical fact from the
schema, not a preference:

| | Tools | Resources | Prompts |
|---|---|---|---|
| Who pulls the trigger | the model | the application | the user |
| The specification's word | model-controlled | application-driven | user-controlled |
| Read with | `tools/call` | `resources/read` | `prompts/get` |
| Addressed by | name | URI | name |
| Arguments | JSON Schema, any type | URI template variables | named, strings only |
| Result carries `ttlMs` and `cacheScope` | no | **yes, required** | no |
| Per-item change notification | no | **yes**, `resourceSubscriptions` | no |
| Argument auto-completion | no | **yes**, `ref/resource` | **yes**, `ref/prompt` |
| Paginated list method | yes | yes | yes |
| Participates in MRTR | yes | yes | yes |

Then the question you are actually asking, which is about a specific thing you are about to
build:

| What you have | The primitive | Why |
|---|---|---|
| The text of a document the user picked in the host's interface | **resource** | The decision is already made. Routing it through the model adds a guess. |
| Full-text search across ten million documents | **tool** | The query is the model's to invent, and the result is not addressable in advance. |
| The current on-call rotation, read-only, refreshed hourly | **resource** | Cacheable with a real TTL, and subscribable when it changes. |
| One report per customer id, thousands of customers | **resource template** | The id is a URI variable, and a completion handler turns it into a picker. |
| Restart a pod | **tool** | It changes the world, and the model must be able to reach it mid-task. |
| "Review this pull request" as a slash command | **prompt** | The user chose to start it; the server supplies the framing and the checklist. |
| An incident triage opener with live metrics attached | **prompt** | The handler runs code, so the first message arrives with the numbers already in it. |
| A 40 MB log file the model must not read whole | **tool returning a resource link** | Return a summary plus a `resource_link` block, and let the host fetch the bulk. |

**The thesis, stated plainly.** Using a tool where a resource belongs is the most common design
mistake in MCP servers, and it is common because it is the path of least resistance. `@mcp.tool()`
works in every host today. Resources need the host to have built a surface for them, and many
have not. So people ship `get_config` as a tool, and the model spends a call fetching something
nobody needed it to think about, uncacheable, unsubscribable, uncompletable.

The repair is not ideological. Expose the data as a resource, because that is what it is, and
if the model genuinely also needs to reach it during a turn, add a thin tool that returns a
`resource_link` content block pointing at the same URI. One definition of the data, two doors,
and the host picks whichever it can render.

Two closing rules that apply to all three primitives, and both are easy to violate by accident.
The set of resources, templates, and prompts a server exposes **must not** vary per connection
or as a side effect of another request on that connection, although it **may** vary by the
authorization presented on the request. Filtering by credential is fine; a `connect_project`
tool that makes new resources appear is not. And the entire surface of a server, resource
descriptions included, enters a model's context before anything is called, so the same tool
poisoning concerns [Post 19](../19-security/index.md) raises for tool descriptions apply word
for word to resource and prompt descriptions.

---

## Common pitfalls

- **Shipping a read as a tool.** The moment data has a stable identity, it wants a URI. A tool
  version of the same read cannot be cached, cannot be subscribed to, cannot be completed, and
  cannot be attached by a user who already knows they want it. Expose the resource, and add a
  thin tool returning a `resource_link` only if the model also needs a door.
- **Trusting the URI matcher as a path check.** `system://disk/../../etc/passwd` is rejected
  with `Unknown resource` because a simple `{disk}` expansion does not match a slash, not
  because anything validated it. The specification pins neither the RFC 6570 level nor the
  failure behavior. The allowlist inside the handler is the defense, and it is the one that
  produced `Unknown disk 'etc'. Known disks: root.`
- **Giving a static resource a parameter, including `Context`.** Static versus template is
  decided purely by whether the URI contains `{...}`. A static URI with handler parameters
  raises `ValueError` at registration, and `Context` injection is template-only, so a static
  resource cannot log, report progress, or publish a notification.
- **Assuming `cursor` does something.** The high-level server ignores it and returns everything
  with no `nextCursor`. If you need real pages, write the list handler on the low-level
  `Server`. Do not document pagination your server does not implement.
- **Expecting the SDK to filter completions.** It returns whatever your handler returns. Write
  the prefix match yourself, and filter by the caller's authorization too, because a completion
  handler is an enumeration endpoint wearing a friendly hat.
- **Waiting for notifications that were never requested.** There is no broadcast. A server
  sends `notifications/resources/updated` only to clients that opened a `subscriptions/listen`
  stream naming that URI in `resourceSubscriptions`, and `resources/subscribe` no longer exists.
- **Passing a number as a prompt argument.** Prompt arguments are `string`-valued on the wire,
  full stop. Parse inside the handler, and say so in the argument's description.
- **Returning an empty `contents` array for a resource that is not there.** It is explicitly
  forbidden because it is ambiguous. Raise `ResourceNotFoundError` and let the client see
  `-32602`.

---

## Further reading

- Specification, *"Resources"*, revision 2026-07-28. The `Resource` object, the capability
  flags, the `-32602` rule, the empty-contents prohibition, and the URI scheme and security
  requirements quoted in sections 2 and 3.
  <https://modelcontextprotocol.io/specification/draft/server/resources>
- Specification, *"Prompts"*, revision 2026-07-28. The `Prompt` and `PromptArgument` objects,
  string-only arguments, and the prompt error codes in section 8.
  <https://modelcontextprotocol.io/specification/draft/server/prompts>
- Specification, *"Completion"*, revision 2026-07-28. The two reference types, the 100-item
  cap, and the `context.arguments` field.
  <https://modelcontextprotocol.io/specification/draft/server/utilities/completion>
- Specification, *"Pagination"*, revision 2026-07-28. Opaque cursors, the four paginated
  operations, and the empty-string-is-a-valid-cursor rule.
  <https://modelcontextprotocol.io/specification/draft/server/utilities/pagination>
- Specification, *"Caching"*, revision 2026-07-28, and SEP-2549. The six cacheable methods,
  the TTL semantics, and the `cacheScope` security warning in section 6.
- Specification, *"Subscriptions"*, revision 2026-07-28. The filter fields, the acknowledgment
  ordering, the subscription id, and the statement that this replaces `resources/subscribe`.
- RFC 6570, *URI Template*. <https://www.rfc-editor.org/rfc/rfc6570>
- RFC 3986, *Uniform Resource Identifier (URI): Generic Syntax*, for custom schemes.
- MCP Python SDK, `mcp==2.0.0b2`, driving
  [code/05-first-server/](../../code/05-first-server/). Every captured string in this post is
  reproducible from [verify/RESULTS.md](../../verify/RESULTS.md).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**:
  the other direction of the same question. Here the user pulls the trigger before the work
  starts; there the server stops halfway through and asks.
- **[Post 06 — Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md)**:
  the primitive this post spent ten sections not using, including the `resource_link` content
  block that section 10 leans on.
- **[Post 11 — Building a host: the tool loop, many servers, and permissions](../11-building-a-host/index.md)**:
  the other side of application-driven, and what a host has to build before a resource is worth
  exposing at all.
