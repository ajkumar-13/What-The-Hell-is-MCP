# 02 - The architecture: hosts, clients, and servers

> **TL;DR.** The Model Context Protocol (MCP) has three roles, and nearly every confusion in the ecosystem is a mix-up of the first two: a host owns the model, the conversation and consent, a client is a thin object that owns exactly one server connection, and a server is the code that exposes tools, resources and prompts. The host merges the lists it gets from every connected server into one catalog, so the model picks a tool name and never a server, and a name that two servers share has to be qualified, shadowed or refused. Tools are model-controlled, resources are application-driven, and prompts are user-controlled. Consent can only live in the host, the one party that sees the user, the conversation and every server, while authorization and validation stay in the server, the one party that knows its own backing system.
>
> **Prerequisites:** [Post 01](../01-what-is-mcp/index.md).
> **Safe to skip?** Skip it if the reader can already say how many clients a host runs for a given configuration, what a host does when two servers both export `search`, and why consent cannot live in a server.
>
> **After reading, you will be able to:**
>
> - Assign any responsibility in an MCP setup to the host, the client, or the server, without guessing.
> - Trace a tool name the model chose back to the exact connection that will carry the call.
> - Predict what a host does when two connected servers export the same tool name.
> - Explain why consent belongs in the host while validation belongs in the server.

![One host containing three clients labeled A, B and C, each with a single arrow crossing the protocol boundary to its own server, and each server reaching a different backing system outside MCP.](diagrams/01-host-clients-servers.svg)

*One host, three clients, three servers, and one connection per client. The same three servers run through the rest of this post.*

---

## 1. The question: who owns which job?

Consider a bug report of a familiar shape: *the client cannot connect to my server*. It arrives with a screenshot of a chat window and a configuration file, and it cannot be answered, because its author thinks the client is the application on the screen.

It is not. The application on the screen is a **host**. Inside it, that host is running several **clients**, one per configured server, and one of them failed. Which one, and at which layer, are separate questions that the report has already collapsed into a single word.

So the question of this post is which of the three MCP roles owns which job, and why almost every confusion in the ecosystem is a mix-up of the first two. [Post 01](../01-what-is-mcp/index.md) argued that MCP replaces a mesh of bespoke integrations with one protocol boundary. This post is about the three things that sit on either side of that boundary, and about being pedantic with their names. The pedantry pays for itself twice: once in debugging, and once in the security model, which is built entirely on the distinction.

No server code appears here. This is the mental-model post, and the one configuration file it shows is there to be counted rather than run. The messages themselves, JavaScript Object Notation remote procedure call (JSON-RPC) requests and results, arrive in [post 03](../03-wire-protocol/index.md).

## 2. The host owns the model, the conversation, and consent

The host is the application a person actually uses. Claude Desktop, Claude Code, Cursor, VS Code, Zed, and the small command-line host that [post 11](../11-building-a-host/index.md) builds are all hosts.

The specification's Architecture page gives the host process a short and blunt job description: it creates and manages multiple client instances, controls client connection permissions and lifecycle, enforces security policies and consent requirements, handles user authorization decisions, coordinates the integration of the model, and manages context aggregation across clients.

Read as a list of consequences, that job description puts three things in the host and nowhere else:

- **The model.** The thing that reads tool descriptions and decides that `run_query` is the right call for "how many signups last week".
- **The conversation.** The full history, including everything the user typed and everything every server returned. The specification's design principles are explicit that servers should not be able to read the whole conversation, nor "see into" other servers, and that the full conversation history stays with the host.
- **Consent.** Approval dialogs, per-tool allow lists, "always allow for this project", and the decision to connect to a server at all.

Nothing in a `tools/call` request mentions a model. A scheduled script that calls three tools every night, decides what to do with a fixed rule, and never asks anyone anything is a perfectly ordinary host, and the servers it calls cannot tell the difference. That is worth knowing because it says where the intelligence is *not*: it is not in the protocol, and it is not in the server.

## 3. The client owns one connection and nothing else

A client is a protocol-speaking object created by the host. The specification is unambiguous about its shape: **each client communicates with exactly one server**, and a host creates one client per server it wants to talk to. The Architecture page calls it a one-to-one relationship: one client, one connection, one server, even when a remote server answers many clients at once.

A client owns four small things:

