# The Model Context Protocol — Free Blog Series

A complete, free, beginner-first series on the **Model Context Protocol (MCP)** — the
open standard that lets AI applications connect to tools and data, taught from first
principles against protocol revision **2026-07-28**.

This document is the **single source of truth** for the series:

- The repository layout
- The 24-post outline, with thesis, sections, diagrams, code, and references for each post
- The diagram system, writing style, and review checklist
- The list of reference assets

When in doubt, edit this file *first*, then update the posts.

> Structure deliberately mirrors the sibling "from scratch" series (`ME_IT`, `nnfs`,
> `rnn_from_scratch`) so the four repositories read as one shelf.

---

## 1. Goals and audience

**Goal.** Produce the most gentle *and* most complete free on-ramp to MCP: the resource a
working developer with no protocol background can read top to bottom and come out able to
design, build, secure, and publish a server that real hosts connect to. Good enough to be
the default link people share when someone asks "how do I actually learn MCP?"

**Audience.** Three personas, served by the same posts at different depths:

1. **Application developers** who have used AI tools and want to extend them. They are the
   protagonist. Every idea is introduced from zero, with a picture before any wire format.
2. **Platform and infrastructure engineers** who need to deploy, secure, and operate MCP
   servers for a team. They can skim Parts I and II and live in Parts V and VI.
3. **Architects evaluating MCP** who want to know what the protocol does and does not
   guarantee before adopting it. Parts I, V, and VI answer them.

**Prerequisites, kept deliberately tiny.**
- Comfort reading Python: functions, classes, `async`/`await`.
- Willingness to run a command in a terminal.
- *No* prior protocol, networking, OAuth, or AI-engineering knowledge assumed. JSON-RPC,
  OAuth 2.1, and the transport layer are each built up from scratch where they are needed.

**Pedagogical principles.**
- Every post answers one question.
- **Picture, then intuition, then wire format, then code**, in that order, always.
- The wire format comes *before* the SDK call that produces it, so a reader can debug with
  `curl` and a text editor rather than trusting a library.
- Every post has at least one diagram and at least one runnable artifact.
- Every non-obvious claim is cited to a primary source, and every number is either cited or
  reproducible from [code/](code/).
- No hype and no marketing voice. Neutral, textbook tone, but warm. Second person is welcome.
- Every acronym is expanded on first use **in every post**; no assumption of linear reading.
- **No invented output.** A transcript in a post is a transcript that was produced.

**Non-goals.** Not a survey of every MCP server in the wild, not an SDK API reference, and
not a comparison shootout against other agent protocols.

---

## 2. The one big decision: this series targets 2026-07-28

MCP's **2026-07-28** revision is the largest change since launch, and this series teaches it
exclusively rather than teaching an older revision and bolting on a migration chapter.

What that means concretely, and what the author of every post must internalize:

| There is no… | Because | Teach instead |
|---|---|---|
| `initialize` handshake | Removed (SEP-2575) | `server/discover`, and per-request `_meta` |
| `Mcp-Session-Id`, protocol session | Removed (SEP-2567) | Server-minted handles passed as ordinary tool arguments |
| Server-to-client request channel | Replaced by MRTR (SEP-2322) | `InputRequiredResult` plus an `inputResponses` retry |
| `ping` | Removed | Nothing; the transport tells you |
| `logging/setLevel` | Removed | `io.modelcontextprotocol/logLevel` in `_meta`, plus stderr |
| SSE resumability, `Last-Event-ID` | Removed | Re-issue the request with a new id |
| `resources/subscribe` | Replaced | `subscriptions/listen` |

Deprecated but still present, and therefore taught only as "you will meet this in older
code": **roots**, **sampling**, **logging**, **HTTP+SSE transport**, and **Dynamic Client
Registration**.

**Consequences for the 13-post series this replaces.** The previous posts were written
against revision 2025-03-26. The old Post 10's entire premise, MCP Sampling, is a deprecated
feature with no back channel left to travel over, so that material is rewritten around
servers calling a model provider directly. Anything that depended on session state is
rewritten around explicit handles.

