# 01 · What MCP is, and the problem it solves

> **TL;DR.** Connecting *N* artificial intelligence (AI) applications to *M* data sources by hand costs *N* × *M* bespoke integrations, and the Model Context Protocol (MCP) collapses that into *N* + *M* by putting one agreed boundary in the middle. A server publishes tools, resources, and prompts across that boundary, and any host that speaks MCP can use them without knowing anything about how the server is built. MCP standardizes access and only access: it adds no intelligence and it defines no privacy boundary. This series teaches protocol revision 2026-07-28 and nothing else.
>
> **After reading this you will be able to:**
> - Count the integrations a given set of applications and data sources costs, with and without a shared protocol.
> - Install a published server into a host you already use, and recognize the permission prompt when it fires.
> - Name the three MCP roles, host, client, and server, and say which one owns the consent decision.
> - State what MCP does not give you, so you can plan the parts you still have to build yourself.

![Four AI applications on the left joined to six data sources on the right by twenty-four separate crossing lines.](diagrams/01-n-times-m.svg) *Every line is its own plugin format, its own credentials, and its own person to page when it breaks. Add a fifth application and six more lines appear.*

---

## 1. The workflow everyone recognizes

A service is failing. You describe the symptom to an AI assistant and it comes back with a sensible list of things to check. Then it asks you to paste the logs.

So you open a terminal, tail the log, select four hundred lines, and paste them into the chat window. The model reads them and asks what the connection pool looks like. You open a second terminal, run a query, copy the result, paste it. It asks which deploy went out this morning. You switch to a browser tab, find the commit, paste that too.

Nothing in that loop is reasoning. It is you, moving bytes by hand between systems that hold the data and a model that could use it. You are the integration, and you are a slow one.

The same shape shows up everywhere. A coding agent that cannot read your issue tracker. A support bot that cannot look up an order. A terminal assistant that will happily write the query but cannot run it. In every case the model is not short of reasoning. It is short of access.

The fix looks obvious from one angle: give the application a way to fetch the log itself. That is an integration. Integrations are where the interesting problem starts, and the problem is arithmetic.

## 2. The N × M integration problem, counted

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

Twenty-four is not twenty-four afternoons of glue code. Each integration is a plugin format specific to that one application, an authentication scheme specific to that one system, a description of what the integration can do written so a model can understand when to use it, error handling, a release process, and a person who fixes it when the vendor changes an API (application programming interface).

The growth is the painful part, and it is easier seen than read. Every new item on one side multiplies by the size of the other side:

| What you add | New integrations you owe | Running total |
|---|---|---|
| the starting point, four applications and six data sources | 24 | 24 |
| a fifth application | 6, one per data source | 30 |
| a seventh data source | 5, one per application | 35 |

In general, *N* applications and *M* data sources cost *N* × *M*. One more application costs *M*, and one more data source costs *N*.

**Nobody actually pays that bill, and that is the real symptom.** In practice three or four popular pairs get built, usually by whoever has the largest user base to justify the work. The other twenty or so never exist. So *N* × *M* rarely appears as a budget line. It appears as the set of things your tools cannot do, and the copy-paste loop in section 1 is what fills the gap.

Now put one agreed interface in the middle instead. Each application implements the protocol once. Each system gets one server, written once, that any of those applications can use.

```text
4 applications + 6 data sources = 10 implementations
```

Ten instead of twenty-four, and the marginal cost changes shape entirely. Starting again from four and six: the mesh charges six integrations for one more application and four for one more data source. Through the boundary, either one costs exactly one piece of work.

**Two honest qualifications.** First, *N* + *M* counts implementations to write and maintain, not connections at run time. All twenty-four paths still exist when the team is working; they simply travel over one shared interface instead of twenty-four private ones. Second, you almost never write both sides. The application vendor writes its half once, and a large number of the *M* already exist as open-source servers you can install, so your own marginal cost is often one server or zero.

