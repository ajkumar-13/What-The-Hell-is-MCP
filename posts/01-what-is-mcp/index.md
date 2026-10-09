# 01 - What MCP is, and the problem it solves

> **TL;DR.** Connecting $N$ artificial intelligence (AI) applications to $M$ data sources by hand costs $N \times M$ bespoke integrations, and the Model Context Protocol (MCP) turns that into $N + M$ implementations by putting one agreed boundary in the middle. A server publishes tools, resources, and prompts across that boundary, and any host that speaks MCP can use them without knowing how the server is built. MCP standardizes access and only access: it adds no intelligence, defines no privacy boundary, and enforces no safety, and it is the host, not the server, that asks the user before a sensitive tool runs. This series teaches protocol revision 2026-07-28 and nothing else.
>
> **Prerequisites:** none. This is the first post of the series.
> **Safe to skip?** Skip it if the reader can already count the integrations a protocol saves, has installed a published server into a host and seen its permission prompt, and can say what MCP leaves unsolved.
>
> **After reading, you will be able to:**
>
> - Count the integrations a set of AI applications and data sources costs, with and without a shared protocol.
> - Install a published server from the registry into a host and recognize the permission prompt when it appears.
> - Name the three MCP roles, host, client, and server, and say which one owns the consent decision.
> - State what MCP leaves unsolved, from tool choice and data privacy to the enforcement of safety.

![Four AI applications on the left joined to six data sources on the right by twenty-four separate crossing lines.](diagrams/01-n-times-m.svg)

*Every line is its own plugin format, its own credentials, and its own person to page when it breaks. Add a fifth application and six more lines appear.*

---

## 1. The question: why is the model short of access, not reasoning?

A service is failing. An engineer describes the symptom to an AI assistant, and it comes back with a sensible list of things to check. Then it asks for the logs.

So the engineer opens a terminal, tails the log, selects four hundred lines, and pastes them into the chat window. The model reads them and asks what the connection pool looks like. A second terminal, a query, a copy, a paste. It asks which deploy went out this morning. A browser tab, the commit, another paste.

Nothing in that loop is reasoning. It is a person moving bytes by hand between the systems that hold the data and a model that could use it. The person is the integration, and a slow one.

The same shape shows up everywhere. A coding agent that cannot read the issue tracker. A support bot that cannot look up an order. A terminal assistant that will happily write the query but cannot run it. In every case the model is not short of reasoning. It is short of access.

The fix looks obvious from one angle: give the application a way to fetch the log itself. That is an integration. Integrations are where the interesting problem starts, and the problem is arithmetic. This post asks what one protocol boundary between AI applications and data sources solves, and what it leaves unsolved.

## 2. The $N \times M$ integration problem, counted

Picture a small team. They use four AI applications:

- a desktop chat assistant
- a coding agent inside the editor
- a terminal agent that runs in their build pipeline
- an internal support bot somebody on the team wrote

They would like those four to reach six systems: PostgreSQL, Slack, GitHub, Jira, the local filesystem, and an internal metrics service.

Wire them pair by pair, as the hero figure above does, and the bill is:

```text
4 applications × 6 data sources = 24 integrations
```

Twenty-four is not twenty-four afternoons of glue code. Each integration is a plugin format specific to that one application, an authentication scheme specific to that one system, a description of what the integration can do written so a model can understand when to use it, error handling, a release process, and a person who fixes it when the vendor changes an application programming interface (API).

The growth is the painful part, and it is easier seen than read. Every new item on one side multiplies by the size of the other side:

| What the team adds | New integrations owed | Running total |
|---|---|---|
| the starting point, four applications and six data sources | 24 | 24 |
| a fifth application | 6, one per data source | 30 |
| a seventh data source | 5, one per application | 35 |

In general, $N$ applications and $M$ data sources cost $N \times M$. One more application costs $M$, and one more data source costs $N$.

**In practice nobody pays that bill, and that is the real symptom.** A few popular pairs get built, by whoever can justify the work, and the rest never exist. So $N \times M$ rarely appears as a budget line. It appears as the set of things the team's tools cannot do, and the copy-paste loop in section 1 is what fills the gap.

Now put one agreed interface in the middle instead. Each application implements the protocol once. Each system gets one server, written once, that any of those applications can use.

```text
4 applications + 6 data sources = 10 implementations
```