**Honesty rule.** Where the RC text is ambiguous, or where an SDK has not caught up, the
post says so in a short note rather than inventing a confident answer. Research notes with
per-claim verification status live in `.research/` and are not published.

---

## 3. Repository layout

```
what-the-hell-is-mcp/
├── README.md                          # Series overview and table of contents
├── PLAN.md                            # This file (the master plan)
├── GLOSSARY.md                        # One-page glossary, alphabetized
├── CHEATSHEET.md                      # One-page printable reference
├── REFERENCES.md                      # Master bibliography
├── CONTRIBUTING.md                    # Style guide, diagram standards, PR rules
├── notation_guide.md                  # Naming and typography conventions
├── LICENSE                            # CC-BY 4.0 prose, MIT code
│
├── assets/
│   ├── diagrams/
│   │   ├── style/tokens.css           # The --mcp-* design tokens
│   │   └── exports/                   # Cross-post diagrams reused by several posts
│   └── poster/                        # "One page of MCP" single-sheet SVG
│
├── posts/
│   ├── 01-what-is-mcp/
│   │   ├── index.md                   # The post body. No frontmatter block.
│   │   ├── frontmatter.yaml           # slug, title, date, tags, hero, reading_time, part
│   │   ├── diagrams/                  # Post-local SVGs, numbered from 01
│   │   └── snippets/                  # Short code shown inline in the post
│   ├── 02-architecture/
│   ⋮
│   └── 24-mcp-apps-and-frontier/
│
├── code/                              # Full runnable companions
│   ├── 05-first-server/
│   ├── 10-mcp-client/
│   ├── 13-postgres-analyst/           # Project 1 (posts 13 and 14)
│   ├── 15-devops-responder/           # Project 2 (posts 15 and 16)
│   ├── 17-research-browser/           # Project 3 (posts 17 and 18)
│   └── 23-knowledge-base/             # Project 4 (post 23)
│
├── templates/
│   ├── post-template.md
│   └── diagram-style-guide.md
│
└── verify/
    ├── capture.py                     # regenerates every measured value the posts quote
    └── RESULTS.md                     # its output, committed, diffed against the posts
```

**Ground truth before prose.** No number appears in a post unless `verify/capture.py` can
produce it again. Schemas, elicitation outcomes, error messages, and the test count all come
from there. If a post and `verify/RESULTS.md` disagree, the post is wrong.

**Naming rule:** `posts/NN-kebab-case-slug/`. `NN` is a stable two-digit number. The slug
never changes after publishing, for URL stability. `frontmatter.yaml` is a **sidecar**:
`index.md` never carries a `---` block and never restates reading time.

---

## 4. The series at a glance

24 posts in 6 parts. Reading order is linear, but each part is a useful standalone unit.

| #   | Part                                | Title                                                              |
| --- | ----------------------------------- | ------------------------------------------------------------------ |
| 01  | I — Foundations                     | What MCP is, and the problem it solves                              |
| 02  | I                                   | The architecture: hosts, clients, and servers                       |
| 03  | I                                   | The wire protocol: JSON-RPC, discovery, and the stateless model     |
| 04  | I                                   | Transports: stdio and Streamable HTTP                               |
| 05  | II — Building servers               | Your first MCP server                                               |
| 06  | II                                  | Tools in depth: schemas, structured output, and annotations         |
| 07  | II                                  | Resources and prompts: the primitives that are not tools            |
| 08  | II                                  | Elicitation and MRTR: asking the user mid-call                      |
| 09  | II                                  | Tasks: work that outlives a single request                          |
| 10  | III — Clients, hosts, and testing   | Building your own MCP client                                        |
| 11  | III                                 | Building a host: the tool loop, many servers, and permissions       |
| 12  | III                                 | Testing and debugging MCP                                           |
| 13  | IV — Projects                       | Project 1 · A secure database analyst                               |
| 14  | IV                                  | Project 1 · Writes, transactions, and an audit trail                |
| 15  | IV                                  | Project 2 · A DevOps first responder                                |
| 16  | IV                                  | Project 2 · Safe remediation with approval                          |
| 17  | IV                                  | Project 3 · A deep research browser                                 |
| 18  | IV                                  | Project 3 · Server-side model calls and multi-page research         |
| 19  | V — Production                      | Security: the attacks the protocol does not stop                    |
| 20  | V                                   | Authorization: OAuth 2.1 for MCP servers                            |
| 21  | V                                   | Deploying to production: containers, scaling, and observability     |
| 22  | V                                   | Publishing: the registry, `server.json`, and MCPB bundles           |
| 23  | VI — Interoperability and frontier  | Project 4 · One server, every client                                |
| 24  | VI                                  | MCP Apps, extensions, and where the protocol goes next              |