1. **A transport.** The pipes of a process the host spawned, or a single HyperText Transfer Protocol (HTTP) endpoint.
2. **Request correlation.** Matching each response to the request id that asked for it. Client A's request id `5` has nothing to do with Client B's request id `5`.
3. **Per-request metadata.** It attaches the protocol revision it speaks and the capabilities it supports to every single request. Section 9 returns to that.
4. **A cache of what that one server offers.** The tool, resource and prompt lists it last read back.

That is the whole job. A client does not decide which tool to call, does not know what the user typed, does not format anything for a human, and cannot see another client's server. It has no judgment. A question of the form "why did the client choose that tool" is a question about the host.

One test settles the count every time. **Count the servers in the configuration. That is how many clients there are. There is still exactly one host.**

Configuration is where that count is easiest to see. Section 3 of [post 01](../01-what-is-mcp/index.md) added a single entry to a host's file. Three entries look like this:

```json
{
  "mcpServers": {
    "files": { "command": "uvx", "args": ["..."] },
    "docs":  { "command": "uvx", "args": ["..."] },
    "db":    { "command": "uvx", "args": ["..."] }
  }
}
```

Three keys, so three clients, and one host reading the file. Those are the three connections of the hero figure, and they run through the rest of this post. The block writes every entry as a local command to stay short; the hero draws the docs server as a remote service, whose entry would name a Uniform Resource Locator (URL) instead, in a form each host spells differently. The keys themselves matter: they are the local identifiers the owner of this host chose for these connections, free to be renamed, and section 6 is about the one job they do that nothing else can.

One honest note, so the vocabulary does not ambush the reader elsewhere. The wider ecosystem uses "MCP client" loosely to mean "an application that supports MCP". The project's own Extension Support Matrix lists Claude Desktop and Cursor, among others, in a column headed *Client*. That page is comparing hosts. This series uses the three words exactly as [notation_guide.md](../../notation_guide.md) defines them, and it does not write a client by hand until [post 10](../10-mcp-client/index.md).

## 4. The server is the code this series builds

The server is the process that exposes tools, resources and prompts. It is the box this series teaches the reader to build, and it is the simplest of the three roles by design: the first of the specification's four design principles is that servers should be extremely easy to build, with host applications handling the complex orchestration.

A server holds the things nobody else should hold. The database password. The scoped application programming interface (API) token. The path to the directory it is allowed to read. It can be a local process the host spawned on a laptop, or a remote service on the other side of the internet, and the protocol treats those two the same way. Only the transport differs, which is [post 04](../04-transports/index.md).

The important property of a server is how little it can see. On any given request, a server gets a method name, some arguments, and per-request metadata. It does not get the conversation. It does not get a list of the other servers the host is connected to. It cannot tell whether the argument it was handed was typed by a human, inferred by the model, copied out of a web page the model just read, or produced by another server's tool result. From inside the server process, all four look identical.

That blindness is deliberate. It is what makes servers composable, which is the second design principle, and it is also exactly why the server cannot be the place where consent is decided. Section 8 returns to that.

## 5. One catalog, built from every connection

Back to those three servers. When a user asks a question, how does the model know which one of them to use?

It does not, and it never finds out. The figure shows what it is given instead.

![A lookup table mapping each name the model sees to a client, a server, and the name that actually goes out on the wire, beside three server cards showing the raw names each server exposes. Two rows are tinted, because two servers both export a tool called search.](diagrams/02-capability-catalog.svg)

*The host merges every server's list into one name-to-connection map, and resolves collisions on the way in. Only the contested name changes; the wire name never does.*

Each client asks its own server what it has, using the list methods: `tools/list`, `resources/list`, `prompts/list`. A client may first call `server/discover` to find out which of those the server supports at all, since there is no point asking a server that offers no prompts for its prompts. Servers must implement `server/discover`; clients may call it before any other request, and are free to skip it.

The host then merges the answers into a single **capability catalog**: one flat map from a display name to the connection that can serve it.

| Name the model sees | Client | Resolves to | Name sent on the wire |
|---|---|---|---|
| `read_file` | A | files server | `read_file` |
| `files.search` | A | files server | `search` |
| `docs.search` | B | docs server | `search` |
| `fetch_page` | B | docs server | `fetch_page` |
| `run_query` | C | db server | `run_query` |

Three of those five rows came through untouched, because their names were already unique across every connected server. The other two did not: the files server and the docs server both export a tool called `search`, so the host qualified both of them. Section 6 is about those two rows.

