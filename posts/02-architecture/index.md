# 02 · The architecture: hosts, clients, and servers

> **TL;DR.** The Model Context Protocol (MCP) has three roles, and nearly every confusion in the ecosystem is a mix-up of the first two: a host owns the model, the conversation and consent, a client is a thin object that owns exactly one server connection, and a server is your code. The host merges the lists it gets from every connected server into one catalog, so the model picks a tool name and never a server. Tools are model-controlled, resources are application-driven, and prompts are user-controlled. Consent can only live in the host, because the host is the only party that can see all of it.
>
> **After reading this you will be able to:**
> - Assign any responsibility in an MCP setup to the host, the client, or the server, without guessing.
> - Trace a tool name the model chose back to the exact connection that will carry the call.
> - Predict what a host does when two of your servers export the same tool name.
> - Explain why consent belongs in the host while validation belongs in your server.

![One host containing three clients labeled A, B and C, each with a single arrow crossing the protocol boundary to its own server, and each server reaching a different backing system outside MCP.](diagrams/01-host-clients-servers.svg) *One host, three clients, three servers, and one connection per client. The same three servers run through the rest of this post.*

---

## 1. The bug report that cannot be answered

Read enough MCP issue trackers and you will meet this report: *the client cannot connect to my server*. It arrives with a screenshot of a chat window and a configuration file, and it cannot be answered, because the person writing it thinks the client is the application on their screen.

It is not. The application on their screen is a **host**. Inside it, that host is running several **clients**, one per configured server, and one of them failed. Which one, and at which layer, are separate questions that the report has already collapsed into a single word.

[Post 01](../01-what-is-mcp/index.md) argued that MCP replaces a mesh of bespoke integrations with one protocol boundary. This post is about the three things that sit on either side of that boundary, and about being pedantic with their names. The pedantry pays for itself twice: once when you are debugging, and once when you get to the security model, which is built entirely on the distinction.

No server code here. This is the mental-model post, and the one configuration file it shows is there to be counted rather than run. The messages arrive in [Post 03](../03-wire-protocol/index.md).

## 2. The host owns the model, the conversation, and consent

The host is the application a person actually uses. Claude Desktop, Claude Code, Cursor, VS Code, Zed, and the small command-line tool you will write in [Post 11](../11-building-a-host/index.md) are all hosts.

The specification gives the host process a short and blunt job description: it creates and manages client instances, controls their connection permissions and lifecycle, enforces security policies and consent requirements, handles user authorization decisions, coordinates the model, and aggregates context across every client it owns.

Read that list again and notice what it means in practice. Three things live in the host and nowhere else:

- **The model.** The thing that reads tool descriptions and decides that `run_query` is the right call for "how many signups last week".
- **The conversation.** The full history, including everything the user typed and everything every server returned. The specification is explicit that servers should not be able to read the whole conversation, nor see into other servers.
- **Consent.** Approval dialogs, per-tool allow lists, "always allow for this project", and the decision to connect to a server at all.

Nothing in the wire format mentions a model. A scheduled script that calls three tools every night, decides what to do with a rule, and never asks anyone anything is a perfectly ordinary host, and the servers it calls cannot tell the difference. That is worth knowing because it tells you where the intelligence is *not*: it is not in the protocol, and it is not in your server.

## 3. The client owns one connection and nothing else

A client is a protocol-speaking object created by the host. The specification is unambiguous about its shape: **each client communicates with exactly one server**, and a host creates one client per server it wants to talk to. One to one, always, in both directions.

A client owns four small things:

1. **A transport.** The pipes of a process the host spawned, or a single HyperText Transfer Protocol (HTTP) endpoint.
2. **Request correlation.** Matching each response to the request id that asked for it. Client A's request id `5` has nothing to do with Client B's request id `5`.
3. **Per-request metadata.** It attaches the protocol revision it speaks and the capabilities it supports to every single request. More on that in section 9.
4. **A cache of what that one server offers.** The tool, resource and prompt lists it last read back.

That is the whole job. A client does not decide which tool to call, does not know what the user typed, does not format anything for a human, and cannot see another client's server. It has no judgment. When you find yourself asking "why did the client choose that tool", you are asking a question about the host.

Here is the test that settles it every time. **Count the servers in your configuration. That is how many clients you have. There is still exactly one host.**