Ten instead of twenty-four, and the marginal cost changes shape entirely. Starting again from four and six: the mesh charges six integrations for one more application and four for one more data source. Through the boundary, either one costs exactly one piece of work.

**Two honest qualifications.** First, $N + M$ counts implementations to write and maintain, not connections at run time. All twenty-four paths still exist when the team is working; they travel over one shared interface instead of twenty-four private ones. Second, a team almost never writes both sides. The application vendor writes its half once, and many of the $M$ already exist as open-source servers, so a team's own marginal cost is often one server or zero.

And the saving is conditional. A protocol that only one application speaks saves nothing at all: with $N = 1$ the mesh costs $M$ integrations and the boundary costs $M + 1$ implementations. The arithmetic works because enough hosts have adopted MCP that writing one server is a reasonable bet, not because $N + M$ is a smaller number on a whiteboard. For an application that does not speak MCP, the integration is still bespoke, and an honest estimate says so. The cheapest way to see the saving is to collect some of it, which is what the next section does.

## 3. Ten minutes with a server someone else wrote

The $M$ side of that arithmetic is not hypothetical. Servers for common systems already exist, published by other people, and installing one is meant to fit in about ten minutes with no code written. It is the fastest way to make MCP concrete.

![Three numbered steps across the top, find, configure, and restart, leading down to a large panel in which the host asks whether a tool call should go ahead, with allow and deny buttons.](diagrams/04-ten-minutes.svg)

*The whole of this section in one picture; the names in the cards are illustrative. The last panel is the one to watch: the host is asking, and that question is where the security story of this series begins.*

**Find one.** Published servers are indexed in the MCP Registry, which stores metadata and no code. An entry records what a server is called, under a name its publisher had to prove ownership of, and, when the entry gives it, where the package lives. The Registry can be searched directly:

```text
https://registry.modelcontextprotocol.io/v0.1/servers?search=postgres
```

Swap the search term for the system to reach; the documentation is at <https://registry.modelcontextprotocol.io/docs>. The Registry is a wholesale index rather than a shop front: its own documentation says it is intended to be consumed primarily by downstream aggregators, such as server marketplaces, and not directly by host applications. Searching it by hand works anyway, and it is the clearest way to see exactly what a listing does and does not say.

This series names no particular server package on purpose. Package names age faster than posts do, and finding a current one is the skill worth having.

**Read what an entry promises.** Three fields are required: a `name`, a `description` of at most 100 characters, and a `version`. Everything else, including the link back to the source repository, is optional and may be absent entirely.

The name is a reverse-domain string such as `io.github.someone/postgres`, and it is not the string that goes on a command line. That one lives in the entry's optional `packages` array, where each package names a `registryType`, the package index it came from, and an `identifier`, its name inside that index. The `identifier` is the string to copy. An entry that carries `remotes` instead of `packages` is a hosted server reached over a network rather than one installed locally, and the steps below do not apply to it.

**Then read what it does not promise.** The Registry is in preview, and its own moderation policy says it "does not make guarantees about moderation, and consumers should assume minimal-to-no moderation". A listing proves that somebody controls a name. It is not evidence that the code is safe, maintained, or what it claims to be.

So a server deserves the treatment any other dependency gets before it runs. If the entry names a source repository, read it. If it does not, that absence is itself information. [Post 19](../19-security/index.md) gives this its full treatment, including the fact that nothing pins the server that ends up running to the definitions that were originally approved.

**Put it in the host's configuration file.** The host is the application the reader already works in, such as a desktop assistant or an editor; section 5 names the three roles properly. Most hosts start a local server as an ordinary subprocess, a program the host launches and keeps running underneath itself, so configuring one means naming a command and its arguments. A published Python server is usually launched with `uvx <identifier>`, where `<identifier>` is the copied string. A package may carry a `runtimeHint` naming that command outright, but the field is optional, so when it is missing the `registryType` says which package index, and so which launcher, applies. For a Python package that means `uvx`, which ships with `uv`.

Finding the file is the step that catches people, because it is rarely where one would guess. Most hosts open their own file on request: Claude Desktop has **Settings > Developer > Edit Config**, and VS Code and Zed each expose theirs as a command-palette entry. A new entry goes alongside whatever the file already holds:

```json
{
  "mcpServers": {
    "orders": {
      "command": "uvx",
      "args": ["the-copied-identifier"]
    }
  }
}
```