What the model is offered is the left column, plus each tool's description and input schema. It picks a name. The host looks that name up, finds the client, strips off anything it added, and has that one client send `tools/call` with the name in the right column, which is the only name that server has ever heard of. The routing is a dictionary lookup, and it happens entirely in the host.

Two consequences follow immediately, and both matter to a server author.

**The model picks a tool, not a server.** It never sees which connection a name belongs to. A call that lands on the wrong server was routed there by the host's name map, and section 10 says where to look when that happens.

**A tool description is competing.** A tool called `search_docs` sits in one flat list next to every tool from every other connected server, and the model chooses between them on description text alone. [Post 06](../06-tools-in-depth/index.md) is about writing descriptions that win that comparison honestly.

One detail worth carrying forward. Since revision 2026-07-28 the Tools page says that the set `tools/list` returns must not vary per connection or as a side effect of other requests on the connection, so a `connect_database` tool that makes `query` appear in a later `tools/list` is no longer conformant. The set may vary by the authorization presented on the request: filtering by the caller's credentials is still fine; filtering by history is not. The Resources and Prompts pages state the same rule for their lists.

Caching is a separate mechanism rather than a consequence of that rule. Every list result whose `resultType` is `"complete"` carries a freshness hint, `ttlMs`, alongside a sharing hint, `cacheScope`. Both are required by SEP-2549, one of the Specification Enhancement Proposals (SEPs) through which the protocol changes, and [post 07](../07-resources-and-prompts/index.md) covers them.

## 6. Name collisions, and the three strategies

Tool names are only expected to be unique **inside** one server. The Tools page says so, and then says the obvious next thing: clients or proxies that aggregate tools from several servers may encounter naming collisions, such as two servers that each expose a `search` tool, and should implement a disambiguation strategy such as prefixing tool names with a server identifier.

There is a trap hidden in that sentence, and the specification flags it. The server `name` from `serverInfo`, the name a server reports for itself, "is not guaranteed to be unique across servers and **SHOULD NOT** be relied upon for disambiguation." The identifier that works is the local key the owner of the host gave the connection in the configuration file, because it is chosen on that machine and is unique in that file.

Hosts pick one of three strategies:

1. **Qualify.** Rewrite the contested name as `<connection>.<tool>` in the catalog and strip the prefix again before sending the call. Both tools stay reachable. This is the strategy the specification points at, and the one a careful host uses. It renames **both** sides rather than only the second one to arrive, so the server that happened to be listed first does not quietly keep the plain name.
2. **Shadow.** First one wins, or last one wins, usually by the order servers appear in the configuration file. One of the two tools silently disappears from the catalog. It is not in the list the model sees, so it is never called, and nothing logs an error.
3. **Refuse.** Drop the duplicate, or refuse to load the second server, and say so. Noisiest, and therefore the kindest.

A host may also skip the comparison and qualify every name up front, contested or not. That is defensible too, and a little louder, but it costs the model the plain names it may have seen during training or in an earlier turn.

A server author cannot control which strategy a host picks, and a server used widely enough will meet all three. Two rules follow:

- A tool named `search`, `query`, `get`, `run`, or `list` is the one that collides. `search_docs` is far less likely to.
- Tool names should be between 1 and 128 characters, drawn from American Standard Code for Information Interchange (ASCII) letters, digits, underscore, hyphen and dot. Every one of those naming rules is a *should* rather than a *must*, so names that break them do turn up. A separator still has to come from that set, and a slash is not in it, which is why the qualified form is `files.search` and never `files/search`. Model providers then apply their own rules on top, and some narrow the set further, to letters, digits, underscore and hyphen with no dot at all, so a host may fall back to `files_search`.

Collision handling from the host's side is [post 11](../11-building-a-host/index.md), which builds the qualifying catalog and tests it. Collision handling as a deliberate attack, where a malicious server picks a name in order to shadow a trusted one, is [post 19](../19-security/index.md).

## 7. Who controls what

The three primitives differ in a way that is easy to state and easy to get wrong: they differ in **who is allowed to start them**.

![A control matrix showing that the model starts tools, the host starts resources, and the user starts prompts, with the other parties marked as able to block, to take part, or not to choose at all, and a closing note that whoever starts an interaction the host can still refuse it.](diagrams/03-who-controls-what.svg)

*One party per row pulls the trigger. Everyone else can at most take part, or refuse.*

