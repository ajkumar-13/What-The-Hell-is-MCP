# 01 · What MCP is, and the problem it solves

> **TL;DR.** Connecting *N* artificial-intelligence applications to *M* data sources by hand
> costs *N* × *M* bespoke integrations, and the Model Context Protocol (MCP) collapses that
> into *N* + *M* by putting one agreed boundary in the middle. A server publishes tools,
> resources, and prompts across that boundary, and any host that speaks MCP can use them
> without knowing anything about how the server is built. MCP standardizes access and only
> access: it adds no intelligence and it defines no privacy boundary. This series teaches
> protocol revision 2026-07-28 and nothing else.
>
> **After reading this you will be able to:**
> - Count the integrations a given set of applications and data sources costs, with and without a shared protocol.
> - Name the three MCP roles, host, client, and server, and say which one owns the consent decision.
> - State what MCP does not give you, so you can plan the parts you still have to build yourself.

![Four AI applications on the left joined to six data sources on the right by twenty-four separate crossing lines.](diagrams/01-n-times-m.svg)
*Four applications and six data sources, wired pair by pair, cost twenty-four separate integrations.*

---

## 1. The workflow everyone recognizes

A service is failing. You describe the symptom to an AI assistant and it comes back with a
sensible list of things to check. Then it asks you to paste the logs.

So you open a terminal, tail the log, select four hundred lines, and paste them into the
chat window. The model reads them and asks what the connection pool looks like. You open a
second terminal, run a query, copy the result, paste it. It asks which deploy went out this
morning. You switch to a browser tab, find the commit, paste that too.

Nothing in that loop is reasoning. It is you, moving bytes by hand between systems that hold
the data and a model that could use it. You are the integration, and you are a slow one.

The same shape shows up everywhere. A coding agent that cannot read your issue tracker. A
support bot that cannot look up an order. A terminal assistant that will happily write the
query but cannot run it. In every case the model is not short of reasoning. It is short of
access.

The fix looks obvious from one angle: give the application a way to fetch the log itself.
That is an integration. Integrations are where the interesting problem starts, and the
problem is arithmetic.

## 2. The N × M integration problem, counted

Picture a small team. They use four AI applications:

- a desktop chat assistant
- a coding agent inside the editor
- a terminal agent that runs in their build pipeline
- an internal support bot somebody on the team wrote

They would like those four to reach six systems: PostgreSQL, Slack, GitHub, Jira, the local
filesystem, and an internal metrics service.

Wire them pair by pair, as the hero figure above does, and the bill is:

```text
4 applications × 6 data sources = 24 integrations
```

Twenty-four is not twenty-four function calls. Each integration is a plugin format specific
to that one application, an authentication scheme specific to that one system, a description
of the capability written so a model can understand when to use it, error handling, a
release process, and a person who fixes it when the vendor changes an API (application
programming interface).

The growth is the painful part. Add a fifth application and you owe six new integrations,
one per data source, for a total of thirty. Then add a seventh data source and you owe five
more, one per application, for thirty-five. Every new item on one side multiplies by the
size of the other side. In general, *N* applications and *M* data sources cost *N* × *M*,
the marginal cost of one more application is *M*, and the marginal cost of one more data
source is *N*.

**Nobody actually pays that bill, and that is the real symptom.** In practice three or four
popular pairs get built, usually by whoever has the largest user base to justify the work.
The other twenty never exist. So *N* × *M* rarely appears as a budget line. It appears as
the set of things your tools cannot do, and the copy-paste loop in section 1 is what fills
the gap.

Now put one agreed interface in the middle instead. Each application implements the protocol
once. Each system gets one server, written once, that any of those applications can use.

```text
4 applications + 6 data sources = 10 implementations
```

Ten instead of twenty-four, and, more importantly, an eleventh item costs one piece of work
rather than six.

**Two honest qualifications.** First, *N* + *M* counts implementations to write and maintain,
not connections at run time. All twenty-four paths still exist when the team is working; they
simply travel over one shared interface instead of twenty-four private ones. Second, you
almost never write both sides. The application vendor writes its half once, and a large
number of the *M* already exist as open-source servers you can install, so your own marginal
cost is often one server or zero.