Reference assets shipped alongside:
- `GLOSSARY.md` — every term used in any post, alphabetized, one line each.
- `CHEATSHEET.md` — single page: the method table, the `_meta` keys, the MRTR loop, the
  security checklist, and the client configuration matrix.
- `assets/poster/one-page-of-mcp.svg` — every concept on one canvas, designed to print at A2.

---

## 5. Diagram system

Goal: posts should look like a small, well-designed book, not a wiki dump. MCP is a
*protocol*, so the teaching load falls on sequence diagrams, message shapes, and boundary
diagrams more than on any other kind of figure.

**Toolchain.** Hand-edited SVG only. No Mermaid, no Excalidraw, no screenshots of text.
Screenshots of real host interfaces are permitted where the interface is the subject, and
must be scrubbed of usernames, paths, and personal data.

**Style tokens** are defined once in [assets/diagrams/style/tokens.css](assets/diagrams/style/tokens.css)
and inlined into every SVG. Full rules in
[templates/diagram-style-guide.md](templates/diagram-style-guide.md). The short version:

- Client side is `--mcp-primary` (blue), server side is `--mcp-accent` (terracotta), and a
  request always flows blue to terracotta.
- Strokes are 1.5, 1.0, and 0.75, and nothing else.
- `viewBox` only, no `width` or `height`. `<title>` and `<desc>` mandatory.
- Light and dark mode via an inlined `prefers-color-scheme` block.
- **No emoji.** Draw the shape.

**Recurring hero diagrams**, drawn once and reused across posts:

1. **The protocol boundary** (host, client, server, data) — posts 01, 02, 11.
2. **The stateless request** (everything the server needs, in one message) — posts 03, 04, 21.
3. **The MRTR loop** (call, `input_required`, retry with responses) — posts 08, 09, 14, 16.
4. **The three primitives** (tools, resources, prompts, and who controls each) — posts 02, 06, 07.
5. **The trust boundary** (what the protocol guarantees against what it does not) — posts 19, 20.
6. **One page of MCP** — the poster, and the "you are here" mini-map.

---

## 6. Per-post specs

Format for each: **Thesis** (one sentence the post must prove) · **Sections** (the H2
outline) · **Diagrams** · **Code** · **References**.

### Part I — Foundations

#### 01. What MCP is, and the problem it solves
- **Thesis.** Connecting *N* AI applications to *M* data sources costs *N* times *M* bespoke
  integrations; MCP replaces that with one protocol boundary, turning the cost into *N* plus *M*.
- **Sections.** The copy-paste workflow everyone recognizes · The N times M integration
  problem, counted · What a protocol boundary buys you · The three roles in one picture ·
  What a server exposes: tools, resources, prompts · What MCP explicitly does *not* do (it
  adds no intelligence and defines no privacy boundary) · Where the protocol came from and
  who owns it now · How to read this series.
- **Diagrams.** (a) **Hero, before: the N times M mesh.** (b) After: the same nodes through
  one boundary. (c) The three roles, with the trust boundary marked.
- **Code.** None. Set expectations and name the versions the series pins.
- **References.** Spec index; the Agentic AI Foundation donation announcement.