**Tools are model-controlled.** The Tools page says tools are designed so that the language model can discover and invoke them automatically, based on its contextual understanding and the user's prompts. That is the point of a tool. The same page carries a warning: for trust and safety there should always be a human in the loop with the ability to deny an invocation, and applications should make clear which tools are exposed to the model, show visibly when one is invoked, and present confirmation prompts.

**Resources are application-driven.** The word on the Resources page is *application-driven*, with host applications determining how to incorporate context based on their needs. A host may show a picker, let the user search and filter a list, or include context automatically, by heuristic or by the model's selection. The model takes part in that decision only if the host chooses to involve it; it does not reach out and read a resource on its own initiative.

**Prompts are user-controlled.** They are exposed with the intention that the user can explicitly select them, typically as slash commands or a menu. One clarification is worth keeping: the word is about who decides *when* a prompt is used, not who authors its content. The content is authored by the server; the trigger is pulled by the person.

A short note on how strong those three words are. Each of the three pages also says that the protocol itself does not mandate any specific user interaction model. The words describe what each primitive is designed for, not an interface every host is obliged to build.

The practical design rule falls straight out of the matrix. If the model should be able to decide, on its own, to go and get something, that is a tool, even if it feels like data. If the host should place something in context, that is a resource. If a person should be able to start a piece of work with one click, that is a prompt. [Post 07](../07-resources-and-prompts/index.md) works through the borderline cases.

One clarification, because "controlled by" is doing a lot of work here. It names who may *start* the interaction, not who may *stop* it. The host can refuse all three, and in a well-built host it sometimes does exactly that.

## 8. Why the host is the security boundary

Sections 2 and 4, set side by side, force the answer.

The host can see the user, the screen, the whole conversation, every connected server, and the model's reasoning about what to do next. A server can see one request. That asymmetry is not an accident of implementation; it is a stated design principle. Servers should not be able to read the whole conversation nor see into other servers, cross-server interactions are controlled by the host, and the host process enforces the security boundaries.

So the questions that need the whole picture can only be answered in the host:

- Should this server be offered to the model at all, in this conversation?
- Did the argument to this call originate with the user, or with text that arrived from somewhere else?
- Should a human see this call before it runs?
- May the output of this server's tool become the input of that server's tool?

A server cannot answer any of them, because it does not have the inputs.

This does not let the server off the hook. It moves it to a different hook. The host owns **consent and isolation**; the server owns **authorization and validation** against its own backing system, and the Tools page makes that half a *must*: servers must validate all tool inputs and implement proper access controls. The host cannot know which rows in the database this token may read, which paths are inside the sandbox, or which arguments are nonsense. The server cannot know whether the person watching agreed to any of it. Neither substitutes for the other, and a setup with only one of them is broken in a way that is invisible until it is not.

Two honest caveats come before relying on any of this.

First, nearly all of the host's obligations toward the user are written as **should**, not **must**. There should be a human in the loop. Applications should show which tools are exposed. Clients should prompt for user confirmation on sensitive operations. A host that quietly auto-approves everything is unhelpful, but it is not non-conformant, and a server is never told which kind of host is calling it.

Second, the boundary has to trust its own inputs. Tool descriptions and annotations are written by servers. On this point the Tools page does say *must*: clients must consider tool annotations to be untrusted unless they come from trusted servers, and the protocol blog's 2026 post on annotations, listed under Further reading, makes the same point at length. A boundary that must trust text supplied by the thing it is guarding has a door in it. [Post 19](../19-security/index.md) walks through the attacks that come through that door, and they are the reason this post insisted on the vocabulary.

## 9. What this picture no longer contains

A reader who learned MCP before revision 2026-07-28 has a connection setup phase in that mental model. It is gone.

There is no `initialize` handshake and no protocol-level session. SEP-2575 (*Stateless MCP*) removed the handshake and SEP-2567 (*Sessionless MCP*) removed sessions and the `Mcp-Session-Id` header, and `initialize` no longer appears anywhere in the schema. A client does not negotiate once and then remember. It attaches `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` to the `_meta` field of **every single request**, and the server must not rely on prior requests over the same connection to establish context. Up-front discovery, when a client wants it, is one ordinary request called `server/discover`.

The architectural consequence is the one to keep: the client got thinner. It used to guard a negotiated, connection-scoped state that a server could depend on. Now the state that matters travels in each message, which is why a server can be an ordinary web service behind a round-robin load balancer with no stickiness at all. State that genuinely has to survive between calls is passed back as an explicit, server-minted handle and supplied again as an ordinary tool argument.