And the saving is conditional. A protocol that only one application speaks saves nothing at all. The arithmetic works because enough hosts have adopted MCP that writing one server is a reasonable bet, not because *N* + *M* is a smaller number on a whiteboard. The cheapest way to check that for yourself is to go and collect some of the saving, which is what the next section asks you to do.

## 3. Ten minutes with a server you did not write

The *M* side of that arithmetic is not hypothetical. Servers for common systems already exist, published by other people, and installing one takes about ten minutes. Collecting that saving is the fastest way to make MCP concrete, and nothing below asks you to write any code.

![Three numbered steps across the top, find, configure, and restart, leading down to a large panel showing the host asking whether a tool call should go ahead, with allow and deny buttons.](diagrams/04-ten-minutes.svg) *The whole of this section in one picture. The last panel is the one to watch: the host is asking, and that question is where every security decision in this series lives.*

**Find one.** Published servers are indexed in the MCP Registry, which stores metadata and no code. An entry records what a server is called, who proved they own that name, and where the package itself lives. You can search it directly:

```text
https://registry.modelcontextprotocol.io/v0.1/servers?search=postgres
```

Swap the search term for the system you want to reach, and the documentation is at <https://registry.modelcontextprotocol.io/docs>. The Registry is a wholesale index rather than a shop front: its own documentation says it is intended for aggregators and marketplaces rather than for host applications directly. Searching it by hand works anyway, and it is the clearest way to see exactly what a listing does and does not tell you.

This series names no particular server package on purpose. Package names age faster than posts do, and finding a current one yourself is the skill worth having.

**Read what an entry promises.** Three fields are required: a name, a description of at most a hundred characters, and a version. Everything else, including the link back to the source repository, is optional and may be absent entirely.

The name is a reverse-domain string such as `io.github.someone/postgres`, and it is not the string you type on a command line. That one lives in the entry's optional `packages` array, where each entry names a `registryType`, the package index it came from, and an `identifier`, its name inside that index. Copy the `identifier`. An entry that carries `remotes` instead of `packages` is a hosted server you connect to over a network rather than one you install, and the steps below do not apply to it.

**Then read what it does not promise.** The Registry is in preview, and its own moderation policy says it "does not make guarantees about moderation, and consumers should assume minimal-to-no moderation". A listing proves that somebody controls a name. It is not evidence that the code is safe, maintained, or what it claims to be.

So treat a server the way you would treat any other dependency you are about to run. If the entry names a source repository, read it. If it does not, that absence is itself information. [Post 19](../19-security/index.md) gives this its full treatment, including the fact that nothing pins the server you end up running to the definitions you originally approved.

**Put it in your host's configuration file.** The host is the application you already work in, such as a desktop assistant or an editor, and section 5 names the three roles properly. Most hosts start a local server as an ordinary subprocess, a program the host launches and keeps running underneath itself, so configuring one means naming a command and its arguments. A published Python server is usually launched with `uvx <identifier>`, where `<identifier>` is the string you copied. An entry may carry a `runtimeHint` naming that command outright, but the field is optional, so when it is missing fall back to the package index the `registryType` names. For a Python package that means `uvx`, which ships with `uv`, so install that first if you do not have it.

Finding the file is the step that catches people, because it is rarely where you would guess. Do not go hunting for it. Most hosts will open their own file for you: Claude Desktop has **Settings > Developer > Edit Config**, and VS Code and Zed each expose theirs as a command-palette entry. If the file already has content, add your entry alongside what is there rather than pasting over it, and keep the braces and commas balanced. A file that no longer parses usually shows up as no servers at all, including the ones that worked yesterday, rather than as a complaint about the entry you just added.

```json
{
  "mcpServers": {
    "your-name-for-it": {
      "command": "uvx",
      "args": ["the-identifier-you-copied"]
    }
  }
}
```