Claude Desktop, Claude Code, and Cursor all use that `mcpServers` key, in `claude_desktop_config.json`, `.mcp.json`, and `.cursor/mcp.json` respectively. Others disagree. VS Code reads `.vscode/mcp.json`, its top-level key is `servers`, and a `type` field is required on every entry. Zed uses `context_servers`, and Gemini CLI, a command-line interface (CLI), uses `mcpServers` with no `type` field at all. [Post 23](../23-multi-client/index.md) has the full matrix, including where each file lives on macOS, Windows, and Linux.

The key, `orders` above, is the configuring person's to choose. It is the label the host shows for the server, and it is the prefix a host puts in front of a tool name when it qualifies names, for example when two servers offer the same one, so it should be short and recognizable.

**Restart, then use it.** Restart the host fully, because some hosts read the file only at launch. What the server offers then appears somewhere in the host's interface, and where depends on the host.

Now ask for something that server covers. The model reads the descriptions it was given, decides one of them fits, and the host stops to ask whether the call should go ahead. The specification's Tools page says clients **SHOULD** prompt for user confirmation on sensitive operations (a duty this series assigns to the host, for the reason section 5 gives) and that there **SHOULD** always be a human in the loop able to deny a tool call; it does not say **MUST**, so a host may be configured not to ask.

That question is the part worth noticing. It is the host asking, not the server, and it sits on the trust boundary the rest of this series keeps returning to. Section 5 explains who owns that decision and why it cannot live anywhere else, and section 6 names the three kinds of thing a server publishes, one of which has now been used. Section 10 lists what goes wrong along the way.

No integration written, and some of the $N + M$ saving of section 2 collected. From [post 05](../05-first-server/index.md) onward, this series builds the other side.

## 4. What a protocol boundary buys

![The same four applications and six data sources, each connected once to a single central panel labeled Model Context Protocol.](diagrams/02-one-boundary.svg)

*Compare the figure at the top of the post: same boxes, same positions. Only the middle changed, and twenty-four lines became ten.*

A protocol boundary is a written agreement about the shape of the messages that cross a line, precise enough that either side can be replaced without telling the other. MCP is that agreement for the line between an AI application and the systems it wants to reach. It fixes five things and deliberately leaves everything else alone:

1. How a client asks a server what it can do (`server/discover`, then `tools/list`).
2. How a client invokes one of those things (`tools/call`).
3. What a result looks like coming back.
4. How failures are reported.
5. How the bytes move, through one of two transports: standard input and output, called `stdio` throughout this series, for a local subprocess, or Streamable HTTP (Hypertext Transfer Protocol) for anything over a network.

Everything in MCP travels as JSON-RPC 2.0. A remote procedure call (RPC) is one program asking another to run a named operation and hand back the result, and the only genuinely hard part is agreeing on how the request and the result are written down. JSON-RPC 2.0 is that agreement, written in JavaScript Object Notation (JSON), and it is about as small as such agreements get: a method name, its parameters, and an id that matches the answer back to the question. Here is a tool call as the specification's Tools page prints it, with one omission the notes below the result explain:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "get_weather",
    "arguments": {
      "location": "New York"
    }
  }
}
```

And the answer, from the same page:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "resultType": "complete",
    "content": [
      {
        "type": "text",
        "text": "Current weather in New York:\nTemperature: 72°F\nConditions: Partly cloudy"
      }
    ],
    "isError": false
  }
}
```

Two things to flag, both of which get a full treatment in [post 03](../03-wire-protocol/index.md). The first is what the example leaves out for readability. A real request also carries a `_meta` object in its `params`, which is where the client writes down the protocol revision it is speaking and the optional parts of the protocol it supports. It goes on every request the client sends, because this revision has no opening exchange in which to say it once. The second is `resultType`, which this revision requires on every result. `"complete"` means the server finished the job, and a server can also answer that it needs more input, such as an answer from the user, before it can finish, which is what [post 08](../08-elicitation-and-mrtr/index.md) is about.

That is the entire boundary, and three useful properties fall out of it.

**Substitution.** Swap the desktop assistant for a different one and the server does not change. Swap PostgreSQL for something else behind the server and the applications do not change. The line in the middle is the only contract either side has to honor.

**Reach.** A server is written once per system, not once per application. This is the whole of the $N + M$ claim, expressed as engineering rather than arithmetic.