#### 02. The architecture: hosts, clients, and servers
- **Thesis.** Three roles with sharply different jobs, the host decides, the client
  transports, the server provides, and almost every MCP misunderstanding is a confusion
  between the first two.
- **Sections.** Host: owns the model, the conversation, and consent · Client: one per server
  connection, owns transport and request correlation · Server: your code · The unified
  capability catalog, and how a host maps a tool name back to a connection · Name collisions
  and the three strategies · Who controls what: model-controlled tools, application-controlled
  resources, user-controlled prompts · Why the host is the security boundary, not the server.
- **Diagrams.** (a) **Hero, one host, three clients, three servers.** (b) The capability
  catalog as a lookup table. (c) Control matrix: which party decides to use each primitive.
- **Code.** None; this is the mental-model post.
- **References.** Spec architecture page; client matrix.

#### 03. The wire protocol: JSON-RPC, discovery, and the stateless model
- **Thesis.** Since 2026-07-28 every MCP request is self-describing and independent, which is
  what makes servers ordinary, horizontally scalable web services.
- **Sections.** JSON-RPC 2.0 in ten minutes: request, response, error, notification ·
  `server/discover`, and what replaced capability negotiation · The `_meta` contract, key by
  key · `resultType`, and why every result now carries one · A complete annotated trace ·
  What statelessness costs you, and the handle pattern that pays it back · Errors: protocol
  errors against tool execution errors.
- **Diagrams.** (a) **Hero, one self-describing request**, every `_meta` key called out.
  (b) Stateful against stateless, side by side, with a load balancer. (c) The error taxonomy.
- **Code.** `snippets/raw_discover.py`, talking to a server with `httpx` and no SDK at all.
- **References.** Draft spec basic pages; SEP-2567; SEP-2575; changelog.

#### 04. Transports: stdio and Streamable HTTP
- **Thesis.** The transport is the only part of MCP that knows about processes and sockets;
  choose it by deployment shape, not by preference.
- **Sections.** stdio: the host spawns your process · Why you must never write to stdout ·
  Streamable HTTP: one endpoint, ordinary POSTs · The headers, one by one · Why round-robin
  load balancing works now · Choosing between them · What was removed, and what to do when
  you meet an HTTP+SSE server.
- **Diagrams.** (a) **Hero, the two transports side by side.** (b) The stdio process model
  and the stdout trap. (c) Streamable HTTP behind a load balancer.
- **Code.** `snippets/stdio_hello.py`, `snippets/http_hello.py`.
- **References.** Draft transports page; deprecated registry; SEP-2243.

### Part II — Building servers

#### 05. Your first MCP server
- **Thesis.** A useful MCP server is a normal Python file with decorators, and the protocol
  disappears once the SDK is doing its job.
- **Sections.** What we are building and why it is a good first server · Project setup with
  `uv` · The smallest server that runs · The first tool · A tool with arguments, and how type
  hints become a schema · Connecting it to a host · Watching the messages go past ·
  Troubleshooting, honestly.
- **Diagrams.** (a) **Hero, your file, the host, and the pipe between them.** (b) Python
  signature to JSON Schema, annotated. (c) The troubleshooting decision tree.
- **Code.** `code/05-first-server/`, a system-information server, runnable, with tests.
- **References.** Python SDK docs; spec tools page.

#### 06. Tools in depth: schemas, structured output, and annotations
- **Thesis.** A tool's schema is its user interface for the model, and the difference between
  a tool that gets called correctly and one that does not is almost always schema design.
- **Sections.** Anatomy of a `Tool` object, field by field · Input schemas that models get
  right · `outputSchema` and `structuredContent` · Content blocks: text, image, audio,
  resource links · Errors that teach the model to retry · Annotations, and why they are hints
  and not enforcement · Naming and description rules · Deterministic ordering and prompt
  caching.
- **Diagrams.** (a) **Hero, the anatomy of a tool call**, request and result annotated.
  (b) Unstructured against structured output. (c) Protocol error against execution error.