Claude Desktop, Claude Code, and Cursor all use that `mcpServers` key, in `claude_desktop_config.json`, `.mcp.json`, and `.cursor/mcp.json` respectively. Others disagree. VS Code reads `.vscode/mcp.json`, its top-level key is `servers`, and a `type` field is required on every entry. Zed uses `context_servers`, and Gemini CLI, a command-line interface, uses `mcpServers` with no `type` field at all.

[Post 23](../23-multi-client/index.md) has the full matrix, including where each file lives on macOS, Windows, and Linux, and a troubleshooting appendix for when a server will not start.

The key you invent, `your-name-for-it` above, is yours to choose. It is the label the host shows you, and it is the prefix the host puts in front of a tool name when two servers offer the same one, so keep it short and recognizable.

**Restart, then use it.** Restart the host fully. Some hosts read the file only at launch, and some keep a failed server marked failed until a second restart. What the server offers then appears somewhere in the host's interface, and where depends on the host.

Now ask for something that server covers. The model reads the descriptions it was given, decides one of them fits, and the host stops to ask whether the call should go ahead. The specification says a host **should** ask, not that it must, so if no prompt appears, look for the setting that turned it off before you conclude nothing ran.

That question is the part worth noticing. It is the host asking, not the server, and it sits on the trust boundary the rest of this series keeps returning to. Section 5 explains who owns that decision and why it cannot live anywhere else. Section 6 names the three kinds of thing a server publishes, one of which you have now used.

Ten minutes, no integration written, and you have collected some of the *N* + *M* saving section 2 counted. From [Post 05](../05-first-server/index.md) onward, this series builds the other side.

## 4. What a protocol boundary buys you

![The same four applications and six data sources, each connected once to a single central panel labeled Model Context Protocol.](diagrams/02-one-boundary.svg) *Compare the figure at the top of the post: same boxes, same positions. Only the middle changed, and twenty-four lines became ten.*

A protocol boundary is a written agreement about the shape of the messages that cross a line, precise enough that either side can be replaced without telling the other. MCP is that agreement for the line between an AI application and the systems it wants to reach. It fixes five things and deliberately leaves everything else alone:

1. How a client asks a server what it can do (`server/discover`, then `tools/list`).
2. How a client invokes one of those things (`tools/call`).
3. What a result looks like coming back.
4. How failures are reported.
5. How the bytes move, through one of two transports: standard input and output, called `stdio` throughout this series, for a local subprocess, or Streamable HTTP (Hypertext Transfer Protocol) for anything over a network.

Everything in MCP travels as JSON-RPC 2.0. A remote procedure call (RPC) is one program asking another to run a named operation and hand back the result, and the only genuinely hard part is agreeing on how the request and the result are written down. JSON-RPC 2.0 is that agreement, written in JavaScript Object Notation (JSON), and it is about as small as such agreements get: an object naming a method, an object of parameters, and an id you use to match the answer back to the question. Here is a tool call as the specification's tools page prints it, with one omission the notes below the result explain:

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

And the answer:

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

Two things to flag, both of which get a full treatment in post 03. The first is what the specification's examples leave out for readability. A real request also carries a `_meta` object, which is where the sender writes down the protocol revision it is speaking and which optional parts of the protocol it supports. It goes on every single request, because this revision has no opening exchange in which to say it once. The second is `resultType`, which is on every result. `"complete"` means the server finished the job, and a server can also answer that it needs something from the user before it can finish, which is what post 08 is about.

That is the entire boundary, and three useful properties fall out of it.

**Substitution.** Swap the desktop assistant for a different one and your server does not change. Swap PostgreSQL for something else behind the server and the applications do not change. The line in the middle is the only contract either side has to honor.

**Reach.** You write the server once per system, not once per application. This is the whole of the *N* + *M* claim, expressed as engineering rather than arithmetic.