And the saving is conditional. A protocol that only one application speaks saves nothing at
all. The arithmetic works because enough hosts have adopted MCP that writing one server is a
reasonable bet, not because *N* + *M* is a smaller number on a whiteboard.

## 3. What a protocol boundary buys you

![The same four applications and six data sources, each connected once to a single central panel labeled Model Context Protocol.](diagrams/02-one-boundary.svg)
*The same nodes and the same positions, with one boundary in the middle instead of a mesh.*

A protocol boundary is a written agreement about the shape of the messages that cross a line,
precise enough that either side can be replaced without telling the other. MCP is that
agreement for the line between an AI application and the systems it wants to reach. It fixes
five things and deliberately leaves everything else alone:

1. How a client asks a server what it can do (`server/discover`, then `tools/list`).
2. How a client invokes one of those things (`tools/call`).
3. What a result looks like coming back.
4. How failures are reported.
5. How the bytes move, through one of two transports: standard input and output for a local
   subprocess, or Streamable HTTP (Hypertext Transfer Protocol) for anything over a network.

Everything in MCP travels as JSON-RPC 2.0, which is about as small as a remote-call
convention gets: a JavaScript Object Notation (JSON) object naming a method, an object of
parameters, and an id you use to match the answer back to the question. Here is the whole of
a tool call, taken from the specification's tools page:

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

Two things to flag, both of which get a full treatment in post 03. The specification's
examples omit a required `_meta` block for readability; a real request carries the protocol
revision and the client's capabilities there, on every single request, because in this
revision there is no handshake to carry them once. And `resultType` is on every result:
`"complete"` here, but a server can also answer that it needs something from the user before
it can finish.

That is the entire boundary, and three useful properties fall out of it.

**Substitution.** Swap the desktop assistant for a different one and your server does not
change. Swap PostgreSQL for something else behind the server and the applications do not
change. The line in the middle is the only contract either side has to honor.

**Reach.** You write the server once for the capability, not once per application. This is
the whole of the *N* + *M* claim, expressed as engineering rather than arithmetic.

**Debuggability.** Because it is JSON over a documented transport, a failing call is
reproducible by hand. You can capture the request that went wrong and replay it with a
command-line HTTP client, with no host and no model anywhere in the picture. That is worth
more than it sounds like on the day something breaks.

## 4. The three roles in one picture

![A host panel containing the user, the model, and a client, separated by a dashed trust boundary from a server panel, which reaches a data source outside MCP.](diagrams/03-three-roles.svg)
*The host owns the model and the consent decision; the client owns the connection; the server owns the capability.*

MCP has exactly three roles, and confusing the first two causes more wasted debugging time
than any other mistake in this ecosystem.

**Host.** The application the user actually interacts with. Claude Desktop, Cursor, VS Code,
a command-line interface you write yourself. The host owns the model, owns the conversation,
and owns every permission decision. When something asks "are you sure you want to let this
tool run", that is the host talking.

**Client.** The protocol-speaking object living inside the host. There is exactly one client
per connected server. It owns the transport, matches responses back to requests by id, and
caches what the server said it offers. It is infrastructure. You will not write one by hand
until [Post 10](../10-mcp-client/index.md), and most readers never need to.

**Server.** The process that publishes tools, resources, and prompts. Usually this is your
code. Note what the word does *not* imply: a server is not necessarily remote and not
necessarily a web service. A host will often spawn your server as an ordinary subprocess on
the same machine and talk to it over a pipe.

Behind the server sits the actual data source, and it is outside MCP entirely. The protocol
has no opinion about your database driver, your file paths, or your API keys. It only cares
about what your server chooses to publish.

The dashed line in the figure is the trust boundary, and it is worth being blunt about which
way it faces. Everything a server sends back, including the descriptions of its own tools, is
untrusted input as far as the host is concerned. The specification is explicit that there
**SHOULD** always be a human in the loop with the ability to deny a tool invocation, and that
clients **MUST** treat tool annotations as untrusted unless the server is trusted. The host,
not the server, is the security boundary. Post 19 is entirely about what happens when people
forget this.