**Debuggability.** Because it is JSON over a documented transport, a failing call is reproducible by hand. A captured request that went wrong can be replayed: written into a local server's standard input, or sent with a command-line HTTP client when the server listens over the network, with no host and no model anywhere in the picture. That is worth more than it sounds on the day something breaks.

## 5. The three roles in one picture

![A host panel containing the user, the model, and a client, separated by a dashed trust boundary from a server panel, which reaches a data source outside MCP.](diagrams/03-three-roles.svg)

*The host owns the model and the consent decision, the client owns the connection, and the server owns what it publishes.*

MCP has exactly three roles, and confusing the first two muddles more MCP conversations than any other mistake in this series.

**Host.** The application the user actually interacts with: Claude Desktop, Cursor, VS Code, or a command-line tool someone writes. The host owns the model, owns the conversation, and owns every permission decision. When something asks whether a tool may run, that is the host talking.

**Client.** The protocol-speaking object living inside the host. There is exactly one client per connected server. It owns the transport, matches responses back to requests by id, and caches what the server said it offers. It is infrastructure. This series writes one by hand only in [post 10](../10-mcp-client/index.md), and most readers never need to.

**Server.** The process that publishes tools, resources, and prompts. Usually this is the reader's own code. Note what the word does *not* imply: a server is not necessarily remote and not necessarily a web service. A host will often spawn a server as an ordinary subprocess on the same machine and talk to it over a pipe.

Behind the server sits the actual data source, and it is outside MCP entirely. The protocol has no opinion about a database driver, file paths, or API keys. It only cares about what the server chooses to publish.

The dashed line in the figure is the trust boundary, and it is worth being blunt about which way it faces. Everything a server sends back, including the descriptions of its own tools, is untrusted input as far as the host is concerned. The specification's Tools page says there **SHOULD** always be a human in the loop with the ability to deny a tool call, and that clients **MUST** consider tool annotations untrusted unless they come from trusted servers. The host, not the server, is the security boundary. [Post 19](../19-security/index.md) is entirely about what happens when this is forgotten.

One vocabulary note that saves confusion. In casual conversation, and in a great deal of documentation, "MCP client" is used loosely to mean the whole application. In this series it never does. Host is the application, client is the connection object inside it, and the two words are never swapped.

## 6. What a server exposes: tools, resources, and prompts

A server publishes three kinds of thing, called primitives throughout this series and in the table below. They differ mainly in who decides to use them.

| Primitive | For | Who pulls the trigger | Covered in |
|---|---|---|---|
| **Tools** | doing something | the model | [post 06](../06-tools-in-depth/index.md) |
| **Resources** | reading something | the application | [post 07](../07-resources-and-prompts/index.md) |
| **Prompts** | starting something | the user | [post 07](../07-resources-and-prompts/index.md) |

**Tools** are functions with a name, a description, and a JSON Schema describing their arguments. The specification calls them model-controlled: the model reads the descriptions and decides when a tool is worth calling. `get_weather`, `run_query`, `create_issue`.

**Resources** are readable things addressed by a Uniform Resource Identifier (URI), such as `file:///var/log/app.log` or a database schema. The specification calls them application-driven: the application, not the model, decides which resources to attach to the conversation, which is a different control flow from a tool call even though both end up as text in front of a model.

**Prompts** are named, reusable entry points that a user picks deliberately, usually from a menu or a slash command, which is why the specification calls them user-controlled. They expand into a message with context already attached. Picking "Diagnose slow machine" can open the conversation with a live reading of the machine already in it, so the user never types the question and the model never has to ask for the data.

That is as far as this post goes. The distinction between the three, and the design mistake of turning everything into a tool because tools are the easiest to write, is the subject of [post 06](../06-tools-in-depth/index.md) and [post 07](../07-resources-and-prompts/index.md).

## 7. What MCP explicitly does not do

Three disclaimers, all of which matter more than most introductions admit.

**It adds no intelligence.** The protocol carries a name, some arguments, and a result. Nothing in it decides which tool to call, or notices that calling it was a bad idea. If the model picks the wrong tool, MCP has no opinion. This is why a tool's description and schema are so consequential: they are the entire user interface the model gets, and a badly written description is a bug that shows up as "the model never uses my tool".

**It defines no privacy boundary.** This one deserves care, because the word "server" misleads people in both directions. A server is a role, not a location. Many MCP servers run as a subprocess on the user's own laptop, holding the user's own credentials, reading the user's own files, with no third party in the path at all. That is genuinely useful and it is the normal setup for local development.