**Debuggability.** Because it is JSON over a documented transport, a failing call is reproducible by hand. You can capture the request that went wrong and replay it: write the JSON object into a local server's standard input, or send it with a command-line HTTP client when the server listens over the network, with no host and no model anywhere in the picture. That is worth more than it sounds like on the day something breaks.

## 5. The three roles in one picture

![A host panel containing the user, the model, and a client, separated by a dashed trust boundary from a server panel, which reaches a data source outside MCP.](diagrams/03-three-roles.svg) *The host owns the model and the consent decision, the client owns the connection, and the server owns what it publishes.*

MCP has exactly three roles, and confusing the first two causes more wasted debugging time than any other mistake in this ecosystem.

**Host.** The application the user actually interacts with. Claude Desktop, Cursor, VS Code, a command-line interface you write yourself. The host owns the model, owns the conversation, and owns every permission decision. When something asks "are you sure you want to let this tool run", that is the host talking.

**Client.** The protocol-speaking object living inside the host. There is exactly one client per connected server. It owns the transport, matches responses back to requests by id, and caches what the server said it offers. It is infrastructure. You will not write one by hand until [Post 10](../10-mcp-client/index.md), and most readers never need to.

**Server.** The process that publishes tools, resources, and prompts. Usually this is your code. Note what the word does *not* imply: a server is not necessarily remote and not necessarily a web service. A host will often spawn your server as an ordinary subprocess on the same machine and talk to it over a pipe.

Behind the server sits the actual data source, and it is outside MCP entirely. The protocol has no opinion about your database driver, your file paths, or your API keys. It only cares about what your server chooses to publish.

The dashed line in the figure is the trust boundary, and it is worth being blunt about which way it faces. Everything a server sends back, including the descriptions of its own tools, is untrusted input as far as the host is concerned. The specification is explicit that there **SHOULD** always be a human in the loop with the ability to deny a tool call, and that clients **MUST** treat tool annotations as untrusted unless the server is trusted. The host, not the server, is the security boundary. [Post 19](../19-security/index.md) is entirely about what happens when people forget this.

One vocabulary note that will save you confusion. In casual conversation, and in a great deal of documentation, "MCP client" is used loosely to mean the whole application. In this series it never does. Host is the application, client is the connection object inside it, and the two words are never swapped.

## 6. What a server exposes: tools, resources, and prompts

A server publishes three kinds of thing, called primitives throughout this series and in the table below. They differ mainly in who decides to use them.

| Primitive | For | Who pulls the trigger | Covered in |
|---|---|---|---|
| **Tools** | doing something | the model | [Post 06](../06-tools-in-depth/index.md) |
| **Resources** | reading something | the application | [Post 07](../07-resources-and-prompts/index.md) |
| **Prompts** | starting something | the user | [Post 07](../07-resources-and-prompts/index.md) |

**Tools** are functions with a name, a description, and a JSON Schema describing their arguments. The specification calls them model-controlled: the model reads the descriptions and decides when a tool is worth calling. `get_weather`, `run_query`, `create_issue`.

**Resources** are readable things addressed by a Uniform Resource Identifier (URI), such as `file:///var/log/app.log` or a database schema. The specification calls them application-driven: the application, not the model, decides which resources to attach to the conversation, which is a different control flow from a tool call even though both end up as text in front of a model.

**Prompts** are named, reusable entry points that a user picks deliberately, usually from a menu or a slash command. They expand into a message with context already attached. Picking "Diagnose slow machine" can open the conversation with a live reading of the machine already in it, so the user never types the question and the model never has to ask for the data.

That is as far as this post goes. The distinction between the three, and the design mistake of turning everything into a tool because tools are the easiest to write, is the subject of [Post 06](../06-tools-in-depth/index.md) and [Post 07](../07-resources-and-prompts/index.md).

## 7. What MCP explicitly does not do

Three disclaimers, all of which matter more than most introductions admit.