- **Code.** `code/05-first-server/` extended; a schema-design before and after.
- **References.** Draft tools page; SEP-986; the tool-annotations post.

#### 07. Resources and prompts: the primitives that are not tools
- **Thesis.** Tools are for doing, resources are for reading, and prompts are for starting;
  using a tool where a resource belongs is the most common design mistake in MCP servers.
- **Sections.** Why not make everything a tool · Resources, and who decides to read one ·
  URI design and templates · Completions · Change notification with `subscriptions/listen` ·
  Caching with `ttlMs` and `cacheScope` · Prompts as user-invoked entry points · Icons and
  titles · A decision table: tool, resource, or prompt.
- **Diagrams.** (a) **Hero, the three primitives and who pulls each trigger.** (b) A URI
  template resolving. (c) The subscription flow.
- **Code.** `code/05-first-server/` extended with a templated resource and a prompt.
- **References.** Draft resources, prompts, completion, and pagination pages; SEP-2549.

#### 08. Elicitation and MRTR: asking the user mid-call
- **Thesis.** A server can still ask the user a question mid-tool-call, but it now does so by
  *returning* rather than *calling*, and understanding that inversion is the key to the
  2026-07-28 protocol.
- **Sections.** The problem: a tool that cannot finish without a human · How this used to
  work, and why the back channel had to go · MRTR: return `input_required`, get retried · The
  complete message loop · Carrying state across round trips without a session · Schema rules
  for what you can ask · Accept, decline, cancel · URL mode, for OAuth and payments ·
  Designing questions a user can actually answer.
- **Diagrams.** (a) **Hero, the MRTR loop**, every message in order. (b) The old back-channel
  model against return-and-retry, side by side. (c) The three outcomes.
- **Code.** `code/05-first-server/` gains a tool that confirms before acting.
- **References.** Draft elicitation page; SEP-2322; SEP-1036; SEP-1034.

#### 09. Tasks: work that outlives a single request
- **Thesis.** Some work takes minutes, and the tasks extension turns "hold the connection
  open and hope" into an explicit, pollable, cancellable lifecycle.
- **Sections.** What extensions are, and the reverse-DNS naming rule · Declaring the tasks
  extension in per-request capabilities · Why task creation is **server-directed**, with no
  per-tool declaration and no per-request opt-in flag · Being ready for either result shape
  on any `tools/call` · The task lifecycle · Polling with `tasks/get` · `tasks/update` and
  `tasks/cancel` · Tasks against MRTR, and the distinction that trips people up · When a
  task is the wrong answer.
- **Correction to carry.** `execution.taskSupport` was the **2025-11-25** mechanism and was
  deliberately removed (SEP-2663). Any tutorial still teaching it is a revision behind, and
  saying so is part of the post's value.
- **Diagrams.** (a) **Hero, the task state machine.** (b) A synchronous call against a task,
  on a timeline. (c) Extension negotiation.
- **Code.** A long-running tool in `code/05-first-server/`.
- **References.** Extensions overview; SEP-2133; tasks extension spec; SEP-2663.

### Part III — Clients, hosts, and testing

#### 10. Building your own MCP client
- **Thesis.** Writing a client once removes all the remaining magic: it is a few hundred
  lines, and afterwards every host behavior you see is explicable.
- **Sections.** Client against host, one more time · Connecting over stdio · Connecting over
  Streamable HTTP · Discovery and listing · Calling a tool and reading the result properly ·
  Handling `input_required` · Reading resources and rendering prompts · Cleaning up.
- **Diagrams.** (a) **Hero, client internals**: transport, correlation, capability cache.
  (b) Result handling, including the `isError` branch everyone forgets.
- **Code.** `code/10-mcp-client/`, a complete client library plus a CLI.
- **References.** Python SDK client docs; draft lifecycle.

#### 11. Building a host: the tool loop, many servers, and permissions
- **Thesis.** A host is a loop around a model plus a permission gate, and the gate is the part
  that matters.