But a local server does not imply a local model. If the host is talking to a hosted model, then everything a tool returns that the host puts into the conversation is sent to that model provider like any other message. MCP standardizes how the data is *reached*. Whether the data leaves the machine is decided by the host and its model configuration, and the protocol has nothing to say about it.

**It enforces no safety.** A tool can declare itself read-only. Nothing checks. Annotations are hints for the host's benefit, and the specification says plainly that clients must treat them as untrusted unless they come from trusted servers. The protocol will not stop a server from deleting a file when a tool named `list_files` is called, will not stop a server from changing its tool descriptions after a user approved them, and gives no way to verify that a server is the code it claims to be. Those are real problems with real answers, and the answers live in server code and in the host's permission model rather than in the wire format.

## 8. Where the protocol came from, and who owns it now

Anthropic open-sourced MCP in November 2024, as the Linux Foundation's announcement of the Agentic AI Foundation recalls. In December 2025 MCP became a founding project of that foundation, which the MCP announcement describes as "a directed fund under the Linux Foundation", alongside Block's goose and OpenAI's AGENTS.md. The foundation was co-founded by Anthropic, Block, and OpenAI, with support from Google, Microsoft, Amazon Web Services, Cloudflare, and Bloomberg. That roster is the part of the announcement worth keeping. Several of those companies ship hosts named elsewhere in this post, so the vendors whose applications a server would want to appear inside are also the ones underwriting the standard it speaks, which is exactly the condition section 2 said the $N + M$ saving depends on.

The announcement is careful to say that the day-to-day did not change: "For MCP little changes. The governance model we introduced earlier this year continues as is." What the move does buy is a neutral home. The specification is not one company's roadmap, and it changes through a public proposal process. Those proposals are called Specification Enhancement Proposals (SEPs), they are numbered, and this series cites them by number when explaining why something is the way it is.

Revisions are named by date rather than by version number. **This series targets 2026-07-28 and only 2026-07-28.** That matters for a specific reason: as the 2026-07-28 changelog lists, this revision made the protocol stateless. The `initialize` handshake is gone, protocol sessions and the `Mcp-Session-Id` header are gone, and there is no longer a channel for a server to send a request back to a client. So a tutorial written against an earlier revision teaches a handshake that no longer exists and a session identifier that no server of this revision honors, and it looks correct while doing it. The date on anything written about MCP, this series included, is worth checking.

## 9. How to read this series

Twenty-four posts in six parts. Read them in order if MCP is new; each part also stands alone.

| Part | Posts | What it gives the reader |
|---|---|---|
| **I. Foundations** | 01 to 04 | The mental model, the wire format, and the two transports. Posts 01 and 02 are code-free; 03 and 04 add short scripts that read the wire directly. |
| **II. Building servers** | 05 to 09 | A working server, then tools, resources, prompts, mid-call questions, and long-running work. |
| **III. Clients, hosts, and testing** | 10 to 12 | The other side of the boundary: write a client, write a host, and test both. |
| **IV. Projects** | 13 to 18 | Three real servers built end to end: a database analyst, a DevOps responder, a research browser. |
| **V. Production** | 19 to 22 | The attacks the protocol does not stop, authorization with OAuth 2.1, deployment, and publishing. |
| **VI. Interoperability and the frontier** | 23 and 24 | One server configured in six hosts, then extensions and MCP Apps. |

**Two ways through Part I.** [Post 03](../03-wire-protocol/index.md) and [post 04](../04-transports/index.md) are the longest in Part I, and both are reference-shaped: the wire format field by field, then the transports header by header. A reader who likes firm ground before building reads them in order and continues to [post 05](../05-first-server/index.md).

A reader who would rather have something running first treats section 3 above as the warm-up, reads [post 02](../02-architecture/index.md) for the three roles, then goes straight to [post 05](../05-first-server/index.md) and writes a server, coming back to post 03 and post 04 to learn what the library was doing behind the scenes, which is usually the first time something breaks. Neither route skips anything. They differ only in when the wire format arrives.

For a server already written against an older revision, the posts that change its code are 03, 07, 08, and 09: the handshake and the session went in post 03, `resources/subscribe` became `subscriptions/listen` in post 07, the server-to-client back channel became a return value in post 08, and tasks moved out of the core protocol in post 09.