**It adds no intelligence.** The protocol carries a name, some arguments, and a result. Nothing in it decides which tool to call, or notices that calling it was a bad idea. If the model picks the wrong tool, MCP has no opinion. This is why a tool's description and schema are so consequential: they are the entire user interface the model gets, and a badly written description is a bug you will observe as "the model never uses my tool".

**It defines no privacy boundary.** This one deserves care, because the word "server" misleads people in both directions. A server is a role, not a location. Many MCP servers run as a subprocess on your own laptop, holding your own credentials, reading your own files, with no third party in the path at all. That is genuinely useful and it is the normal setup for local development.

But a local server does not imply a local model. If your host is talking to a hosted model, then everything your tool returns, and that the host puts into the conversation, is sent to that model provider like any other message. MCP standardizes how the data is *reached*. Whether the data leaves your machine is decided by your host and your model configuration, and the protocol has nothing to say about it.

**It enforces no safety.** A tool can declare itself read-only. Nothing checks. Annotations are hints for the host's benefit and the specification says plainly that clients must treat them as untrusted. The protocol will not stop a server from deleting a file when a tool named `list_files` is called, will not tell you that a server changed its tool descriptions overnight, and does not authenticate the server to you. Those are real problems with real answers, and the answers live in your server code and your host's permission model rather than in the wire format.

## 8. Where the protocol came from, and who owns it now

Anthropic introduced MCP as an open standard in November 2024, alongside open-source software development kits (SDKs) and reference server implementations. In December 2025 Anthropic donated it to the Agentic AI Foundation, described in the announcement as "a directed fund under the Linux Foundation", where MCP is a founding project alongside Block's goose and OpenAI's AGENTS.md. The foundation was co-founded by Anthropic, Block, and OpenAI, with support from Google, Microsoft, Amazon Web Services, Cloudflare, and Bloomberg. That roster is the part of the announcement worth keeping. Several of those companies ship the hosts named elsewhere in this post, so the vendors whose applications you would want your server to appear inside are also the ones underwriting the standard it speaks, which is exactly the condition section 2 said the *N* + *M* saving depends on.

The announcement is careful to say that the day-to-day did not change: "For MCP little changes. The governance model we introduced earlier this year continues as is." What the donation does buy is a neutral home. The specification is not one company's roadmap, and it changes through a public proposal process. Those proposals are called Specification Enhancement Proposals (SEPs), they are numbered, and this series cites them by number when explaining why something is the way it is.

Revisions are named by date rather than by version number. **This series targets 2026-07-28 and only 2026-07-28.** That matters for a specific reason: this revision made the protocol stateless. The `initialize` handshake is gone, protocol sessions and the `Mcp-Session-Id` header are gone, and there is no longer a channel for a server to send a request back to a client. So a tutorial written against an earlier revision will teach you a handshake that no longer exists and a session identifier that no server will honor, and it will look correct while doing it. Check the date on anything you read about MCP, including this.

## 9. How to read this series

Twenty-four posts in six parts. Read them in order if MCP is new to you; each part also stands alone.

| Part | Posts | What it gives you |
|---|---|---|
| **I. Foundations** | 01 to 04 | The mental model, the wire format, and the two transports. Posts 01 and 02 are code-free; 03 and 04 add short scripts that read the wire directly. |
| **II. Building servers** | 05 to 09 | A working server, then tools, resources, prompts, mid-call questions, and long-running work. |
| **III. Clients, hosts, and testing** | 10 to 12 | The other side of the boundary: write a client, write a host, and test both. |
| **IV. Projects** | 13 to 18 | Three real servers built end to end: a database analyst, a DevOps responder, a research browser. |
| **V. Production** | 19 to 22 | The attacks the protocol does not stop, OAuth 2.1, deployment, and publishing. |
| **VI. Interoperability and the frontier** | 23 and 24 | One server proven against every major host, then extensions and MCP Apps. |