One vocabulary note that will save you confusion. In casual conversation, and in a great deal
of documentation, "MCP client" is used loosely to mean the whole application. In this series
it never does. Host is the application, client is the connection object inside it, and the
two words are never swapped.

## 5. What a server exposes: tools, resources, and prompts

A server publishes three kinds of thing. They differ mainly in who decides to use them.

| Primitive | For | Who pulls the trigger | Covered in |
|---|---|---|---|
| **Tools** | doing something | the model | Post 06 |
| **Resources** | reading something | the application | Post 07 |
| **Prompts** | starting something | the user | Post 07 |

**Tools** are functions with a name, a description, and a JSON Schema describing their
arguments. The specification calls them model-controlled: the model reads the descriptions
and decides when a tool is worth calling. `get_weather`, `run_query`, `create_issue`.

**Resources** are readable things addressed by a URI (uniform resource identifier), such as
`file:///var/log/app.log` or a database schema. The application, not the model, decides which
resources to attach to the conversation, which is a different control flow from a tool call
even though both end up as text in front of a model.

**Prompts** are named, reusable entry points that a user picks deliberately, usually from a
menu or a slash command. They expand into a message with context already attached.

That is as far as this post goes. The distinction between the three, and the design mistake
of turning everything into a tool because tools are the easiest to write, is the subject of
posts 06 and 07.

## 6. What MCP explicitly does not do

Three disclaimers, all of which matter more than most introductions admit.

**It adds no intelligence.** The protocol carries a name, some arguments, and a result.
Nothing in it decides which tool to call, or notices that calling it was a bad idea. If the
model picks the wrong tool, MCP has no opinion. This is why a tool's description and schema
are so consequential: they are the entire user interface the model gets, and a badly written
description is a bug you will observe as "the model never uses my tool".

**It defines no privacy boundary.** This one deserves care, because the word "server" misleads
people in both directions. A server is a role, not a location. Many MCP servers run as a
subprocess on your own laptop, holding your own credentials, reading your own files, with no
third party in the path at all. That is genuinely useful and it is the normal setup for local
development.

But a local server does not imply a local model. If your host is talking to a hosted model,
then everything your tool returns, and that the host puts into the conversation, is sent to
that model provider like any other message. MCP standardizes how the data is *reached*.
Whether the data leaves your machine is decided by your host and your model configuration,
and the protocol has nothing to say about it.

**It enforces no safety.** A tool can declare itself read-only. Nothing checks. Annotations
are hints for the host's benefit and the specification says plainly that clients must treat
them as untrusted. The protocol will not stop a server from deleting a file when a tool named
`list_files` is called, will not tell you that a server changed its tool descriptions
overnight, and does not authenticate the server to you. Those are real problems with real
answers, and the answers live in your server code and your host's permission model rather
than in the wire format.

## 7. Where the protocol came from, and who owns it now

Anthropic introduced MCP as an open standard in November 2024, alongside open-source software
development kits (SDKs) and reference server implementations. In December 2025 Anthropic
donated it to the Agentic AI Foundation, described in the announcement as "a directed fund
under the Linux Foundation", where MCP is a founding project alongside Block's goose and
OpenAI's AGENTS.md. The foundation was co-founded by Anthropic, Block, and OpenAI, with
support from Google, Microsoft, Amazon Web Services, Cloudflare, and Bloomberg.

The announcement is careful to say that the day-to-day did not change: "For MCP little
changes. The governance model we introduced earlier this year continues as is." What the
donation does buy is a neutral home. The specification is not one company's roadmap, and it
changes through a public proposal process. Those proposals are called Specification
Enhancement Proposals (SEPs), they are numbered, and this series cites them by number when
explaining why something is the way it is.

Revisions are named by date rather than by version number. **This series targets 2026-07-28
and only 2026-07-28.** That matters for a specific reason: this revision made the protocol
stateless. The `initialize` handshake is gone, protocol sessions and the `Mcp-Session-Id`
header are gone, and there is no longer a channel for a server to send a request back to a
client. So a tutorial written against an earlier revision will teach you a handshake that no
longer exists and a session identifier that no server will honor, and it will look correct
while doing it. Check the date on anything you read about MCP, including this.