Two things are pinned throughout, and both are named here so they can be checked against whatever is installed. The protocol revision is 2026-07-28. The Python software development kit (SDK) is `mcp==2.3.0`; every project pins the exact version it was tested against, and where the SDK and the specification disagree, the post says so rather than guessing.

## 10. What can go wrong?

These are the mistakes the ten minutes of section 3 actually produce. Each host words its messages differently and changes them between releases, so the symptoms are described rather than quoted.

- **The registry `name` goes on the command line.** `uvx io.github.someone/postgres` is not a package name the launcher can resolve, so the server never starts and the host shows it as failed or shows no tools from it. The string to copy is the package's `identifier`.
- **A file that no longer parses.** Pasting a whole example, outer braces included, into an existing file, or dropping a comma or a brace while merging, breaks the JSON. The usual symptom is no servers at all, including the ones that worked yesterday, rather than a complaint about the entry just added.
- **The wrong top-level key for the host.** An `mcpServers` block pasted into VS Code's `.vscode/mcp.json` is not an error to VS Code: it finds no `servers` key and reports no servers configured. The key belongs to the host, not to MCP, and section 3 lists the ones that differ.
- **No launcher installed.** If `uv` is not installed, the host cannot run `uvx` and the server fails to start before any MCP message is sent. The fix is to install `uv`, not to change the entry.
- **A half restart.** On some hosts closing the window leaves the application running, and a host that reads its file only at launch shows nothing new. Quit the host fully and start it again.
- **A hosted entry configured as a local one.** An entry with `remotes` and no `packages` has nothing to launch; it is a Uniform Resource Locator (URL) to connect to, and the `command` form does not apply.
- **No permission prompt, read as no tool call.** The prompt is a **SHOULD**, and a host may let a user approve a tool permanently or turn prompting off. Before concluding nothing ran, look for that setting and for the tool call in the host's transcript.

## Common pitfalls

1. **Calling the host "the client".** The host is the application; the client is the connection object inside it. When a document says "the client asks the user", it usually means the host.
2. **Assuming a local server keeps the data local.** It keeps the *access* local; if the host uses a hosted model, tool output that reaches the conversation reaches the provider.
3. **Expecting MCP to improve tool selection.** If the model calls the wrong tool, the fix lives in the tool's name, description, or schema, because the protocol has no layer that could help.
4. **Following a tutorial without checking its date.** Anything that opens with an `initialize` handshake, an `Mcp-Session-Id` header, or a server calling `sampling/createMessage` is written against a revision before 2026-07-28 and will not work against a server that speaks only this revision.
5. **Treating annotations such as `readOnlyHint` as enforcement.** They are hints from a party the host is told not to trust; read-only belongs in the server and in its database grants.
6. **Reaching for a tool when the answer is a resource.** Exposing everything as a tool because tools are the easiest primitive to write fills the model's context with things that should have been readable on demand.

## Further reading

- Model Context Protocol, [*Versioning and revision index*](https://modelcontextprotocol.io/specification/versioning) (2026). While 2026-07-28 was a release candidate its pages were served from `/specification/draft/`, so check both paths if a deep link fails.
- Model Context Protocol, [*2026-07-28 changelog*](https://modelcontextprotocol.io/specification/2026-07-28/changelog) (2026). The authoritative list of what this revision removed.
- Model Context Protocol, [*Tools*](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), revision 2026-07-28. Source of the `tools/call` request and result in section 4 and of the confirmation, human-in-the-loop, and annotation rules in sections 3 and 5.
- Model Context Protocol, [*Registry moderation policy*](https://modelcontextprotocol.io/registry/moderation-policy). Source of the "minimal-to-no moderation" line quoted in section 3.
- [Registry documentation](https://registry.modelcontextprotocol.io/docs). What a listing records, and the search address section 3 uses.
- Model Context Protocol, [*MCP joins the Agentic AI Foundation*](https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/) (2025). The announcement quoted in section 8.
- Linux Foundation, [*Formation of the Agentic AI Foundation*](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) (2025). The founding projects and members, and the November 2024 open-sourcing of MCP.
- [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification). The entire wire grammar MCP is built on, and short enough to read in one sitting.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 02 - The architecture: hosts, clients, and servers](../02-architecture/index.md):** the three roles at full depth, including how a host turns many servers into one catalog of tools and what happens when two servers claim the same tool name.
- **[Post 05 - Your first MCP server](../05-first-server/index.md):** the other side of the boundary this post installed a server across, written from a blank file and connected to the same host.