None of that changes the three roles. It changes how much the middle one has to remember. [Post 03](../03-wire-protocol/index.md) shows the messages.

## 10. What can go wrong?

Each failure below follows from the three roles, and each shows a symptom that points at the wrong component unless the roles are kept straight.

- **A tool vanishes on a shadowing host.** A user reports that a server's `search` tool "does nothing", and no log anywhere shows an error. On a first-wins or last-wins host, another server's `search` took the name, so this one never reached the catalog the model sees. The first question is what else the user has configured; the fix on the server side is a specific name such as `search_docs`.
- **The wrong server runs.** A question about the documentation produces a call on the files server. The model chose a name from a flat catalog and the host routed it, so the place to look is the host's name map and the descriptions it fed the model, not the model's reasoning and not the server that received the call.
- **The prefix collides again.** A host that qualifies names by the server's self-reported `name` produces two identical qualified names as soon as two different servers both report themselves as, say, `filesystem`, and the collision it meant to resolve is back. The prefix has to come from the configuration key.
- **A tool appears only after another call.** `query` shows up in `tools/list` after `connect_database` on one connection and is missing on the next, or on another replica behind a load balancer. That is the non-conformant pattern of section 5; state that has to survive between calls belongs in a handle, which [post 03](../03-wire-protocol/index.md) builds.
- **The separator is a slash.** A qualified name such as `files/search` falls outside the character set the Tools page recommends and outside the narrower sets some model providers accept, so the tool may be rejected or renamed somewhere between the catalog and the model. The separator is a dot, or an underscore where a provider forbids the dot.

## 11. Summary

| Role | Owns | Cannot see |
|---|---|---|
| host | the model, the conversation, consent, the catalog and its routing | which rows a token may read, which arguments the backing system would reject |
| client | one transport, request correlation, per-request metadata, one server's cached lists | the user, the conversation, any other server |
| server | its tools, resources and prompts, its credentials, authorization and validation | the conversation, other servers, where an argument came from |

The model picks a name, the host routes it to one client, and that client carries it to one server under the name the server published. Consent lives where the whole picture is, and validation lives where the backing system is.

---

## Common pitfalls

1. **Saying "client" when the application is meant.** Cursor is a host, and so is Claude Desktop. Before answering "the client will not connect", the useful question is which of the two the reporter means, and how many clients that host is running.
2. **Assuming a client can be shared.** There is no connection pool spanning servers and no client that multiplexes two of them. A design that needs one client to talk to two servers is a design for a host.
3. **Building a resource for something the model is supposed to fetch by itself.** Resources are application-driven. If the decision to go and get the data belongs to the model, it is a tool.
4. **Treating the host's approval dialog as the server's security control.** It is a *should*, it lives in software the server author did not write, and it can be switched off. The server still needs authorization, validation, and least-privilege credentials of its own.
5. **Carrying an initialization handshake over from an older tutorial.** There is no `initialize` in 2026-07-28, and a diagram with a setup phase is a revision behind.

---

## Further reading

- Specification, *"Architecture"*, revision 2026-07-28. The host and client job descriptions of sections 2 and 3, and the four design principles quoted in sections 2, 4 and 8.
- Specification, *"Tools"*, *"Resources"*, *"Prompts"*, revision 2026-07-28. Each page opens with a "User Interaction Model" section, which is where model-controlled, application-driven and user-controlled are defined, and each states the list rule of section 5.
- Specification, *"Tools"* § Tool Names, revision 2026-07-28. Uniqueness scoping, the recommended character set, and the warning against keying disambiguation on a server's self-reported name.
- SEP-2575, *"Stateless MCP"*, and SEP-2567, *"Sessionless MCP"* (2026). The removals of section 9.
- Hungerford, O., Morrow, S., and Chang, L., *"Tool Annotations as Risk Vocabulary: What Hints Can and Can't Do"* (2026). Why annotations are hints a host must not trust from an untrusted server.
- Model Context Protocol, *"Extension Support Matrix"*. The "clients" table of section 3, which lists hosts.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 03 - The wire protocol: JSON-RPC, discovery, and the stateless model](../03-wire-protocol/index.md):** the same three boxes with every message drawn in and every `_meta` key named.
- **[Post 11 - Building a host: the tool loop, many servers, and permissions](../11-building-a-host/index.md):** the host of this post built in code, with the qualifying catalog of section 6 and the permission gate of section 8.