Configuration is where that count is easiest to see. [Post 01](../01-what-is-mcp/index.md) section 3 added a single entry to a host's file. Three entries look like this:

```json
{
  "mcpServers": {
    "files": { "command": "uvx", "args": ["..."] },
    "docs":  { "command": "uvx", "args": ["..."] },
    "db":    { "command": "uvx", "args": ["..."] }
  }
}
```

Three keys, so three clients, and one host reading the file. Those are the three connections in the diagram at the top of this post, and they run through the rest of it. Keep the keys themselves in mind: they are the local identifiers this host chose for these connections, they are yours to rename, and section 6 is about the one job they do that nothing else can.

One honest note, so the vocabulary does not ambush you elsewhere. The wider ecosystem uses "MCP client" loosely to mean "an application that supports MCP", and the community pages that compare Claude Desktop against Cursor against VS Code are usually titled something like *clients*. Those pages are comparing hosts. This series uses the three words exactly as [notation_guide.md](../../notation_guide.md) defines them, and you will not write a client by hand until [Post 10](../10-mcp-client/index.md).

## 4. The server is your code

The server is the process that exposes tools, resources and prompts. It is the box you are here to learn to build, and it is the simplest of the three roles by design: the specification's first principle is that servers should be extremely easy to build, with the host carrying the orchestration.

A server holds the things nobody else should hold. The database password. The scoped application programming interface (API) token. The path to the directory it is allowed to read. It can be a local process the host spawned on your laptop, or a web service on the other side of the internet, and the protocol treats those two the same way. Only the transport differs, which is [Post 04](../04-transports/index.md).

The important property of a server is how little it can see. On any given request, a server gets a method name, some arguments, and per-request metadata. It does not get the conversation. It does not get a list of the other servers the host is connected to. It cannot tell whether the argument it was handed was typed by a human, inferred by the model, copied out of a web page the model just read, or produced by another server's tool result. From inside your process, all four look identical.

That blindness is deliberate. It is what makes servers composable, and it is also exactly why your server cannot be the place where consent is decided. Section 8 returns to that.

## 5. One catalog, built from every connection

Go back to those three servers. When you ask a question, how does the model know which one of them to use?

It does not, and it never finds out.

![A lookup table mapping each name the model sees to a client, a server, and the name that actually goes out on the wire, beside three server cards showing the raw names each server exposes. Two rows are tinted, because two servers both export a tool called search.](diagrams/02-capability-catalog.svg) *The host merges every server's list into one name-to-connection map, and resolves collisions on the way in. Only the contested name changes; the wire name never does.*

Each client asks its own server what it has, using the list methods: `tools/list`, `resources/list`, `prompts/list`. A client may first call `server/discover` to find out which of those the server even supports, since a server that offers no prompts should not be asked for them. Servers must implement `server/discover`; clients are free to skip it.

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

Two consequences follow immediately, and both matter to you as a server author.

**The model picks a tool, not a server.** So when the wrong thing runs, the first place to look is the host's name map and the tool descriptions it fed the model, not the server that got the call. If a call landed on the wrong server, the host routed it there.

**Your tool description is competing.** Your `search_docs` sits in one flat list next to every tool from every other connected server, and the model chooses between them on description text alone. [Post 06](../06-tools-in-depth/index.md) is about writing descriptions that win that comparison honestly.

One detail worth carrying forward: since revision 2026-07-28 a list result must not vary as a side effect of earlier requests on the same connection, so a `connect_database` tool that makes `query` appear in a later `tools/list` is no longer conformant. Filtering by the caller's credentials is still fine; filtering by history is not.

Caching is a separate mechanism rather than a consequence of that rule. Every complete list result carries a freshness hint, `ttlMs`, alongside a sharing hint, `cacheScope`. Both are required by SEP-2549, and both are covered in [Post 07](../07-resources-and-prompts/index.md).

## 6. Name collisions, and the three strategies

Tool names are only guaranteed unique **inside** one server. The specification says so, and then says the obvious next thing: a client or proxy that aggregates tools from several servers may hit a collision, two servers each exposing a `search`, and should implement a disambiguation strategy such as prefixing with a server identifier.

There is a trap hidden in that sentence, and the specification flags it. The name a server reports for itself is **not** guaranteed to be unique across servers, so a host must not key its prefix on that. The identifier that works is the local one you gave the connection in your own configuration file, because you control it and it is unique on your machine.

Hosts pick one of three strategies:

1. **Qualify.** Rewrite the contested name as `<connection>.<tool>` in the catalog and strip the prefix again before sending the call. Both tools stay reachable. This is the strategy the specification points at, and the one a careful host uses. Note that it renames **both** sides rather than only the second one to arrive, so the server that happened to be listed first does not quietly keep the plain name.
2. **Shadow.** First one wins, or last one wins, usually by the order servers appear in the configuration file. One of the two tools silently disappears from the catalog. It is not in the list the model sees, so it is never called, and nothing logs an error.
3. **Refuse.** Drop the duplicate, or refuse to load the second server, and say so. Noisiest, and therefore the kindest.

Some hosts skip the comparison and qualify every name up front, contested or not. That is also defensible, and a little louder, but it costs the model the plain names it may have seen during training or in an earlier turn.

You cannot control which one a host picks, and a server used widely enough will meet all three. So:

- Do not name a tool `search`, `query`, `get`, `run`, or `list`. Name it `search_docs`. Generic names are the ones that collide.
- Tool names should be between 1 and 128 characters, drawn from American Standard Code for Information Interchange (ASCII) letters, digits, underscore, hyphen and dot. Every one of those naming rules is a *should* rather than a *must*, so you will meet names that break them. A separator still has to come from that set, and a slash is not in it, which is why the qualified form is `files.search` and never `files/search`. Model providers then apply their own rules on top, and some narrow the set further, to letters, digits, underscore and hyphen with no dot at all.
- Assume shadowing is possible, and that you will not be told. If a user reports that your tool "does nothing", ask what else they have configured.

Collision handling from the host's side is [Post 11](../11-building-a-host/index.md), which builds the qualifying catalog and tests it. Collision handling as a deliberate attack, where a malicious server picks a name in order to shadow a trusted one, is [Post 19](../19-security/index.md).

## 7. Who controls what

The three primitives differ in a way that is easy to state and easy to get wrong: they differ in **who is allowed to start them**.

![A control matrix showing that the model starts tools, the host starts resources, and the user starts prompts, with the other parties marked as able to block, to take part, or not to choose at all, and a closing note that whoever starts an interaction the host can still refuse it.](diagrams/03-who-controls-what.svg) *One party per row pulls the trigger. Everyone else can at most take part, or refuse.*

**Tools are model-controlled.** The specification says tools are designed so the model can discover and invoke them automatically, based on its contextual understanding and the user's prompts. That is the point of a tool. It also carries a warning: for trust and safety there should always be a human in the loop with the ability to deny an invocation, and applications should make clear which tools are exposed and confirm sensitive operations.

**Resources are application-driven.** The word in the specification is *application-driven*, with host applications determining how to incorporate context based on their needs. A host may show a picker, let the user search a list, or attach resources automatically by heuristic. The model may be part of that decision if the host chooses to involve it, but the model does not reach out and read a resource on its own initiative. This series uses the shorthand "application-controlled" for the same idea.

**Prompts are user-controlled.** They are exposed with the intention that a user can explicitly select them, typically as slash commands or a menu. The specification adds a clarification worth quoting in spirit: this is about who decides *when* a prompt is used, not who writes it. The content is authored by the server; the trigger is pulled by the person.

The practical design rule falls straight out of the table. If you want the model to be able to decide, on its own, to go and get something, that is a tool, even if it feels like data. If you want the host to place something in context, that is a resource. If you want a person to start a piece of work with one click, that is a prompt. [Post 07](../07-resources-and-prompts/index.md) works through the borderline cases.

One clarification, because "controlled by" is doing a lot of work here. It names who may *start* the interaction, not who may *stop* it. The host can refuse all three, and in a well-built host it will sometimes do exactly that.

## 8. Why the host is the security boundary

Put sections 2 and 4 next to each other and the answer is forced.

The host can see the user, the screen, the whole conversation, every connected server, and the model's reasoning about what to do next. Your server can see one request. That asymmetry is not an accident of implementation; it is a stated design principle. Servers should not be able to read the whole conversation nor see into other servers, cross-server interactions are controlled by the host, and the host process enforces the security boundaries.

So the questions that need the whole picture can only be answered in the host:

- Should this server be offered to the model at all, in this conversation?
- Did the argument to this call originate with the user, or with text that arrived from somewhere else?
- Should a human see this call before it runs?
- May the output of this server's tool become the input of that server's tool?