- **Sections.** The tool-execution loop, step by step · Translating MCP schemas into a model
  provider's tool format · Parallel tool calls · Connecting to several servers at once · Name
  collisions in practice · The permission layer: what to prompt for and what to remember ·
  Conversation and token budget · Streaming and feedback.
- **Diagrams.** (a) **Hero, the tool loop** with the permission gate on the critical path.
  (b) Many servers, one catalog. (c) Where a prompt-injection attack enters this loop.
- **Code.** `code/10-mcp-client/` grows a multi-server host CLI.
- **References.** Provider tool-use docs; the containment engineering post.

#### 12. Testing and debugging MCP
- **Thesis.** MCP servers are testable like any other library, and the reason so few are
  tested is that nobody shows the in-memory pattern.
- **Sections.** In-memory testing without a subprocess · Asserting on schemas, not just
  results · Testing the MRTR path · The Inspector · Reading a wire trace · The conformance
  suite · Continuous integration for a server · The five failures that account for most bug
  reports.
- **Diagrams.** (a) **Hero, the three levels of test**: unit, in-memory protocol, live host.
  (b) A wire trace, annotated.
- **Code.** `code/*/tests/` across every project, plus a shared `conftest.py`.
- **References.** Inspector repo and its deprecation note; the conformance suite.

### Part IV — Projects

#### 13. Project 1 · A secure database analyst
- **Thesis.** Giving a model read access to a production database is safe only if the server,
  not the model, decides what "read" means.
- **Sections.** The brief and the threat model · Architecture · Connection pooling · Schema
  introspection as a resource · The query tool · A validating SQL layer, and its honest limits
  · Database-level hardening as the real control · Structured results · Testing the security
  layer.
- **Diagrams.** (a) **Hero, defense in depth**, four layers with what each stops. (b) The
  query pipeline. (c) What the validator cannot see.
- **Code.** `code/13-postgres-analyst/`.
- **References.** PostgreSQL role docs; `sqlglot`; the lethal-trifecta post.

#### 14. Project 1 · Writes, transactions, and an audit trail
- **Thesis.** Write access becomes defensible when every mutation is previewed to a human,
  wrapped in a transaction, and recorded where the model cannot reach.
- **Sections.** Why writes are categorically different · A second pool, and why · Dry run
  first · Confirming with elicitation over MRTR · Transactions · The audit log · Exposing the
  audit trail as a resource · What still goes wrong.
- **Diagrams.** (a) **Hero, the write path** with the human gate. (b) Transaction and audit
  atomicity. (c) The audit record.
- **Code.** `code/13-postgres-analyst/`, part two.
- **References.** Draft elicitation; PostgreSQL transaction docs.

#### 15. Project 2 · A DevOps first responder
- **Thesis.** Cluster debugging is mostly reading, correlating, and summarizing, which is
  exactly what a read-only MCP server plus a model is good at.
- **Sections.** The brief · Talking to Kubernetes from Python · Listing and describing · Logs,
  and the size problem · Correlating events with pod state · Diagnosing a crash loop ·
  Read-only by construction · Returning structured findings.
- **Diagrams.** (a) **Hero, the diagnostic pipeline.** (b) Where a crash loop shows up in each
  API. (c) Read-only enforcement layers.
- **Code.** `code/15-devops-responder/`.
- **References.** Kubernetes Python client docs.

#### 16. Project 2 · Safe remediation with approval
- **Thesis.** A tool that changes a cluster must be irreversible only after a human has seen
  exactly what it will do.
- **Sections.** The three remediations worth automating · Preview and diff · Approval via
  elicitation · Long rollouts as tasks · Verifying the fix, not just issuing it · Rollback ·
  Blast-radius limits · Annotations, and why they do not save you.
- **Diagrams.** (a) **Hero, propose, preview, approve, verify.** (b) A rollout as a task.
  (c) Blast radius.
- **Code.** `code/15-devops-responder/`, part two.
- **References.** Kubernetes rollout docs; the tool-annotations post.

