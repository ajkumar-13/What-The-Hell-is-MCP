# 07 - Resources and prompts: the primitives that are not tools

> **TL;DR.** The Model Context Protocol (MCP) gives a server three ways to expose something, and they differ by who decides to fetch it: the model calls tools, the application reads resources, and the user invokes prompts. This post adds a static resource, a Uniform Resource Identifier (URI) template guarded by an allowlist, a prompt, and a completion handler to the first server, then reads `subscriptions/listen`, the `ttlMs` and `cacheScope` caching fields, and pagination, and marks where the high-level Python server does less than the specification allows. It ends with a decision table, because reaching for a tool where a resource belongs is an easy design mistake to make and an expensive one to keep.
>
> **Prerequisites:** [Post 06](../06-tools-in-depth/index.md).
> **Safe to skip?** Skip it if the reader can already say which of the three primitives a piece of data belongs behind, keep a hostile template variable out of a handler, write a completion handler that filters by prefix, and say what a listen stream, a time to live, and a cursor do on a server built with the Python software development kit (SDK).
>
> **After reading, you will be able to:**
>
> - Decide for any piece of data whether it belongs behind a tool, a resource, or a prompt, by naming who should decide to fetch it.
> - Register a URI template and defend its variables as untrusted input with an allowlist that the handler enforces.
> - Write a completion handler that does the prefix match itself, since the SDK returns whatever the handler returns.
> - State what subscriptions/listen, ttlMs with cacheScope, and pagination give a client, and what the high-level Python server leaves out.