Your server cannot answer any of them, because it does not have the inputs.

This does not let your server off the hook. It moves it to a different hook. The host owns **consent and isolation**; your server owns **authorization and validation** against its own backing system. The host cannot know which rows in your database this token may read, which paths are inside the sandbox, or which arguments are nonsense. You cannot know whether the person watching agreed to any of it. Neither substitutes for the other, and a setup with only one of them is broken in a way that is invisible until it is not.

Two honest caveats before you rely on any of this.

First, nearly all of the host's obligations are written as **should**, not **must**. There should be a human in the loop. Applications should show which tools are exposed. Clients should prompt for confirmation on sensitive operations. A host that quietly auto-approves everything is unhelpful, but it is not non-conformant, and you will not be told which kind of host is calling you.

Second, the boundary has to trust its own inputs. Tool descriptions and annotations are written by servers, and the specification tells clients to treat annotations as untrusted unless the server is trusted. A boundary that must trust text supplied by the thing it is guarding has a door in it. [Post 19](../19-security/index.md) walks through every attack that comes through that door, and it is the reason this post insisted on the vocabulary.

## 9. What this picture no longer contains

If you learned MCP before revision 2026-07-28, your mental model has a connection setup phase in it. Delete it.

There is no `initialize` handshake and no protocol-level session. Both were removed, by Specification Enhancement Proposals SEP-2575 and SEP-2567 respectively, and the words do not appear in the schema. A client does not negotiate once and then remember. It attaches `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` to the `_meta` field of **every single request**, and the server must not rely on anything an earlier request over the same connection established. Up-front discovery, when a client wants it, is one ordinary request called `server/discover`.

The architectural consequence is the one to keep: the client got thinner. It used to guard a negotiated, connection-scoped state that a server could depend on. Now the state that matters travels in each message, which is why a server can be an ordinary web service behind a round-robin load balancer with no stickiness at all. State that genuinely has to survive between calls is passed back as an explicit, server-minted handle and supplied again as an ordinary tool argument.

None of that changes the three roles. It changes how much the middle one has to remember. [Post 03](../03-wire-protocol/index.md) shows the messages.

---

## Common pitfalls

- **Saying "client" when you mean the application.** Cursor is a host. Claude Desktop is a host. Before answering "the client will not connect", ask which of the two the reporter means, and how many clients that host is running.
- **Assuming a client can be shared.** There is no connection pool spanning servers, and no client that multiplexes two of them. One client, one server, both directions. If your design needs a client to talk to two servers, what you are designing is a host.
- **Expecting the model to pick a server.** It picks a name out of a flat catalog. When the wrong server runs, inspect the host's name map and your tool descriptions, not the model's reasoning.
- **Shipping a tool called `search`.** It will collide, and on a shadowing host it will vanish from the catalog with no error anywhere. Nobody can warn you, because the collision happens on someone else's machine against someone else's configuration.
- **Building a resource for something the model is supposed to fetch by itself.** Resources are application-driven. If the decision to go and get the data belongs to the model, it is a tool.
- **Treating the host's approval dialog as your security control.** It is a *should*, it lives in software you did not write, and it can be switched off. Your server still needs authorization, validation, and least-privilege credentials of its own.
- **Carrying an initialization handshake over from an older tutorial.** There is no `initialize` in 2026-07-28. If your diagram has a setup phase, it is a revision behind.

---

## Further reading

- Specification, *"Architecture"*, revision 2026-07-28. The host, client and server job descriptions, and the four design principles quoted in sections 2, 4 and 8.
- Specification, *"Tools"*, *"Resources"*, *"Prompts"*, revision 2026-07-28. Each page opens with a "User Interaction Model" section, which is where model-controlled, application-driven and user-controlled are defined.
- Specification, *"Tools"* § Tool Names, revision 2026-07-28. Uniqueness scoping, the allowed character set, and the warning against keying disambiguation on a server's self-reported name.
- SEP-2575, *"Stateless MCP"*, and SEP-2567, *"Sessionless MCP"* (2026).
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 03 — The wire protocol: JSON-RPC, discovery, and the stateless model](../03-wire-protocol/index.md)**: the same three boxes, with every message drawn in and every `_meta` key named.
- **[Post 05 — Your first MCP server](../05-first-server/index.md)**: build the terracotta box on the right of the hero diagram, and watch a host discover it.