#### 17. Project 3 · A deep research browser
- **Thesis.** The value of a browsing server is not fetching the page; it is throwing away the
  ninety-five percent of it that would waste the model's context.
- **Sections.** The brief · Why `requests` is not enough · Driving a headless browser ·
  Extracting the article · Measuring what you saved · Caching · Screenshots as image content ·
  Resource links instead of giant blobs.
- **Diagrams.** (a) **Hero, 5 MB of HTML reduced to 20 KB of article**, to scale. (b) The
  extraction pipeline with the cache short-circuit. (c) Fetch against render.
- **Code.** `code/17-research-browser/`.
- **References.** Playwright docs; `trafilatura`.

#### 18. Project 3 · Server-side model calls and multi-page research
- **Thesis.** When a server needs a model of its own it now calls a provider directly, and
  that shift changes who pays, who consents, and who is on the hook for the prompt.
- **Sections.** The old answer, sampling, and why it is deprecated · The new answer: bring
  your own model · What you lose, and what you must now disclose · Multi-page research as a
  task · Extracting claims and keeping citations · Budget and stopping rules · Returning a
  report with sources.
- **Diagrams.** (a) **Hero, sampling against a direct provider call**, with the trust and
  billing boundary marked. (b) The research loop as a task. (c) Citation provenance.
- **Code.** `code/17-research-browser/`, part two.
- **References.** Deprecated registry (sampling); provider API docs.

### Part V — Production

#### 19. Security: the attacks the protocol does not stop
- **Thesis.** Every serious MCP incident so far has exploited something the specification does
  not defend against, so a server author has to know the attack classes by name.
- **Sections.** What the spec does guarantee · Prompt injection through tool descriptions ·
  Line jumping, before any tool is called · Rug pulls, and the absence of pinning ·
  Cross-server shadowing · The confused deputy · Token passthrough · The lethal trifecta ·
  Real incidents, with numbers · A server author's checklist · A host author's checklist.
- **Diagrams.** (a) **Hero, the trust boundary**, with each attack drawn where it crosses.
  (b) Line jumping on a timeline. (c) The lethal trifecta as a Venn diagram.
- **Code.** A deliberately malicious server in `code/19-security/`, used only as a test
  fixture, clearly marked, plus the defense that catches it.
- **References.** Security best practices page; Invariant Labs; Trail of Bits; the CVEs; the
  Anthropic containment post.

#### 20. Authorization: OAuth 2.1 for MCP servers
- **Thesis.** An MCP server is an OAuth 2.1 resource server and nothing more exotic, and the
  whole job is validating a token you did not issue, for an audience that is you.
- **Sections.** When you need auth, and when you must not add it · The three roles · What the
  server must implement · Protected resource metadata · Validating a token, including the
  audience check everyone skips · Scopes and minimization · Client registration, and why DCR
  is deprecated in favor of CIMD · Mix-up and confused-deputy defenses · Testing.
- **Diagrams.** (a) **Hero, the three-party flow** with the MCP server's job highlighted.
  (b) The metadata discovery chain. (c) Token validation as a gate diagram.
- **Code.** `code/20-auth/`, a minimal protected server plus a client that authenticates.
- **References.** Draft authorization pages; RFC 9728; RFC 8707; SEP-991; SEP-2468.

#### 21. Deploying to production: containers, scaling, and observability
- **Thesis.** Statelessness turned MCP deployment into an ordinary web-service problem, so the
  interesting work is now observability and cost, not session affinity.
- **Sections.** Containerizing a server · Configuration and secrets · Running behind a load
  balancer, with no sticky sessions · Health and readiness · Caching list results ·
  OpenTelemetry trace context in `_meta` · Structured logging to stderr · Rate limiting and
  abuse · Cost, and where it actually goes.
- **Diagrams.** (a) **Hero, the deployment topology.** (b) A trace spanning host, server, and
  backend. (c) Where the money goes.