![Three columns, each headed by the party that decides: a diamond for the model above tools, a window for the application above resources, and a person for the user above prompts, with the specification's own words model-controlled, application-driven and user-controlled printed under each figure and the method that carries each primitive in its box.](diagrams/01-three-primitives.svg)

*The primitive under each figure is a detail. The figure is the decision.*

---

## 1. The question: why not make everything a tool?

Here is a server that works and is still built wrong.

It fronts an internal wiki, and it exposes three tools: `search_pages`, `get_page`, and `list_recent`. Every one of them does its job. Then a user who knows exactly which document they mean has to describe it in prose, so that the model can invent a search query, so that the search can find the thing the user could have pointed at. A read that nobody needed the model's judgment for now depends entirely on the model's judgment.

That is the symptom. The cause is that a tool is **model-controlled**. It is the primitive whose trigger is pulled by the model and by nothing else, which is the right design when the model has to decide, and the wrong one when somebody has already decided. So the question this post answers is: **when should a server expose something as a resource or a prompt instead of a tool, and who pulls each trigger?**

The protocol backs the answer with four concrete differences, none of which are matters of taste.

**Resource reads are cacheable and tool calls are not.** The specification's Caching page names six methods whose complete results **must** carry `ttlMs` and `cacheScope`, and `resources/read`, `resources/list`, and `resources/templates/list` are three of them. `tools/call` is not on the list and carries neither field, so the protocol gives a client no way to know how long a tool result stays fresh. Section 6 covers what that buys.

**Resources have per-item change notification.** A client can name individual URIs in the `resourceSubscriptions` filter of a `subscriptions/listen` request and be told when exactly those change. Tools get one bit for the whole list, `toolsListChanged`, and nothing per tool.

**Resource and prompt arguments can be auto-completed, tool arguments cannot.** The `completion/complete` method takes exactly two reference types, `ref/prompt` and `ref/resource`. There is no `ref/tool`. For a host to offer a user a picker of valid values, the thing being picked has to be a resource template variable or a prompt argument.

**Resources have a place in the interface outside the model's turn.** Application-driven means a host may put them in an attachment picker, a sidebar, or a paperclip menu, and read one because a person clicked it.

That last point is also where the honest caveat lives, and it is why the mistake is easy to make: application-driven also means the specification leaves the resource interface to the host, so a server author cannot count on a picker existing, while a tool reaches the model in any host that runs a tool loop. Section 10 has the bridge for when a server needs both.

Everything in this post is built in [code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py), the system-information server that [post 05](../05-first-server/index.md) started and [post 06](../06-tools-in-depth/index.md) filled with tools. The project pins `mcp==2.3.0`, which implements protocol revision 2026-07-28, and `uv run --extra dev pytest` inside it reports `19 passed`. Every transcript below is printed by one of the six scripts under `snippets/` that section 11 lists.

## 2. Resources, and who decides to read one

The specification's own three words, which this series uses and does not paraphrase, are **model-controlled** for tools, **application-driven** for resources, and **user-controlled** for prompts. The hero figure above is that sentence.

A server declares the resource capability in its `server/discover` result. `snippets/resource_surface.py` connects an in-memory client to the first server and prints what it declares, in the wire spelling of JavaScript Object Notation (JSON):

```
resources: {"subscribe": true, "listChanged": true}
prompts: {"listChanged": true}
completions: {}
```

Both resource flags are optional, and a server with neither may declare `"resources": {}`. `listChanged` says the server will announce changes to the list. `subscribe` says the server supports "resource-specific update notifications for resources requested through `subscriptions/listen` using the `resourceSubscriptions` filter", which is section 5. Nothing in the project sets these flags; the SDK's `MCPServer` fills them in itself.

Wire format before code, as always. A `resources/list` result carries an array of `Resource` objects, condensed here from the specification's TypeScript schema:

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
| `uri` | **Yes** | The identifier a `resources/read` names. The server's namespace, under the server's rules. |
| `name` | **Yes** | A programmatic identifier for the resource. |
| `title` | No | A human-readable label for a user interface. |
| `description` | No | Free text. A model may read it when the host offers the resource. |
| `mimeType` | No | The media type of what a read will return. |
| `size` | No | Raw bytes before encoding. Section 9. |
| `annotations` | No | `audience`, `priority`, `lastModified`. Section 9. |
| `icons` | No | Icon references for a user interface. |
| `_meta` | No | The server's own metadata, under a reverse Domain Name System (DNS) prefix. |

The first server's one static resource, as `snippets/resource_surface.py` prints its entry in `resources/list`:

```
{"name": "top_processes", "title": "Top processes by memory", "uri": "system://processes/top", "description": "The ten processes currently using the most memory, as a Markdown table.", "mimeType": "text/markdown"}
```

The decorator below sets only the URI, the title, and the media type; the SDK took `name` from the function's name and `description` from its docstring.

Reading one is a `resources/read` naming the URI, and the result is an array of contents. These two messages are the specification's own examples rather than captures from this server, because the shape is what matters here:

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

Three things about them are easy to miss. `contents` is a list, and servers **may** return several entries from one read, "for example, a server could return the contents of several files when a directory resource is read". Each entry carries either `text` or a Base64 `blob`, never both. And `resources/read` is one of the three methods that participate in Multi Round-Trip Requests (MRTR), so it may answer with an `input_required` result instead of contents, which is [post 08](../08-elicitation-and-mrtr/index.md).

Now the code. A resource is a decorated function, and the return value is the content. From [code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py):

```python
@mcp.resource(
    "system://processes/top",
    title="Top processes by memory",
    mime_type="text/markdown",
)
def top_processes() -> str:
    """The ten processes currently using the most memory, as a Markdown table."""
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            mem = info["memory_info"]
            rows.append((info["pid"], info["name"] or "", mem.rss if mem else 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    rows.sort(key=lambda r: r[2], reverse=True)

    lines = ["| PID | Name | Memory (MB) |", "| --- | --- | --- |"]
    for pid, name, rss in rows[:10]:
        # Escape pipes so a process with a pipe in its name cannot break out of
        # the table and forge extra rows. Untrusted text going into a formatted
        # document is exactly where injection starts.
        safe_name = name.replace("|", "\\|")
        lines.append(f"| {pid} | {safe_name} | {rss / (1024 * 1024):.1f} |")

    return "\n".join(lines)
```

The escaping line is not decoration. Process names are attacker-controlled on any machine where an attacker can start a process, and a name containing a pipe character would otherwise forge extra columns in a table that a model is about to read as fact. Untrusted text entering a formatted document is where injection starts, whichever primitive is carrying it. Read through a client, the resource comes back as one content entry of type `text/markdown` whose first line is `| PID | Name | Memory (MB) |`.

Two error rules close the section, and one of them changed in this revision. A resource that does not exist is `-32602` (`Invalid params`) in the JSON Remote Procedure Call (JSON-RPC) error object, not `-32002`, which is what older code and older tutorials use; clients **should** still accept `-32002` for compatibility. And servers **must not** return an empty `contents` array for a missing resource, because "an empty array is ambiguous: it could mean the resource exists but has no content, or that it doesn't exist at all". In the Python SDK, raising `ResourceNotFoundError` from `mcp.server.mcpserver.exceptions` produces the `-32602` for the server, with the URI in the error's `data` as the specification's own example has it; section 3 prints one.

## 3. URI design and templates

The rule for which kind of resource a decorator just registered is mechanical, and it is worth stating because nothing in the decorator call says it: **static versus template is decided purely by whether the URI contains `{...}` variables.** No flag, no separate decorator. The SDK parses the string as an RFC 6570 (Request for Comments 6570) URI template when the decorator runs and branches on whether it found any variables.

Three working shapes, from `snippets/registration_errors.py`:

```python
@mcp.resource("config://app")                        # static
def config() -> str: ...

@mcp.resource("weather://{city}/current")            # one variable
def weather(city: str) -> str: ...

@mcp.resource("notes://{name}{?limit}")              # query expansion
def notes(name: str, limit: int = 10) -> str: ...
```

The script lists them back through a client, and the split is exactly the braces:

```
resources/list carries ['config://app']
resources/templates/list carries ['weather://{city}/current', 'notes://{name}{?limit}']
```

The variables and the handler's parameters must correspond by name, a query variable such as `{?limit}` needs a parameter with a default because a client may leave it out, and every one of these checks runs at registration rather than at read time. Section 12 prints what each mistake raises.

On the wire, templates are listed separately by `resources/templates/list`, and the object is a `ResourceTemplate` whose `uriTemplate` replaces `uri`. The first server's one template, as `snippets/resource_surface.py` prints it:

```
{"name": "disk_usage", "title": "Disk usage", "uriTemplate": "system://disk/{disk}", "description": "Usage for one named disk. `disk` is a key from the server's allowlist.", "mimeType": "text/plain"}
```

The specification's summary is short: "Resource templates allow servers to expose parameterized resources using URI templates. Arguments may be auto-completed through the completion API." That second sentence is the completion application programming interface (API), and it is section 4.

**One honest gap.** The specification cites RFC 6570 and marks the field `@format uri-template`, but the 2026-07-28 text says neither which RFC 6570 *level* an implementation must support nor what to do with a template that fails to parse. Each SDK answers for itself. The 2.3.0 Python SDK documents, in its `mcp.shared.uri_template` module, that its matcher supports Levels 1 to 3 fully plus the Level 4 explode modifier, and it refuses at registration a template it cannot match unambiguously. Treat matching behavior as an implementation detail rather than a contract, which matters more than it sounds, as the next part shows.

**Design the URI scheme deliberately.** The standard schemes the specification names, and it says the list is not exhaustive, are `https://` (only for something the client can fetch directly, without the server), `file://`, and `git://`. A custom scheme **must** conform to RFC 3986. A custom scheme, `system://` here, is usually the right answer for a server-specific namespace, because it says plainly that the server is the only thing that knows how to resolve it. Keep the hierarchy readable, `system://disk/{disk}` rather than `system://d?x={disk}`, because the URI is the part a user sees.

### A template variable is untrusted input

Here is the templated resource and the map it consults, from [code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py):

```python
# The disks we are willing to describe through the templated resource below.
# An allowlist, rather than accepting any path the caller sends, is the whole
# defense here: a resource template variable is untrusted input.
_ALLOWED_DISKS = {
    "root": pathlib.Path.home().anchor or "/",
}
```

```python
@mcp.resource(
    "system://disk/{disk}",
    title="Disk usage",
    mime_type="text/plain",
)
def disk_usage(disk: str) -> str:
    """Usage for one named disk. `disk` is a key from the server's allowlist."""
    path = _ALLOWED_DISKS.get(disk)
    if path is None:
        # The SDK turns this into -32602 Invalid params for the client.
        known = ", ".join(sorted(_ALLOWED_DISKS))
        raise ResourceNotFoundError(f"Unknown disk {disk!r}. Known disks: {known}.")

    usage = psutil.disk_usage(path)
    return (
        f"disk: {disk}\n"
        f"path: {path}\n"
        f"used: {usage.used // _GB} GB\n"
        f"total: {usage.total // _GB} GB\n"
        f"percent: {usage.percent:.1f}\n"
    )
```

The allowlist is the defense the server wrote. Not validation, not escaping, not a regular expression: a fixed map of names the server is willing to answer for, with everything else refused. The specification requires servers to validate all resource URIs, and to sanitize file paths to prevent directory traversal when serving `file://` resources, and an allowlist is the version of that which is easiest to review.

![Three resources/read requests entering from the left and traveling through a matcher stage and an allowlist gate before reaching the handler; the first passes both, the second is stopped at the gate with error -32602, and the third never matches the template at all and is rejected one stage earlier.](diagrams/02-uri-template.svg)

*Two rejections, two different stages. Only one of them is a defense the server's author wrote.*

Now the part worth slowing down for. `snippets/template_refusals.py` reads the template with four hostile-looking values, and every one fails, but not for the same reason:

```
system://disk/etc
    error {"code": -32602, "message": "Unknown disk 'etc'. Known disks: root.", "data": {"uri": "system://disk/etc"}}
system://disk/../../etc/passwd
    error {"code": -32602, "message": "Unknown resource: system://disk/../../etc/passwd", "data": {"uri": "system://disk/../../etc/passwd"}}
system://disk/..%2F..%2Fetc%2Fpasswd
    error {"code": -32602, "message": "Unknown resource: system://disk/..%2F..%2Fetc%2Fpasswd", "data": {"uri": "system://disk/..%2F..%2Fetc%2Fpasswd"}}
system://disk/..
    error {"code": -32602, "message": "Unknown resource: system://disk/..", "data": {"uri": "system://disk/.."}}
```

The first read **reached the handler**. `{disk}` bound to `"etc"`, the SDK called `disk_usage("etc")`, the allowlist said no, and the client got `-32602` with a message that names the offending value and lists the legal ones. That is the server's own code working.

The other three never reached it, and the script's second part asks the two SDK layers in front of the handler why:

```
system://disk/etc
    matcher: {'disk': 'etc'}; ResourceSecurity: passes
system://disk/../../etc/passwd
    matcher: no match
system://disk/..%2F..%2Fetc%2Fpasswd
    matcher: {'disk': '../../etc/passwd'}; ResourceSecurity: rejects 'disk'
system://disk/..
    matcher: {'disk': '..'}; ResourceSecurity: rejects 'disk'
```

The literal traversal string **never matched the template**: a simple `{disk}` expansion does not match a value containing a slash. The percent-encoded one did match, because the matcher decodes `%2F` into `/` and binds `disk = "../../etc/passwd"`, and so did a bare `..`. Both were then refused by `ResourceSecurity`, a policy that `MCPServer` applies to every template parameter by default and that rejects a value whose `..` segments would climb above its starting point, a value that looks like an absolute path, and any null byte. From the client, all three look the same as a URI that names nothing at all: `-32602`, `Unknown resource`.

The temptation is to read those three lines as evidence that traversal is handled. It is handled by layers the server's author did not write and does not see in the code. The matcher's refusal is the shape of this template, and the policy is one keyword argument away from switched off. The script's third part widens the template to the reserved expansion `{+disk}`, whose values may contain slashes, on a throwaway server, once with the default policy and once with `security=ResourceSecurity(exempt_params={"disk"})`:

```
default policy: error -32602: Unknown resource: system://disk/../../etc/passwd
policy off for disk: handler entered with disk='../../etc/passwd'
```

Widen the template and exempt the parameter, which is what an author reaches for the first time the policy refuses a value the template legitimately needs, and the same string walks straight into the handler. The allowlist is the only layer that knows which disks exist, and it is the only one that keeps working whatever the template looks like.

The project's test suite asserts both messages so that a future widening of the template breaks a test rather than a machine. From [code/05-first-server/tests/test_server.py](../../code/05-first-server/tests/test_server.py):

```python
async def test_traversal_shaped_uri_does_not_match_the_template_at_all():
    """A URI with path separators fails to match before the handler is reached."""
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("system://disk/../../etc/passwd")
        assert "Unknown resource" in str(excinfo.value)
```

What `read_resource` raises is `MCPError` (importable from `mcp`), whose `error` field carries the code, message, and data printed above; the test catches the broader `Exception`, which includes it. Section 12 shows what the same exception becomes once it escapes the `async with` block.

## 4. Completions

A template variable is only usable if the user can find out what to put in it. That is what `completion/complete` is for, and it is the one method in this post whose whole job is the typing experience.

The capability is declared as `{ "capabilities": { "completions": {} } }`, which is the `completions: {}` line section 2 printed. The request names a reference, the argument being typed, and optionally a `context` carrying the arguments already filled in. `snippets/client_requests.py` prints what the SDK's client sends when asked for the template variable after the user has typed `r`; the transport adds `jsonrpc` and `id` around it, and the `_meta` envelope every request carries ([post 03](../03-wire-protocol/index.md)) is shown as its key list:

```
params._meta keys: ['io.modelcontextprotocol/clientCapabilities', 'io.modelcontextprotocol/clientInfo', 'io.modelcontextprotocol/protocolVersion']
{
  "method": "completion/complete",
  "params": {
    "ref": {
      "type": "ref/resource",
      "uri": "system://disk/{disk}"
    },
    "argument": {
      "name": "disk",
      "value": "r"
    }
  }
}
```

With no other argument filled in, the client leaves `context` out. `snippets/completion_and_prompt.py` prints the result the server returns, here with its `_meta`:

```
one full result: {"_meta": {"io.modelcontextprotocol/serverInfo": {"name": "system-info", "version": ""}}, "completion": {"values": ["root"]}, "resultType": "complete"}
```

Four facts about that exchange. `values` is capped at 100 items by the schema. `total` and `hasMore` are optional fields that let a server say "there are more than I sent", and the SDK leaves both out unless the handler sets them, which this one does not. `context.arguments` carries the values of *other* arguments, which is how a region list is narrowed by an already-chosen country. And a `CompleteResult` is neither cacheable nor allowed to be `input_required`, so it cannot ask the user a question of its own.

In the Python SDK one completion handler serves every reference on the server, the SDK awaits it so it is written `async def`, and it takes three positional arguments: the reference, the argument being typed, and the context. From [code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py):

```python
@mcp.completion()
async def complete(ref, argument, context):
    """Suggest values while a user is filling in a prompt or a resource template.

    The SDK does not filter for you. Whatever this returns is what the user
    sees, so the prefix match has to be written by hand.
    """
    if isinstance(ref, ResourceTemplateReference) and argument.name == "disk":
        prefix = (argument.value or "").lower()
        return Completion(
            values=[d for d in sorted(_ALLOWED_DISKS) if d.startswith(prefix)]
        )

    if isinstance(ref, PromptReference) and argument.name == "symptom":
        prefix = (argument.value or "").lower()
        suggestions = [
            "general slowness",
            "high fan noise",
            "slow disk access",
            "unresponsive windows",
        ]
        return Completion(values=[s for s in suggestions if s.startswith(prefix)])

    return None
```

**The SDK does not filter for the server.** Whatever list the handler returns is the list the user sees, so the `startswith` is the handler's to write and the handler's to forget. A handler that ignores `argument.value` and returns everything is the easiest completion bug to write, and it turns a picker into a wall. Returning `None` is normalized to an empty `Completion`, which is the correct answer for a reference the handler does not serve. The script drives the handler with a few prefixes:

```
ref/resource disk, value 'r': {"values": ["root"]}
ref/resource disk, value 'zzz': {"values": []}
ref/resource disk, value '': {"values": ["root"]}
ref/prompt symptom, value 's': {"values": ["slow disk access"]}
ref/prompt symptom, value 'H': {"values": ["high fan noise"]}
```

An empty value offers everything, a prefix that matches nothing offers nothing, and the capital `H` still matches because the handler lowers the prefix before comparing. The project's test pins the half of the contract that is easiest to break, that a miss returns nothing rather than everything. From [code/05-first-server/tests/test_server.py](../../code/05-first-server/tests/test_server.py):

```python
async def test_completion_filters_by_prefix():
    async with Client(mcp) as c:
        hit = await c.complete(
            ref=ResourceTemplateReference(uri="system://disk/{disk}"),
            argument={"name": "disk", "value": "r"},
        )
        assert "root" in hit.completion.values

        miss = await c.complete(
            ref=ResourceTemplateReference(uri="system://disk/{disk}"),
            argument={"name": "disk", "value": "zzz"},
        )
        assert miss.completion.values == []
```

One design note. Completion values are a disclosure surface. The list above is the same allowlist the handler enforces, which is fine, but a completion handler over customer identifiers happily enumerates those identifiers to anyone who can type one letter. Filter by the caller's authorization, not just by the prefix.

## 5. Change notification with `subscriptions/listen`

**Changed in 2026-07-28.** `resources/subscribe` is gone. The specification's Subscriptions page says of the single replacement method that "it replaces the former `resources/subscribe` RPC and the HTTP GET endpoint", where HTTP is the Hypertext Transfer Protocol, and the one method now carries all four server-push notification types on both transports. Code that calls `resources/subscribe` or `resources/unsubscribe` was written for an older revision.

A client opts in with one request whose response is a long-lived stream. The filter, condensed from the schema:

```typescript
export interface SubscriptionFilter {
  toolsListChanged?: boolean;
  promptsListChanged?: boolean;
  resourcesListChanged?: boolean;
  resourceSubscriptions?: string[];
}
```

and the request the SDK's client sends to the first server for its list and its process table, as `snippets/client_requests.py` prints it:

```
{
  "method": "subscriptions/listen",
  "params": {
    "notifications": {
      "resourcesListChanged": true,
      "resourceSubscriptions": [
        "system://processes/top"
      ]
    }
  }
}
```

Those four are every field core defines, all of them optional, and `notifications` itself is not. Omitting a field means not subscribing to it, and the rule that follows is a hard prohibition: "The server **MUST NOT** send notification types the client has not explicitly requested." Nothing is broadcast. A server with `listChanged: true` in its capabilities still sends nothing at all to a client that never opened a stream. An extension may add a fifth field: the tasks extension puts `taskIds` in the same filter, which [post 09](../09-tasks/index.md) covers.

![A sequence diagram with a client and a server lifeline: the client sends subscriptions/listen with a notifications filter naming one URI in resourceSubscriptions, the server answers first with an acknowledgment carrying the subscription id, work on the server changes the resource, notifications/resources/updated travels back down the open stream carrying only the URI, and the client issues a fresh resources/read.](diagrams/03-subscription-flow.svg)

*The notification names the URI. Step 4 is where the content actually arrives.*

Three details make this workable without sessions.

**The acknowledgment is always first.** The server **must** send `notifications/subscriptions/acknowledged` before any other notification on that subscription, and its `notifications` field reflects the subset it agreed to honor, with unsupported types omitted. Clients **should** compare that against what they asked for.

**The subscription id is the request id.** Every notification on the stream carries `_meta["io.modelcontextprotocol/subscriptionId"]`, and its value is the JSON-RPC `id` of the `subscriptions/listen` request that opened the stream. On standard input and output (stdio), where every subscription shares one channel, that field is the only way to demultiplex.

**Ending it is explicit.** The client closes the stream, or the server sends the ordinary JSON-RPC response to the long-lived request as a graceful end, or the transport dies. A server tearing down a stream is the only sanctioned use of `notifications/cancelled` from the server side, and when a stdio connection is re-established the client **must** re-send `subscriptions/listen`, because the server holds no subscription state across reconnections.

What does **not** travel here: `notifications/progress` and `notifications/message` flow only on the response stream of the request they belong to, never on a listen stream.

The first server registers nothing that changes its resources, so `snippets/listen_and_cache.py` builds a small throwaway server with one static resource, `notes://today`, and one tool that rewrites it. A server publishes through the `Context` object the SDK injects into the tool:

```python
@mcp.tool()
async def rewrite_note(text: str, ctx: Context) -> str:
    """Replace today's note, then tell every listener that asked about it."""
    NOTE["text"] = text
    await ctx.notify_resource_updated("notes://today")
    await ctx.notify_resources_changed()
    return "rewritten"
```

The call lives in the thing that caused the change, which is usually a tool, and it has to: a static resource cannot take a `Context` at all (section 12 prints the refusal). A client subscribes with an async context manager, which returns once the acknowledgment has arrived:

```python
async def watch(client: Client) -> None:
    async with client.listen(
        tools_list_changed=True,
        resource_subscriptions=["notes://today"],
    ) as sub:
        print(f"subscription id {sub.subscription_id!r}, honored "
              f"{json.dumps(sub.honored.model_dump(mode='json', by_alias=True, exclude_none=True))}")
        await client.call_tool("rewrite_note", {"text": "second draft"})
        event = await asyncio.wait_for(sub.__anext__(), timeout=5)
        print(f"event: {event!r}")
```

The script prints:

```
resources capability: subscribe=True listChanged=True
subscription id 'listen-1', honored {"toolsListChanged": true, "resourceSubscriptions": ["notes://today"]}
event: ResourceUpdated(uri='notes://today')
no second event within 0.5 s (resources list_changed was not requested)
re-read: 'second draft'
```

Every rule above is in those five lines. The subscription id is the id the SDK's client gave its `subscriptions/listen` request, the string `listen-1` (a JSON-RPC id may be a string or a number). The honored filter echoes what was asked. The update names the URI and nothing else, so the new text arrives only with the re-read. And the tool also called `notify_resources_changed()`, but this client never asked for `resourcesListChanged`, so that notification never arrived.

Two notes save a search. `MCPServer` serves `subscriptions/listen` by default, and `MCPServer(subscriptions=False)` turns it off; the high-level server serves no `resources/subscribe` at all, so a pre-2026 client that calls it is refused (section 12 prints both). And the specification argues with itself here, in a way worth knowing before diffing output against a published sample. The normative text is not ambiguous: the Subscriptions page says the server **must** put `subscriptionId` in `_meta` on every notification delivered on a stream, and that is what [post 03](../03-wire-protocol/index.md) records in its `_meta` table. What disagrees is the illustration. The schema types a notification's `params` as optional, and the published examples of the three `list_changed` notifications print them with no `params` at all, so the samples show a message the prose forbids. Follow the requirement and include the field.

## 6. Caching with `ttlMs` and `cacheScope`

New in this revision, from SEP-2549 (Specification Enhancement Proposal 2549). Both fields are required, not optional, on the results that carry them. Condensed from the schema:

```typescript
export interface CacheableResult extends Result {
  ttlMs: number;                        // REQUIRED, minimum 0
  cacheScope: "public" | "private";     // REQUIRED
}
```

Six methods must include them on a `resultType: "complete"` result: `server/discover`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and `resources/read`. Three conspicuous absences: `prompts/get`, `tools/call`, and `completion/complete` are not cacheable and carry neither field.

`ttlMs` semantics are "analogous to HTTP `Cache-Control: max-age`". A positive value means the client **should** treat the result as fresh for that many milliseconds after receiving it, so the freshness test is `now < received_at + ttlMs`. Zero means immediately stale. A missing value means assume zero, and a negative value means ignore it and assume zero. Time to live (TTL) is a freshness hint and not a guarantee: servers may change the data before it expires.

The rule people break is the next one. Clients **should not** treat the TTL as a polling interval that triggers background refetches, and any implementation that does poll **must** apply jitter and backoff. A TTL is permission to skip a request, not an instruction to make one.

`cacheScope` is the security-relevant half:

| Value | Meaning |
|---|---|
| `"public"` | No user-specific data. Any client, gateway, or caching proxy may serve it to any user. |
| `"private"` | Reusable only within the same authorization context. Caches **must not** be shared across authorization contexts. |

The specification attaches a warning that is worth reading twice: servers **must** be aware that a `"public"` response may be shared between callers even when it came from an authenticated endpoint, and servers **must not** rely on `cacheScope` alone to prevent unauthorized access. Access control is per primitive and lives in the server's code. Marking a per-user resource `"public"` is a data leak with a two-word diff.

The cache key is the method plus the parameters that affect the result, the `uri` for a read or the `cursor` for a list page, and a client **must not** serve a cached response for a request whose method or parameters differ. A notification invalidates a fresh cached response immediately, which is why sections 5 and 6 belong together: the TTL bounds how stale a client can get, and a subscription cuts it short when something actually changes. Results from an MRTR retry are never cacheable at all.

**What `mcp==2.3.0` gives a server.** The `@mcp.resource` decorator accepts `name`, `title`, `description`, `mime_type`, `icons`, `annotations`, `meta`, and `security`, and nothing about caching. By default every cacheable result says zero and private, which is what `snippets/resource_surface.py` printed under each list and read of the first server:

```
ttlMs=0 cacheScope='private' nextCursor=None
```

Zero and private is the conservative answer. A real TTL is set per method, on the server: `MCPServer(cache_hints={...})` takes a `CacheHint(ttl_ms=..., scope=...)` for any of the six cacheable methods, and refuses any other method by name. `snippets/listen_and_cache.py` builds the notes server again with a hint for `resources/read` only:

```
default:     resources/list ttlMs=0 'private'; resources/read ttlMs=0 'private'
cache_hints: resources/list ttlMs=0 'private'; resources/read ttlMs=60000 'public'
cache_hints for prompts/get: ValueError: cache_hints keys must be cacheable methods (see CacheableMethod); got: 'prompts/get'
```

The hint covers every read the server answers, whichever resource it names. A different TTL for each resource is not something the high-level server exposes; it means building the `ReadResourceResult` in an `on_read_resource` handler on the low-level `Server`, where a field the handler sets itself is left alone by the hint.

## 7. Pagination, and what the high-level server does not do

Pagination in MCP is opaque-cursor based, on exactly four operations: `resources/list`, `resources/templates/list`, `prompts/list`, and `tools/list`. A request may carry `cursor`, a result may carry `nextCursor`, and page size is the server's business.

Clients **must** treat cursors as opaque. The specification spells out the trap: "Don't make any determination based on cursor value other than whether a non-null value was provided (e.g. **an empty string is a valid cursor and thus MUST NOT be treated as the end of results**)." A missing `nextCursor` means the end. An invalid cursor **should** produce `-32602`.

Pages and section 6 interlock in three specific ways, and all three are easy to get wrong. Each page is independently cacheable and runs its own `ttlMs` clock, and a server **may** give different pages different values, so there is no cross-page consistency guarantee at all; a client that needs a coherent snapshot **should** re-fetch from the first page rather than trust the set it assembled. `cacheScope` is the opposite: a server **must** apply the same one to every page of a given list request, so a catalog cannot be public on page one and private on page two. And when a cursor comes back invalid, the client **should** discard the pages it already cached instead of stitching them onto a fresh run.

Now the part to be plain about. **On the high-level Python server, pagination does not happen.** Every list handler on `MCPServer` ignores the cursor and returns the entire set with no `nextCursor`. `snippets/resource_surface.py` sends a `resources/list` with a cursor the server never issued:

```
== resources/list with cursor 'page-2' ==
1 resource(s); ttlMs=0 cacheScope='private' nextCursor=None
```

The whole list, no next page, and no `-32602` for a cursor that cannot be valid. So a server that registers five thousand resources with the decorator would answer every `resources/list` with all five thousand in one result, whatever cursor the client sent. That is legal, since page size is server-determined and one page is a page. It is also a surprise waiting for whoever operates it. A large resource set needs either a list handler written on the low-level `Server`, whose `on_list_resources` receives the `PaginatedRequestParams` with the cursor in them, or a template with a small list behind it, which is what `system://disk/{disk}` does here.

The client side is complete: `list_tools`, `list_resources`, `list_resource_templates`, and `list_prompts` all accept `cursor=`, so a client written against a paginating server works properly. Only the server-side high-level API has the gap.

## 8. Prompts as user-invoked entry points

A prompt is the primitive a person triggers. In practice it is the slash command, the "starter" button, the menu entry. The host lists them, the user picks one, the server expands it into messages, and the conversation begins with those messages already in it.

Condensed from the schema:

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

Fetching one is `prompts/get` with a name and arguments, here as `snippets/client_requests.py` prints the client's request:

```
{
  "method": "prompts/get",
  "params": {
    "name": "diagnose_performance",
    "arguments": {
      "symptom": "high fan noise"
    }
  }
}
```

and the result is typed:

```typescript
export interface GetPromptResult extends Result {
  description?: string;
  messages: PromptMessage[];
}
export interface PromptMessage { role: Role; content: ContentBlock; }
export type Role = "user" | "assistant";
```

`Role` has two values. **There is no system role in an MCP prompt.** Framing that would go in a system message belongs in the text of the first user message, or in the server's tool and resource descriptions, and the host decides what to do with it.

Two more constraints are the ones that catch people.

**Prompt arguments are strings, and only strings.** The wire type is `arguments?: { [key: string]: string }`. There is no schema, no integers, no booleans, no nested objects. A prompt that needs a number receives the digits and parses them. This is a much poorer argument model than a tool's JSON Schema, and deliberately so, because the thing filling it in is a text field in a user interface.

**`GetPromptResult` extends plain `Result`, not `CacheableResult`.** No `ttlMs`, no `cacheScope`. A prompt expansion is never cached, which is precisely what lets a prompt embed a live reading. From [code/05-first-server/src/system_info/resources.py](../../code/05-first-server/src/system_info/resources.py):

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

That is the difference between a prompt and a saved text snippet: **a prompt is a template that runs code.** The user picks "Diagnose slow machine" and the conversation opens with the current central processing unit (CPU) and memory figures already in the first message, plus an instruction telling the model which tools exist. No tool call was needed to get there, and the model did not have to decide to look.

Registration is `@mcp.prompt()`, the handler may return a string, a `Message`, a dict, a content block, or a sequence of those, and a bare string becomes a `UserMessage`. `snippets/resource_surface.py` prints the entry `prompts/list` carries, where the parameter's default has become `"required": false`:

```
{"name": "diagnose_performance", "title": "Diagnose slow machine", "description": "Start an investigation into why this machine feels slow.", "arguments": [{"name": "symptom", "required": false}]}
```

and `snippets/completion_and_prompt.py` expands it:

```
description: 'Start an investigation into why this machine feels slow.'; 1 message(s)
role 'user', content type 'text'
first line: 'This machine is showing high fan noise.'
result carries ttlMs: False
```

The project's test asserts both halves of what the handler promises, the caller's symptom and the live reading. From [code/05-first-server/tests/test_server.py](../../code/05-first-server/tests/test_server.py):

```python
async def test_prompt_embeds_a_live_reading():
    async with Client(mcp) as c:
        result = await c.get_prompt(
            "diagnose_performance", {"symptom": "high fan noise"}
        )
        text = result.messages[0].content.text
        assert "high fan noise" in text
        assert "CPU" in text
```

Three closing notes. `prompts/get` participates in MRTR, so a prompt may return `input_required` and be retried, exactly like a tool call. The SDK's `Resolve` mechanism from [post 08](../08-elicitation-and-mrtr/index.md) is documented for tools only and is not read on prompts or resources, so a prompt that needs to ask the user something does so through the raw MRTR types. And the specification's Prompts page gives prompt errors the resource rules: an unknown name or a missing required argument is `-32602`, an internal failure is `-32603`. The 2.3.0 SDK does not yet follow the first half, as section 12 prints.

## 9. Titles, icons, and annotations

Three small fields decide how a server's primitives look in somebody else's interface.

**`name` against `title`.** `name` is the identifier; `title` is for humans and may contain spaces and punctuation. For resources, templates, and prompts the display precedence is simply `title` and then `name`. `Tool` is the one exception in the whole schema, because it also consults `annotations.title` in between, which [post 06](../06-tools-in-depth/index.md) covers.

**`icons`.** An array of `{ src, mimeType, sizes, theme }` objects, all but `src` optional. The specification's rules apply to a client that renders icons; nothing requires a host to render them, and a host that does not is still conformant.

**`annotations`.** Shared by resources, resource templates, and every content block:

| Field | Type | What it says |
|---|---|---|
| `audience` | array of `"user"` and `"assistant"` | Who this is meant for. |
| `priority` | number, 0.0 to 1.0 | 1 means effectively required, 0 means entirely optional. |
| `lastModified` | ISO 8601 timestamp (International Organization for Standardization) | For example `"2025-01-12T15:00:58Z"`. |

`audience: ["user"]` on a resource is how a server tells a host that a document is for a person to look at rather than for the model's context window, and `priority` is how it ranks several resources a host might attach. Both are hints a client may use to decide what to display or include. Like the tool annotations in [post 06](../06-tools-in-depth/index.md), nothing enforces them, and a client is free to ignore every one.

`size` belongs in the same conversation. It is "the size of the raw resource content, in bytes (i.e., before base64 encoding or any tokenization), if known", and the specification's stated purpose is that hosts can display file sizes and estimate context window usage. Setting it on a large resource is a kindness to whoever has to budget tokens.

## 10. A decision table: tool, resource, or prompt

First, what the protocol actually gives each one. Every row below is a mechanical fact from the schema, not a preference:

| | Tools | Resources | Prompts |
|---|---|---|---|
| Who pulls the trigger | the model | the application | the user |
| The specification's word | model-controlled | application-driven | user-controlled |
| Read with | `tools/call` | `resources/read` | `prompts/get` |
| Addressed by | name | URI | name |
| Arguments | JSON Schema, any type | URI template variables | named, strings only |
| Is the read itself cacheable | no, `tools/call` | **yes**, `resources/read` | no, `prompts/get` |
| Per-item change notification | no | **yes**, `resourceSubscriptions` | no |
| Argument auto-completion | no | **yes**, `ref/resource` | **yes**, `ref/prompt` |
| Paginated list method | yes | yes | yes |
| Participates in MRTR | yes | yes | yes |

Then the question a server author is actually asking, which is about a specific thing about to be built:

| What the server has | The primitive | Why |
|---|---|---|
| The text of a document the user picked in the host's interface | **resource** | The decision is already made. Routing it through the model adds a guess. |
| Full-text search across ten million documents | **tool** | The query is the model's to invent, and the result is not addressable in advance. |
| The current on-call rotation, read-only, refreshed hourly | **resource** | Cacheable with a real TTL, and subscribable when it changes. |
| One report per customer id, thousands of customers | **resource template** | The id is a URI variable, and a completion handler turns it into a picker. |
| Restart a pod | **tool** | It changes the world, and the model must be able to reach it mid-task. |
| "Review this pull request" as a slash command | **prompt** | The user chose to start it; the server supplies the framing and the checklist. |
| An incident triage opener with live metrics attached | **prompt** | The handler runs code, so the first message arrives with the numbers already in it. |
| A 40 megabyte (MB) log file the model must not read whole | **tool returning a resource link** | Return a summary plus a `resource_link` block, and let the host fetch the bulk. |

**The thesis, stated plainly.** Using a tool where a resource belongs is an easy design mistake, because it is the path of least resistance. `@mcp.tool()` reaches the model in any host that runs a tool loop. A resource needs the host to have built a surface for it, and the specification does not require one. So `get_config` ships as a tool, and the model spends a call fetching something nobody needed it to think about, uncacheable, unsubscribable, uncompletable.

The repair is not ideological. Expose the data as a resource, because that is what it is, and if the model genuinely also needs to reach it during a turn, add a thin tool that returns a `resource_link` content block pointing at the same URI. One definition of the data, two doors, and the host picks whichever it can render.

Two closing rules apply to all three primitives, and both are easy to violate by accident. The set of resources, templates, and prompts a server exposes **must not** vary per connection or as a side effect of another request on that connection, although it **may** vary by the authorization presented on the request. Filtering by credential is fine; a `connect_project` tool that makes new resources appear is not. And the entire surface of a server, resource descriptions included, enters a model's context before anything is called, so the same tool poisoning concerns [post 19](../19-security/index.md) raises for tool descriptions apply word for word to resource and prompt descriptions.

## 11. Make it run: the project and the scripts behind this post

The project is the post's code. From inside `code/05-first-server/`:

```bash
uv sync --extra dev                         # once: the environment, with the test tools
uv run --extra dev pytest                   # 19 passed, the resource, prompt and completion tests among them
uv run python -m system_info                # stdio, what a desktop host spawns; Ctrl+C stops it
```

Six scripts under `snippets/` print every transcript quoted above. Each runs from the series root with no arguments, in a few seconds, through the root environment that pins `mcp[cli]==2.3.0` and `psutil`. Four of them import the project under `code/05-first-server/` and connect an in-memory client to it, as the project's tests do; the other two build throwaway servers in the same process, and two of the four add a throwaway server of their own. None starts a subprocess, opens a socket, or needs a host.

```bash
uv run python posts/07-resources-and-prompts/snippets/resource_surface.py      # sections 2, 6, 7, 8: capabilities, the three lists, a cursor, two reads
uv run python posts/07-resources-and-prompts/snippets/registration_errors.py   # sections 3 and 12: three working URI shapes, six refused ones
uv run python posts/07-resources-and-prompts/snippets/template_refusals.py     # sections 3 and 12: four hostile reads and the layer that stopped each
uv run python posts/07-resources-and-prompts/snippets/client_requests.py       # sections 4, 5, 8: the params the client sends
uv run python posts/07-resources-and-prompts/snippets/completion_and_prompt.py # sections 4, 8, 12: completions, the prompt, prompt errors
uv run python posts/07-resources-and-prompts/snippets/listen_and_cache.py      # sections 5, 6, 12: a listen stream, cache_hints, subscriptions=False
```

`client_requests.py` reads the outgoing params by wrapping a private method of the SDK's in-memory dispatcher, a seam that exists in `mcp==2.3.0` and may move in a later release; it only observes.

The disk figures and the process table come from the machine the script runs on, so those values differ from run to run; nothing quoted above depends on them. The server logs each refused read at INFO on standard error, and the scripts that provoke failures keep those lines out of standard output.

## 12. What can go wrong?

Five mistakes are easy to make with this post's code, and none of them is obvious from the code alone. Each one below is printed by a script in `snippets/`.

**A handler that does not fit its URI.** Every check on a resource runs when the decorator does, so the server fails at import instead of starting. `snippets/registration_errors.py` registers six broken resources:

```
static_with_parameter:
    ValueError: Resource 'static://y' has no URI template variables, but the handler declares parameters {'a'}. Add matching {...} variables to the URI or remove the parameters.
static_with_context:
    ValueError: Resource 'static://x' has no URI template variables, but the handler declares a Context parameter. Context injection for static resources is not supported. Add a template variable to the URI or remove the Context parameter.
names_disagree:
    ValueError: Mismatch between URI parameters {'city'} and function parameters {'town'}
query_without_default:
    ValueError: Resource 'notes://{name}{?b}': query parameter(s) ['b'] have no default value. A client may omit a {?...}/{&...} query parameter, so the matching handler parameter must declare a default.
adjacent_variables:
    InvalidUriTemplate: Variables 'a' and 'b' are adjacent with no literal separator; matching cannot determine where one ends and the other begins. Add a literal between them or use a single variable.
no_parentheses:
    TypeError: The @resource decorator was used incorrectly. Did you forget to call it? Use @resource('uri') instead of @resource
```

The second one surprises people arriving from the tool decorator: context injection on resources is template-only, so a static resource can neither log through `Context` nor publish a notification, and that work belongs in a tool.

**Expecting the specification's error code from a prompt.** The Prompts page says an unknown prompt name or a missing required argument is `-32602`. Under `mcp==2.3.0` both come back as an internal error with the message withheld, as `snippets/completion_and_prompt.py` prints against a prompt whose one argument is required:

```
get_prompt('review', {}): error -32603 'Internal server error'
get_prompt('no_such_prompt', {}): error -32603 'Internal server error'
```

This is an SDK lag rather than a protocol rule: the SDK reports the failure as a generic exception, which it maps to `-32603` on every transport. A client that branches on `-32602` to show "check the arguments" never takes that branch against this SDK, and the reason sits only in the server's log.

**Passing a prompt argument that is not a string.** The Python client checks the wire type before anything is sent:

```
get_prompt('review', {'code': 42}): refused by the client before sending: ('arguments', 'code') Input should be a valid string
```

The fix is `{"code": "42"}` and a parse inside the handler, with the expected format in the argument's description.

**Listening where nothing is served.** A server built with `MCPServer(subscriptions=False)` declares neither resource flag and refuses the method, and the high-level server never serves the pre-2026 `resources/subscribe`. `snippets/listen_and_cache.py` prints both:

```
== a pre-2026 resources/subscribe call ==
error -32601 'Method not found'

== MCPServer(subscriptions=False) ==
resources capability: subscribe=False listChanged=False
subscriptions/listen: error -32601 'Method not found'
```

`-32601` (`Method not found`) on `subscriptions/listen` means the server opted out, and the capability flags already said so; on `resources/subscribe` it means the client is speaking an older revision. A server that must serve such a client registers `on_subscribe_resource` on the low-level `Server`.

**Catching the refusal in the wrong place.** Inside the `async with Client(...)` block a refused read raises `MCPError`. Once it escapes the block, the client's task groups have wrapped it, and an `except MCPError` outside never fires. The fourth part of `snippets/template_refusals.py` lets it escape:

```
caught as ExceptionGroup; 2 group level(s) down: MCPError -32602 "Unknown disk 'etc'. Known disks: root."
```

Catch `MCPError` inside the block, next to the call that raised it, or use `except*` outside.

## Common pitfalls

1. **Shipping a read as a tool.** Data with a stable identity wants a URI; a tool version of the same read cannot be cached, subscribed to, completed, or attached by a user who already knows they want it.
2. **Trusting the matcher or the SDK's default policy as the path check.** `system://disk/../../etc/passwd` fails to match only because of the template's shape, and the percent-encoded form is stopped by a policy one keyword argument can switch off; the allowlist inside the handler is the defense.
3. **Assuming `cursor` does something.** The high-level server ignores it and returns everything with no `nextCursor`, so real pages need a list handler on the low-level `Server`, and documentation should not promise pagination the server does not implement.
4. **Expecting the SDK to filter completions.** It returns whatever the handler returns; write the prefix match, and filter by the caller's authorization too, because a completion handler is an enumeration surface.
5. **Waiting for notifications that were never requested.** There is no broadcast: `notifications/resources/updated` reaches only a client whose open `subscriptions/listen` stream names that URI in `resourceSubscriptions`.
6. **Answering a missing resource with an empty `contents` array.** The specification forbids it as ambiguous; raise `ResourceNotFoundError` and let the client see `-32602`.

## Further reading

- Specification, *"Resources"*, revision 2026-07-28. The `Resource` object, the capability flags, the `-32602` rule, the empty-contents prohibition, and the URI scheme and security requirements quoted in sections 2 and 3. <https://modelcontextprotocol.io/specification/2026-07-28/server/resources>
- Specification, *"Prompts"*, revision 2026-07-28. The `Prompt` and `PromptArgument` objects, string-only arguments, and the prompt error codes in section 8. <https://modelcontextprotocol.io/specification/2026-07-28/server/prompts>
- Specification, *"Completion"*, revision 2026-07-28. The two reference types, the 100-item cap, and the `context.arguments` field. <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/completion>
- Specification, *"Pagination"*, revision 2026-07-28. Opaque cursors, the four paginated operations, and the empty-string-is-a-valid-cursor rule. <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination>
- Specification, *"Caching"*, revision 2026-07-28, and SEP-2549. The six cacheable methods, the TTL semantics, and the `cacheScope` security warning in section 6. <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching>
- Specification, *"Subscriptions"*, revision 2026-07-28. The filter fields, the acknowledgment ordering, the subscription id, and the statement that this replaces `resources/subscribe`. <https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/subscriptions>
- RFC 6570, *URI Template*. <https://www.rfc-editor.org/rfc/rfc6570>
- RFC 3986, *Uniform Resource Identifier (URI): Generic Syntax*, for custom schemes. <https://www.rfc-editor.org/rfc/rfc3986>
- MCP Python SDK, release 2.3.0. `@mcp.resource`, `@mcp.prompt()`, `@mcp.completion()`, `ResourceSecurity`, `cache_hints`, and `Client.listen`. <https://github.com/modelcontextprotocol/python-sdk>
- [code/05-first-server/](../../code/05-first-server/). The complete project, with the tests quoted in sections 3, 4, and 8.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 08 - Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md):** the other direction of the same question; here the user pulls the trigger before the work starts, there the server stops halfway through and asks.
- **[Post 13 - Project 1: A secure database analyst](../13-database-analyst/index.md):** the decision table applied to a real server, with the database schema exposed as a resource beside the query tool.