**Two ways through Part I.** [Post 03](../03-wire-protocol/index.md) and [Post 04](../04-transports/index.md) are the longest in Part I, and both are reference-shaped: the wire format field by field, then the transports header by header. If you like the ground firm under you before you build, read them in order and continue to [Post 05](../05-first-server/index.md).

If you would rather have something running first, treat section 3 above as the warm-up, read [Post 02](../02-architecture/index.md) for the three roles, then go straight to [Post 05](../05-first-server/index.md) and write a server. Come back to [Post 03](../03-wire-protocol/index.md) and [Post 04](../04-transports/index.md) when you want to know what the library was doing on your behalf, which is usually the first time something breaks. Neither route skips anything. They differ only in when the wire format arrives.

If you already run a server written against an older revision, the posts that will change your code are 03, 07, 08, and 09: the handshake and the session went in 03, `resources/subscribe` became `subscriptions/listen` in 07, the server-to-client back channel became a return value in 08, and tasks moved out of the core protocol in 09.

Two things are pinned throughout, and both are named here so you can check them against whatever you have installed. The protocol revision is 2026-07-28. The Python SDK is `mcp==2.0.0b2`, which was a beta at the time of writing; every project pins the exact version it was tested against, and where the SDK had not yet caught up with the specification, the post says so rather than guessing.

---

## Common pitfalls

- **Calling the host "the client".** Almost every confusing MCP conversation traces back to this. The host is the application; the client is the connection object inside it. When a document says "the client asks the user", it usually means the host.
- **Assuming a local server keeps your data local.** It keeps the *access* local. If the host is using a hosted model, tool output that reaches the conversation reaches the provider. Decide this at the host level, not by looking at where the server process runs.
- **Expecting MCP to improve tool selection.** If the model calls the wrong tool, the fix is almost always the tool's name, description, or schema. The protocol has no layer that could help.
- **Following a tutorial without checking its date.** Anything that opens with an `initialize` handshake, an `Mcp-Session-Id` header, or a server calling `sampling/createMessage` is written against a revision this series does not teach. It is not wrong for its own era, but it will not run against a 2026-07-28 server.
- **Treating annotations such as `readOnlyHint` as enforcement.** They are hints from a party the host has been told not to trust. Enforce read-only in your server and in your database grants.
- **Reaching for a tool when the answer is a resource.** New servers tend to expose everything as a tool because tools are the easiest primitive to write. That fills the model's context with things that should have been readable on demand.
- **Counting *N* + *M* as a saving you personally collect.** You collect it only for the hosts that already speak MCP. For anything else, you are still writing a bespoke integration and should say so out loud when estimating.

---

## Further reading

- Model Context Protocol, *"Versioning and revision index"* (2026). While 2026-07-28 was a release candidate its pages were served from `/specification/draft/`, so check both paths if a deep link fails.
- Model Context Protocol, *"2026-07-28 changelog"* (2026). The authoritative list of what this revision removed.
- Model Context Protocol, *"Tools"*, revision 2026-07-28. Source of the `tools/call` request and result shown above.
- Model Context Protocol, *"Registry moderation policy"*. Source of the "minimal-to-no moderation" line quoted in section 3.
- MCP Registry documentation. What a listing records, and the search URL section 3 uses.
- Model Context Protocol, *"MCP joins the Agentic AI Foundation"* (2025). The donation announcement quoted in section 8.
- Linux Foundation, *"Formation of the Agentic AI Foundation"* (2025).
- JSON-RPC 2.0 specification. The entire wire grammar MCP is built on, and short enough to read in one sitting.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 02 — The architecture: hosts, clients, and servers](../02-architecture/index.md)**: the three roles at full depth, including how a host turns many servers into one catalog of tools and what happens when two servers claim the same tool name.
- **[Post 03 — The wire protocol: JSON-RPC, discovery, and the stateless model](../03-wire-protocol/index.md)**: the `_meta` block this post skipped, `server/discover`, `resultType`, a complete annotated trace, and a short script that speaks the protocol with an HTTP client and no SDK at all.