## 8. How to read this series

Twenty-four posts in six parts. Read them in order if MCP is new to you; each part also
stands alone.

| Part | Posts | What it gives you |
|---|---|---|
| **I. Foundations** | 01 to 04 | The mental model, the wire format, and the two transports. No code yet. |
| **II. Building servers** | 05 to 09 | A working server, then tools, resources, prompts, mid-call questions, and long-running work. |
| **III. Clients, hosts, and testing** | 10 to 12 | The other side of the boundary: write a client, write a host, and test both. |
| **IV. Projects** | 13 to 18 | Three real servers built end to end: a database analyst, a DevOps responder, a research browser. |
| **V. Production** | 19 to 22 | The attacks the protocol does not stop, OAuth 2.1, deployment, and publishing. |
| **VI. Interoperability and the frontier** | 23 and 24 | One server proven against every major host, then extensions and MCP Apps. |

If you have never built a server, read Part I in order and then go straight to
[Post 05](../05-first-server/index.md). If you already run a server written against an older
revision, the three posts that will change your code are 03, 08, and 09.

Two versions are pinned throughout, and both are named here so you can check them against
whatever you have installed. The protocol revision is 2026-07-28. The Python SDK is
`mcp==2.0.0b2`, which was a beta at the time of writing; every project pins the exact version
it was tested against, and where the SDK had not yet caught up with the specification, the
post says so rather than guessing.

---

## Common pitfalls

- **Calling the host "the client".** Almost every confusing MCP conversation traces back to
  this. The host is the application; the client is the connection object inside it. When a
  document says "the client asks the user", it usually means the host.
- **Assuming a local server keeps your data local.** It keeps the *access* local. If the host
  is using a hosted model, tool output that reaches the conversation reaches the provider.
  Decide this at the host level, not by looking at where the server process runs.
- **Expecting MCP to improve tool selection.** If the model calls the wrong tool, the fix is
  almost always the tool's name, description, or schema. The protocol has no layer that could
  help.
- **Following a tutorial without checking its date.** Anything that opens with an `initialize`
  handshake, an `Mcp-Session-Id` header, or a server calling `sampling/createMessage` is
  written against a revision this series does not teach. It is not wrong for its own era, but
  it will not run against a 2026-07-28 server.
- **Treating annotations such as `readOnlyHint` as enforcement.** They are hints from a party
  the host has been told not to trust. Enforce read-only in your server and in your database
  grants.
- **Reaching for a tool when the answer is a resource.** New servers tend to expose everything
  as a tool because tools are the easiest primitive to write. That fills the model's context
  with things that should have been readable on demand.
- **Counting *N* + *M* as a saving you personally collect.** You collect it only for the hosts
  that already speak MCP. For anything else, you are still writing a bespoke integration and
  should say so out loud when estimating.

---

## Further reading

- Model Context Protocol, *"Specification: versioning and revision index"* (2026). While
  2026-07-28 was a release candidate its pages were served from `/specification/draft/`, so
  check both paths if a deep link fails.
- Model Context Protocol, *"2026-07-28 changelog"* (2026). The authoritative list of what this
  revision removed.
- Model Context Protocol, *"Tools"*, revision 2026-07-28. Source of the `tools/call` request
  and result shown above.
- Model Context Protocol, *"MCP joins the Agentic AI Foundation"* (2025). The donation
  announcement quoted in section 7.
- Linux Foundation, *"Formation of the Agentic AI Foundation"* (2025).
- JSON-RPC 2.0 specification. The entire wire grammar MCP is built on, and short enough to
  read in one sitting.

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 02 — The architecture: hosts, clients, and servers](../02-architecture/index.md)**: the three roles at full depth, including how a host turns many servers into one catalog of tools and what happens when two servers claim the same tool name.
- **[Post 03 — The wire protocol: JSON-RPC, discovery, and the stateless model](../03-wire-protocol/index.md)**: the `_meta` block this post skipped, `server/discover`, `resultType`, and a complete annotated trace you can reproduce with a command-line HTTP client.