- **Code.** `code/21-deploy/`, a Dockerfile, a compose file, and an instrumented server.
- **References.** SEP-414; SEP-2549; draft transports.

#### 22. Publishing: the registry, `server.json`, and MCPB bundles
- **Thesis.** A server nobody can install is a private script, and publishing is a
  fifteen-minute job that most authors skip.
- **Sections.** The distribution options, compared · `server.json`, field by field ·
  Namespaces, and proving you own one · Publishing with the CLI · Automating it in CI · What
  the registry does and does not promise · MCPB bundles for desktop install · Versioning and
  deprecating your own server.
- **Diagrams.** (a) **Hero, the distribution paths** from your repository to a user's host.
  (b) The publish flow. (c) Bundle anatomy.
- **Code.** A real `server.json` and a publish workflow for `code/05-first-server/`.
- **References.** Registry docs; the moderation policy; the MCPB repository.

### Part VI — Interoperability and the frontier

#### 23. Project 4 · One server, every client
- **Thesis.** A protocol is only worth adopting if the same server really does work
  everywhere, and the things that break are configuration and capability gaps, not the wire
  format.
- **Sections.** The brief: a team knowledge base · Building it once · The configuration
  matrix, client by client · The three top-level keys, and the interpolation syntaxes ·
  Capability differences, and degrading gracefully · Local against remote for the same server
  · Verifying on each client · A troubleshooting appendix.
- **Diagrams.** (a) **Hero, one server, five hosts**, with each configuration file named.
  (b) The capability matrix. (c) Local and remote side by side.
- **Code.** `code/23-knowledge-base/`, plus committed configuration files for each client.
- **References.** Each host's own configuration docs; the extensions client matrix.

#### 24. MCP Apps, extensions, and where the protocol goes next
- **Thesis.** The core protocol is deliberately finished; everything new arrives as an
  extension, and the first one that matters lets a server ship its own interface.
- **Sections.** The extensions framework as the growth mechanism · MCP Apps: a tool that
  returns an interface · The `ui://` resource and the sandboxed frame · The `postMessage`
  dialect · Content security and permissions · A worked example · Which hosts support it ·
  What is still open: pinning, identity, agent-to-agent · How to influence the spec · Where
  to go from here.
- **Diagrams.** (a) **Hero, a tool result that renders.** (b) The app sandbox and its message
  channel. (c) The road ahead.
- **Code.** `code/24-mcp-app/`, one server and one interactive widget.
- **References.** Extensions overview; the apps extension spec; the MCP Apps announcement;
  the SEP process.

---

## 7. Writing style

The rules the checker enforces, plus the ones it cannot.

**Mechanical, enforced by `blog-quality`:**
- Frontmatter only in `frontmatter.yaml`; never a `---` block in `index.md`; never a restated
  reading-time line.
- A `> **TL;DR.**` block, four sentences or fewer.
- A `## Common pitfalls` section.
- At most **ten em-dashes** per post.
- Every relative link resolves, forward slashes only.
- SVGs: `viewBox` only, `<title>` and `<desc>`, a dark-mode block, no emoji.
- American spelling.

**Judgment, enforced by review:**
- Picture, then intuition, then wire format, then code.
- No fabricated output. Every transcript was produced; every number is cited or reproducible.
- Every acronym expanded on first use in every post.
- Terms used exactly as [notation_guide.md](notation_guide.md) defines them.
- Second person is welcome; hype is not.
- Claims about the protocol carry a citation to the spec page that supports them.
- Where the RC is ambiguous or an SDK lags, say so.

---

## 8. Review checklist

Before a post is considered done:

- [ ] `python <blog-quality>/checks.py --root .` reports zero errors.
- [ ] Every code block either runs as shown or names its file in `code/`.
- [ ] Every project's tests pass.
- [ ] Every diagram opens correctly in light and dark mode.
- [ ] Every spec claim links to the page that supports it.
- [ ] Forward and backward links point at posts that exist.
- [ ] Read once out loud, for the hype the checker cannot see.
