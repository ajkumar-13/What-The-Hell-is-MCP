# What The Hell is MCP?

Standard: 2.2.0 · Plan version: 1.0.0 · Status: draft · Date: 2026-10-03

What The Hell is MCP? is a twenty-four post series that teaches the Model Context Protocol (MCP) from zero against protocol revision 2026-07-28 and nothing else. It starts with the $N \times M$ integration argument, reads the wire format and both transports field by field before any SDK call, builds a server, a client, and a host in Python, then spends six posts on four real projects (a PostgreSQL analyst, a Kubernetes first responder, a research browser, and a multi-host knowledge base) and four posts on security, authorization, deployment, and publishing. A reader who finishes it can design, build, test, secure, deploy, and publish an MCP server that real hosts connect to, write a client and a permission-gated host from a blank file, and read the specification itself without a guide.

# 1. Purpose and authority

This plan owns the curriculum of the series: which posts exist, in which order, what each one promises, what it requires and recommends, what it covers, which figures and labs carry it, and which primary sources anchor it. Layout, identifiers, the sidecar schema, the body template, the question bank, diagrams, tooling, and workflow are owned by series-standard/STANDARD.md at the version `series.yaml` pins, not by this document. `series.yaml` is the machine form of this lock: its `parts[].posts` lists the reading order, its ids are allocated from `next_post_ordinal` and never reused, and a dropped id goes to `retired`. The validator's plan stage keeps the two files and the sidecars in agreement. The pre-lock plan that preceded this document, with its per-post thesis, section outline, diagram list, and code pointers, is preserved at `audits/plan-pre-lock.md` and is no longer authoritative.

# 2. Audience, entry, and exit levels

## Reader profiles

- The application developer: someone who has used an AI assistant and wants to extend it with their own tools and data. This reader is the protagonist; every idea is introduced from zero with a picture before any wire format, and every acronym is expanded on first use in every post because the series does not assume linear reading.
- The platform or infrastructure engineer who must deploy, secure, and operate MCP servers for a team. This reader can skim Parts I and II and live in Parts V and VI, using mcp-003 and mcp-004 as the reference for what a stateless server owes a load balancer.
- The architect evaluating MCP who wants to know what the protocol does and does not guarantee before adopting it. Parts I, V, and VI answer this reader; mcp-001 section 7 and mcp-019 are the two posts to read first.
- The developer who already builds servers against an older revision and wants only what changed in 2026-07-28: mcp-003 for the stateless model, mcp-008 for what replaced the server-to-client channel, and mcp-009 for tasks and the extension model.

## Entry requirement

The series assumes comfort reading Python (functions, classes, `async` and `await`) and willingness to run a command in a terminal. It assumes no protocol, networking, OAuth, or AI-engineering background: JSON-RPC 2.0, OAuth 2.1, and the transport layer are each built up from scratch where they are needed. No post depends on a post of another family series, so mcp-001 has no prerequisites and the entry gate is the "Ten minutes with a server you did not write" section of mcp-001, which has the reader install a published server into a host they already use.

## Exit levels

- After mcp-004 (beginner, end of Part I): the reader can state the $N \times M$ argument, assign any responsibility to the host, the client, or the server, write a valid MCP request by hand with nothing but an HTTP client, and choose a transport from the shape of a deployment.
- After mcp-009 (intermediate, end of Part II): the reader has a running server with tools, a templated resource, a prompt, a completion handler, an elicitation-gated tool, and a long-running task, and can say which of the three primitives a given piece of data belongs behind.
- After mcp-012 (end of Part III): the reader has written a client and a permission-gated multi-server host and can test any server in memory with no subprocess and no human.
- After mcp-018 (end of Part IV): the reader has shipped three projects whose refusals, approvals, and byte counts are produced by committed tests, and can put a human on the critical path of a mutation in a parameter the model cannot forge.
- After mcp-022 (end of Part V): the reader can name every attack class by where it crosses the trust boundary, validate a token for an audience that is them, containerize a server behind a round-robin load balancer, and publish it to the registry.
- After mcp-024 (advanced): the reader can configure the same server in six hosts, declare and degrade any extension, ship a tool that returns an interface, and take a change through the Specification Enhancement Proposal process.

## Gates

`series.yaml` declares no formal gates (`gates: []`). The de facto gates are the test suites: no reader should proceed past mcp-005 without the nineteen-test run of `code/05-first-server/`, past mcp-011 without the sixty-eight tests of `code/10-mcp-client/`, or past any project post without that project's suite green, because every figure the posts quote comes from those runs.

# 3. Pedagogical rules

- One revision. Every post teaches protocol revision 2026-07-28 exclusively and treats removed mechanisms (`initialize`, `Mcp-Session-Id`, the server-to-client request channel, `ping`, `logging/setLevel`, SSE resumability, `resources/subscribe`) as things the reader will meet in older code, never as things to learn. Deprecated but present features (roots, sampling, logging, HTTP+SSE, Dynamic Client Registration) are taught only as "you will meet this".
- Picture, then intuition, then wire format, then code, in that order, always. The JSON that crosses the wire is shown before the SDK call that produces it, so a reader can debug with `curl` and a text editor rather than trusting a library.
- Every post answers one question, and that question is the spec's Central question below.
- Every post has at least one hand-edited SVG; every post from mcp-003 onward also has at least one runnable artifact. mcp-001 and mcp-002 are deliberately code-free mental-model posts.
- No invented output. A transcript in a post is a transcript that was produced against `mcp==2.0.0b2`; every number is either cited to a primary source or reproducible from `code/` and, for the first server, recorded in `verify/RESULTS.md`.
- Where the specification text is ambiguous or an SDK has not caught up, the post says so in a short note rather than inventing a confident answer; mcp-009, mcp-018, mcp-022, and mcp-024 each carry such a note.
- Every acronym is expanded on first use in every post; terms are used exactly as `notation_guide.md` defines them.
- Security is taught where it bites, not in one chapter: the stdout rule in mcp-004, unforgeable approval in mcp-008, the gate in mcp-011, the database role in mcp-013, read-only by construction in mcp-015, and then the full threat model in mcp-019.
- Neutral textbook tone, warm, second person welcome, no hype and no marketing voice.

# 4. Curriculum lock

24 posts in 6 parts. Post numbers are permanent; a post inserted later takes the next ordinal from `series.yaml` and the directories after it renumber (STANDARD.md Section 2.2).

| Part | Title | Posts | Ids |
|---|---|---|---|
| I | Part I - Foundations | 01-04 | mcp-001 to mcp-004 |
| II | Part II - Building servers | 05-09 | mcp-005 to mcp-009 |
| III | Part III - Clients, hosts, and testing | 10-12 | mcp-010 to mcp-012 |
| IV | Part IV - Projects | 13-18 | mcp-013 to mcp-018 |
| V | Part V - Production | 19-22 | mcp-019 to mcp-022 |
| VI | Part VI - Interoperability and the frontier | 23-24 | mcp-023 to mcp-024 |

Retired ids: none.

# 5. Per-post specifications

One specification per shipped post, in `series.yaml` order. The sidecars of this series carry no `level`, `prerequisites`, or `recommended` keys yet, so for now this section is the sole record of level and dependencies (Section 9); when the sidecars gain those keys they must equal the values here.

## Part I - Foundations

### mcp-001 - What MCP is, and the problem it solves

- **Meta:** Part I; beginner; core; proof none; code none.
- **Central question:** What problem does one protocol boundary between AI applications and data sources solve, and what does it leave unsolved?
- **Promise:** The reader can count the integrations a set of applications and data sources costs with and without a shared protocol, install a published server into a host they already use and recognise the permission prompt, name the three roles and say which owns consent, and state what MCP does not give them.
- **Coverage:** the copy-paste workflow, the $N \times M$ integration problem counted, ten minutes with a server from the registry (listing, configuration, restart, permission prompt), what a protocol boundary buys, the three roles, tools, resources, and prompts, what MCP explicitly does not do (no intelligence, no privacy boundary), provenance and the Agentic AI Foundation, how to read the series and the versions it pins.
- **Dependencies:** requires none; supports mcp-002.
- **Visuals:** hero `01-n-times-m.svg` (the mesh of bespoke integrations against the same nodes through one boundary); the three roles with the trust boundary marked; a registry listing read for what it does and does not promise.
- **Lab/gate:** Install one registry server into a desktop host, trigger a tool call, and screenshot or transcribe the permission prompt; then count the integrations for the reader's own set of applications and sources.
- **Anchors:** MCP specification versioning index and 2026-07-28 changelog; specification "Tools" page; registry documentation and moderation policy; the Agentic AI Foundation announcements; JSON-RPC 2.0 specification.

### mcp-002 - The architecture: hosts, clients, and servers

- **Meta:** Part I; beginner; core; proof none; code none.
- **Central question:** Which of the three MCP roles owns which job, and why is almost every confusion in the ecosystem a mix-up of the first two?
- **Promise:** The reader can assign any responsibility in an MCP setup to the host, the client, or the server, trace a tool name the model chose back to the connection that carries the call, predict what a host does when two servers export the same tool name, and explain why consent belongs in the host while validation belongs in the server.
- **Coverage:** the unanswerable bug report, the host owns the model, the conversation, and consent, the client owns exactly one connection, the server is your code, the unified catalog built from every connection, name collisions and the three strategies, who controls what (model-controlled tools, application-driven resources, user-controlled prompts), the host as the security boundary, what the picture no longer contains since 2026-07-28.
- **Dependencies:** requires mcp-001; supports mcp-003, mcp-011.
- **Visuals:** hero `01-host-clients-servers.svg` (one host, three clients, three servers); the capability catalog as a lookup table; the control matrix of which party decides to use each primitive.
- **Lab/gate:** Given a described setup with two servers exporting `search`, write down which component performs each of eight listed actions and which collision strategy the host would need; check against section 6.
- **Anchors:** specification "Architecture" page and the "User Interaction Model" sections of the Tools, Resources, and Prompts pages; specification "Tools" tool-name rules; SEP-2575 and SEP-2567; "Tool annotations are not enforcement" (2026).

### mcp-003 - The wire protocol: JSON-RPC, discovery, and the stateless model

- **Meta:** Part I; beginner; core; proof none; code included.
- **Central question:** What does one self-describing MCP request carry, and why does carrying it all make a server an ordinary web service?
- **Promise:** The reader can write a valid MCP request by hand with nothing but an HTTP client, name every reserved `_meta` key and say which side sets it, carry state across two tool calls with a server-minted handle instead of a connection, and decide whether a failure belongs in a JSON-RPC `error` object or in a result with `isError: true`.
- **Coverage:** one message has to be enough, JSON-RPC 2.0 in ten minutes (request, response, error, notification), `server/discover` and what replaced capability negotiation, the `_meta` contract key by key, `resultType` and why every result carries one, a complete annotated trace, what statelessness costs and the handle pattern that pays it back, protocol errors against tool execution errors, doing it by hand with `httpx`.
- **Dependencies:** requires mcp-002; supports mcp-004, mcp-005, mcp-008, mcp-021.
- **Visuals:** hero `01-self-describing-request.svg` (one request with every `_meta` key called out); stateful against stateless behind a load balancer; the error taxonomy.
- **Lab/gate:** Run `snippets/raw_discover.py` against a server with no SDK, then hand-write a `tools/call` request that reproduces one line of the annotated trace and classify two failures into the correct error channel.
- **Anchors:** specification "Base protocol", "server/discover", and "Versioning and compatibility" pages, revision 2026-07-28; specification "Tools" error handling; SEP-2575 and SEP-2567; JSON-RPC 2.0 specification.

### mcp-004 - Transports: stdio and Streamable HTTP

- **Meta:** Part I; beginner; core; proof none; code included.
- **Central question:** How do identical messages physically travel over stdio and over Streamable HTTP, and how should a deployment's shape choose between them?
- **Promise:** The reader can choose a transport from the shape of a deployment, compose a conformant Streamable HTTP request by hand with every required header, diagnose the stdout corruption that breaks most first stdio servers, and probe an unfamiliar server for its era instead of guessing its revision.
- **Coverage:** the same message two ways, stdio and the spawned process, the stdout rule, Streamable HTTP as one endpoint and ordinary POSTs, the headers one by one (`Mcp-Method`, `Mcp-Name`, `Mcp-Param-{Name}`, the Base64 sentinel), why round-robin load balancing works now, what was removed and how to meet an HTTP+SSE server, security that lives at this layer, choosing between them.
- **Dependencies:** requires mcp-003; supports mcp-005, mcp-010, mcp-021, mcp-023.
- **Visuals:** hero `01-two-transports.svg` (the two transports side by side); the stdio process model and the stdout trap; Streamable HTTP behind a load balancer.
- **Lab/gate:** Run `snippets/stdio_hello.py` and `snippets/http_hello.py`, then break the stdio server with one stray `print()` and read the failure; compose one HTTP request by hand with every header and confirm the server answers.
- **Anchors:** specification "Transports: Streamable HTTP", "Transports: stdio", "Versioning and compatibility", and "Deprecated features" pages; SEP-2243 (HTTP standardization); SEP-2575 and SEP-2567; RFC 9110 section 5.1.

## Part II - Building servers

### mcp-005 - Your first MCP server

- **Meta:** Part II; beginner; core; proof none; code required.
- **Central question:** What is the smallest useful MCP server, and where does the protocol go once the SDK is doing its job?
- **Promise:** The reader can create and pin a Python project a desktop host can spawn, register a tool with `@mcp.tool()` and read the input schema generated from type hints, drive the server by hand over standard input and output one JSON line at a time, and diagnose a server that does not appear in a host in the order that finds the fault fastest.
- **Coverage:** the system-information server and why it is a good first server, project setup with `uv`, the smallest server that runs, the first tool, a tool with arguments and how a signature becomes a schema, connecting to a host, watching the messages go past, troubleshooting honestly (the byte-order mark, stdout, paths).
- **Dependencies:** requires mcp-003; recommends mcp-004; supports mcp-006, mcp-010, mcp-021.
- **Visuals:** hero `01-your-file-and-the-host.svg` (your file, the host, and the pipe between them); Python signature to JSON Schema, annotated; the troubleshooting decision tree.
- **Lab/gate:** Run `code/05-first-server/` under `uv`, connect it to a host, call both tools, and reproduce the nineteen-test run and the published tool surface recorded in `verify/RESULTS.md`.
- **Anchors:** specification "Tools" and "Transports" pages, revision 2026-07-28; MCP Python SDK 2.0.0b2 (`MCPServer`, `@mcp.tool()`, `run()`); `uv` documentation; `code/05-first-server/` and `verify/capture.py`.

### mcp-006 - Tools in depth: schemas, structured output, and annotations

- **Meta:** Part II; intermediate; core; proof none; code included.
- **Central question:** Why does schema design, not implementation, decide whether a tool gets called correctly?
- **Promise:** The reader can read and write every field of a `Tool` object and say which two are required, design an input schema a model fills in correctly on its first attempt, publish an `outputSchema` deliberately and prove with a test that they did, and route any failure to the JSON-RPC `error` object or to `isError: true`.
- **Coverage:** the schema is the interface, the complete `Tool` object field by field, input schemas a model gets right, `outputSchema` and `structuredContent`, the return type that publishes nothing silently, the five content block types, errors that teach the model to retry, annotations as hints and only hints, naming, titles, and descriptions, deterministic ordering and caching.
- **Dependencies:** requires mcp-005; supports mcp-007, mcp-008, mcp-012, mcp-013, mcp-017.
- **Visuals:** hero `01-anatomy-of-a-tool-call.svg` (request and result annotated); unstructured against structured output; protocol error against execution error.
- **Lab/gate:** Extend `code/05-first-server/` with one dataclass-returning tool, run the schema test that fails on `outputSchema: null`, fix the annotation, and watch it pass; then rewrite one weak description and compare the model's first-attempt call.
- **Anchors:** specification "Tools" page and "Base protocol" JSON Schema usage, revision 2026-07-28; SEP-2106, SEP-986, SEP-1303; "Tool annotations are not enforcement" (2026); MCP Python SDK 2.0.0b2.

### mcp-007 - Resources and prompts: the primitives that are not tools

- **Meta:** Part II; intermediate; core; proof none; code included.
- **Central question:** When should a server expose something as a resource or a prompt instead of a tool, and who pulls each trigger?
- **Promise:** The reader can decide for any piece of data whether it belongs behind a tool, a resource, or a prompt, register a URI template and defend its variables as untrusted input, write a completion handler including the prefix match the SDK does not do, and say precisely what `subscriptions/listen`, `ttlMs`, and pagination do and do not give them.
- **Coverage:** why not make everything a tool, resources and who decides to read one, URI design and templates, completions, change notification with `subscriptions/listen`, caching with `ttlMs` and `cacheScope`, pagination and what the high-level server does not do, prompts as user-invoked entry points, titles, icons, and annotations, the decision table.
- **Dependencies:** requires mcp-006; supports mcp-013, mcp-023.
- **Visuals:** hero `01-three-primitives.svg` (tools, resources, prompts, and who pulls each trigger); a URI template resolving; the subscription flow.
- **Lab/gate:** Add a static resource, an allowlisted templated resource, a prompt, and a completion handler to `code/05-first-server/`; attempt a path-traversal variable and confirm the refusal; list the resources and confirm the caching fields.
- **Anchors:** specification "Resources", "Prompts", "Completion", "Pagination", "Caching", and "Subscriptions" pages, revision 2026-07-28; SEP-2549; RFC 6570; RFC 3986; MCP Python SDK 2.0.0b2.

### mcp-008 - Elicitation and MRTR: asking the user mid-call

- **Meta:** Part II; intermediate; core; proof none; code included.
- **Central question:** How does a server ask the user a question in the middle of a tool call when there is no channel left to call back on?
- **Promise:** The reader can trace a Multi Round-Trip Request (MRTR) exchange message by message and say where the state lives at each step, build a tool whose approval parameter the model can neither see nor forge, handle accept, decline, and cancel as three separate outcomes, and protect `requestState` on a server that runs more than one worker.
- **Coverage:** a tool that cannot finish without a human, how this used to work and why the back channel had to go, `input_required` and the retry, the complete message loop, carrying state across round trips without a session, `Resolve` and `Elicit` in the SDK, schema rules for what you can ask, accept, decline, cancel, URL mode for authorization flows and payments, designing questions a user can answer.
- **Dependencies:** requires mcp-006; recommends mcp-003; supports mcp-009, mcp-014, mcp-016, mcp-018, mcp-020.
- **Visuals:** hero `01-mrtr-loop.svg` (every message in order); the old back-channel model against return-and-retry; the three outcomes.
- **Lab/gate:** Add `terminate_process` with a resolved `approval` parameter to `code/05-first-server/`, confirm from the published schema that `approval` is absent (as `verify/RESULTS.md` records), and drive accept, decline, and cancel by hand.
- **Anchors:** specification "Multi Round-Trip Requests", "Elicitation", "Base protocol" responses, and "Caching" pages, revision 2026-07-28; SEP-2322 (Final), SEP-1036, SEP-1034.

### mcp-009 - Tasks: work that outlives a single request

- **Meta:** Part II; intermediate; core; proof none; code included.
- **Central question:** How does the tasks extension turn "hold the connection open and hope" into an explicit, pollable, cancellable lifecycle, and who decides when a task is created?
- **Promise:** The reader can declare an extension in per-request capabilities and read a server's from `server/discover`, drive a task from creation through polling to a terminal state by hand, tell a task apart from a multi round-trip request, and report progress from a slow tool today while standing up the extension on the seams the SDK ships.
- **Coverage:** extensions and the reverse-DNS naming rule, what a task is for and what is not a task, declaring `io.modelcontextprotocol/tasks` and why creation is server-directed, either result shape on any `tools/call`, the lifecycle, `tasks/get` and being told instead, `tasks/update` and `tasks/cancel`, tasks against MRTR, `taskId` as the state handle, what the SDK gives today, when a task is the wrong answer; the correction that `execution.taskSupport` was the 2025-11-25 mechanism and was removed by SEP-2663.
- **Dependencies:** requires mcp-008; supports mcp-010, mcp-016, mcp-018, mcp-024.
- **Visuals:** hero `01-task-state-machine.svg`; a synchronous call against a task on a timeline; extension negotiation.
- **Lab/gate:** Add the long-running `watch_cpu` tool to `code/05-first-server/`, poll it to a terminal state by hand, cancel one run mid-flight, and confirm the two result shapes a client must accept.
- **Anchors:** tasks extension specification (`ext-tasks`); SEP-2663 (Final, Extensions Track); SEP-2133 (Extensions, Final); specification "Versioning" extension negotiation and "Progress" pages; extensions overview and client support matrix.

## Part III - Clients, hosts, and testing

### mcp-010 - Building your own MCP client

- **Meta:** Part III; intermediate; core; proof none; code required.
- **Central question:** What does a client actually do, and why does writing one remove the remaining magic from every host behaviour?
- **Promise:** The reader can connect to a server over stdio, Streamable HTTP, or in memory through one code path, read every content block type and branch on `isError` rather than on exceptions, answer a server that asks a question mid-call both automatically and by hand, and tear a connection down without leaking the subprocess it held.
- **Coverage:** the client that lied to its model, client and host one more time, three transports behind one `Client`, lifetimes and the leaked subprocess, discovery, calling a tool and reading the result properly, handling `input_required` from the client side, resources and prompts, running it, when not to write your own client.
- **Dependencies:** requires mcp-005; recommends mcp-004, mcp-009; supports mcp-011, mcp-012.
- **Visuals:** hero `01-client-internals.svg` (transport, correlation, capability cache); result handling including the `isError` branch everyone forgets.
- **Lab/gate:** Run the `code/10-mcp-client/` CLI against `code/05-first-server/` over all three transports, make a tool fail and show that `try`/`except` never fires, and confirm with the process table that no subprocess outlives the client.
- **Anchors:** specification "Tools", "Multi Round-Trip Requests", "Base protocol" error codes, and "Transports" pages, revision 2026-07-28; SEP-2322; SEP-2243; MCP Python SDK 2.0.0b2 driving `code/10-mcp-client/`.

### mcp-011 - Building a host: the tool loop, many servers, and permissions

- **Meta:** Part III; intermediate; core; proof none; code required.
- **Central question:** If a host is a loop around a model plus a permission gate, what makes the gate the only part that is hard to get right?
- **Promise:** The reader can drive a tool-execution loop where no call reaches a server without passing the gate, merge many servers into one catalog and resolve collisions the way the specification allows, write a permission policy that treats annotations as untrusted claims, and point at the exact place in the loop where a prompt-injection payload enters.
- **Coverage:** the gate is the host, the loop step by step, translating a catalog into a provider's tool format, parallel tool calls, many servers and one dot-namespaced catalog, the permission gate, two independent gates, where a prompt injection enters, feedback and what this host does not do, running it.
- **Dependencies:** requires mcp-010; recommends mcp-002; supports mcp-019.
- **Visuals:** hero `01-tool-loop.svg` (the loop with the permission gate on the critical path); many servers, one catalog; where a prompt-injection attack enters the loop.
- **Lab/gate:** Run the multi-server host CLI from `code/10-mcp-client/` against two servers with a colliding tool name, deny one call and approve another, and confirm from the sixty-eight-test suite that a denial sticks while a session approval does not.
- **Anchors:** specification "Tools" naming and security sections, revision 2026-07-28; "Tool annotations are not enforcement" (2026); Trail of Bits, "Jumping the line" (2025); Anthropic, "How we contain Claude" (2026); MCP Python SDK 2.0.0b2.

### mcp-012 - Testing and debugging MCP

- **Meta:** Part III; intermediate; core; proof none; code included.
- **Central question:** How do you test an MCP server like any other library, with a real client on the other end and no subprocess, socket, or host?
- **Promise:** The reader can write in-memory protocol tests that drive a real client against the server object, assert on the published schema so a silent `outputSchema: null` fails a test, drive the elicitation round trip through accept, decline, and cancel without a human, and read a wire trace and find the two places a failure hides.
- **Coverage:** the server works and the host says it does not, three levels of test and what each catches, in-memory with `Client(server_object)`, two rules that make async tests work, asserting on the schema, testing the multi-round trip without a human, reading a wire trace, the Inspector and its CVE, the conformance suite, continuous integration and ground truth, the five failures behind most bug reports.
- **Dependencies:** requires mcp-010; recommends mcp-006; supports mcp-013, mcp-015, mcp-017.
- **Visuals:** hero `01-three-levels-of-test.svg` (unit, in-memory protocol, live host); a wire trace, annotated.
- **Lab/gate:** Run the suites of `code/05-first-server/` and `code/10-mcp-client/` in memory, add one schema assertion that fails on a missing `outputSchema`, and drive one elicitation to each of its three outcomes with no human in the loop.
- **Anchors:** specification "Tools" and "Base protocol" error codes, revision 2026-07-28; MCP Inspector repository and CVE-2025-49596; the conformance suite; MCP Python SDK 2.0.0b2.

## Part IV - Projects

### mcp-013 - Project 1 · A secure database analyst

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** What makes giving a model read access to a PostgreSQL database defensible, and which layer is the control rather than the filter?
- **Promise:** The reader can build a read-only database tool whose refusals happen before any connection is acquired, separate the layers that merely filter from the two that enforce, refuse the syntax the SQL parser quietly gave up on, and test a security layer with no database at all including the cases it allows.
- **Coverage:** the brief and the thing that goes wrong, architecture, pools and reading configuration at call time, schema introspection as a resource, the query tool and the shape of a refusal, the validating SQL layer and its honest limits, when the parser gives up and does not tell you, the database role as the real control, structured results, testing the security layer.
- **Dependencies:** requires mcp-012; recommends mcp-006, mcp-007; supports mcp-014, mcp-019.
- **Visuals:** hero `01-defense-in-depth.svg` (four layers and what each stops); the query pipeline; what the validator cannot see.
- **Lab/gate:** Run the eighty-five tests of `code/13-postgres-analyst/` with no database, then against a local PostgreSQL with the read-only role; send one `Command` fallback statement and confirm the refusal text matches the post.
- **Anchors:** specification "Tools" and "Resources" pages, revision 2026-07-28; PostgreSQL "Database roles and privileges"; `sqlglot` 30.13.0; `asyncpg`; Willison, "The lethal trifecta" (2025).

### mcp-014 - Project 1 · Writes, transactions, and an audit trail

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** What has to be true before a model-initiated database write is defensible?
- **Promise:** The reader can put a human confirmation on the critical path of a tool call in a parameter the model cannot forge, keep an audit row atomic with the change it describes and record the failures too, choose the resolver return type that preserves all three elicitation outcomes, and recognise the pool and fallback mistakes that turn a write boundary back into nothing.
- **Coverage:** why a write is categorically different, three connection accessors, validate then dry run then ask, confirming over MRTR and the resolver signature, one transaction with the audit row inside it, the audit record field by field, the trail as a resource, running it and what still goes wrong.
- **Dependencies:** requires mcp-013, mcp-008; supports mcp-015, mcp-019.
- **Visuals:** hero `01-the-write-path.svg` (the write path with the human gate); transaction and audit atomicity; the audit record.
- **Lab/gate:** Run the second half of `code/13-postgres-analyst/`, perform one `UPDATE` through accept, one through decline, and one through cancel, and read back the audit resource to confirm exactly which of the three left a row.
- **Anchors:** specification "Multi Round-Trip Requests" and "Elicitation" pages, revision 2026-07-28; PostgreSQL roles and transactions; `asyncpg` transactions and pools; MCP Python SDK 2.0.0b2.

### mcp-015 - Project 2 · A DevOps first responder

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** Why is cluster debugging the right shape of work for a read-only MCP server, and what makes "read-only" structural rather than advisory?
- **Promise:** The reader can call a synchronous client library from an `async` tool without freezing the transport, correlate pod phase, container status, and events into one verdict a human can check, bound a log read in lines and in bytes while keeping the end that matters, and make a server's read-only claim structural.
- **Coverage:** the brief, what goes over the wire before any Python, talking to Kubernetes from Python, read-only by construction through the import graph, where a crash loop shows up, logs and the size problem, diagnosing as a pure function, running it, when this is the wrong tool.
- **Dependencies:** requires mcp-012; recommends mcp-014; supports mcp-016.
- **Visuals:** hero `01-diagnostic-pipeline.svg`; where a crash loop shows up in each API; bounding a log in two dimensions.
- **Lab/gate:** Run the seventy-eight tests of `code/15-devops-responder/` with no cluster, then diagnose a deliberately crash-looping pod on a local cluster and confirm the verdict and the dropped-bytes report.
- **Anchors:** specification "Tools" annotations note, revision 2026-07-28; Kubernetes Python client; Kubernetes "Debug Running Pods"; "Tool annotations are not enforcement" (2026); MCP Python SDK 2.0.0b2.

### mcp-016 - Project 2 · Safe remediation with approval

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** How does a tool that changes a cluster prove, rather than promise, that a human saw the exact change first?
- **Promise:** The reader can gate a mutating tool behind an approval the model can neither see nor forge, render a change preview that is provably identical in the question and in the result, roll a deployment back the way `kubectl rollout undo` does and say what that does not cover, and put blast-radius limits where they fire before a human is asked.
- **Coverage:** the three remediations worth automating, what "approval" cannot mean, the preview as a pure function, restart and what it is, scale and reading back, rollback and what goes back, the ReplicaSet ownership trap, blast-radius limits, long rollouts and why the tasks extension is the wrong tool here, running it, when not to build this.
- **Dependencies:** requires mcp-015, mcp-008; recommends mcp-009; supports mcp-017.
- **Visuals:** hero `01-propose-preview-approve.svg` (propose, preview, approve, verify); rollback scope; blast radius.
- **Lab/gate:** Scale a deployment through the approval path, decline a second change, and roll back a deployment that has a sibling with the same labels; confirm the rollback landed on the correct pod template and that the blast-radius check fired before any question.
- **Anchors:** specification "Multi Round-Trip Requests", "Elicitation", and "Tools" pages, revision 2026-07-28; Kubernetes "Rolling Back a Deployment" and "Owners and Dependents"; "Tool annotations are not enforcement" (2026).

### mcp-017 - Project 3 · A deep research browser

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** Why is the valuable part of a browsing server the discard rather than the fetch, and how do you measure it?
- **Promise:** The reader can drive a headless browser from an MCP server without making it a test dependency, extract article text and report the measured reduction alongside it, return a screenshot as a real image content block while still publishing an output schema, and cache extractions with bounds on both age and count.
- **Coverage:** the brief, why a plain fetch is not enough, driving a headless browser, extracting the article, measuring what you saved (94.6 and 99.6 percent on the two committed pages), caching what you already paid for, screenshots as images, bounding the result and the resource-link alternative, running it, when not to build this.
- **Dependencies:** requires mcp-006; recommends mcp-012, mcp-016; supports mcp-018.
- **Visuals:** hero `01-html-to-article.svg` (76 KB of HTML reduced to 4 KB of article, to scale, with the second pair alongside); the extraction pipeline with the cache short-circuit; fetch against render.
- **Lab/gate:** Run `code/17-research-browser/` on the two committed pages and reproduce the byte counts and word counts in the TL;DR; then screenshot one page and confirm the result is an image block with a published schema.
- **Anchors:** specification "Tools" content block types and "Caching" pages, revision 2026-07-28; Playwright for Python; `trafilatura`; MCP Python SDK 2.0.0b2.

### mcp-018 - Project 3 · Server-side model calls and multi-page research

- **Meta:** Part IV; intermediate; core; proof none; code required.
- **Central question:** Now that sampling is deprecated and the back channel is gone, what changes when a server needs a model of its own?
- **Promise:** The reader can explain precisely what happened to sampling and what survives of it, build a server-side summariser as a protocol with an offline default and an opt-in provider, report which summariser actually ran so a fallback is never labelled as the model, and run a bounded multi-page research pass whose every claim carries its page.
- **Coverage:** the post that had to be rewritten, what happened to sampling, who pays, who consented, who answers for the prompt, a protocol and a default that needs no model, the optional provider path, never label a fallback as the model, the research loop and the budget, every claim carries its URL, running it, when not to call a model from your server.
- **Dependencies:** requires mcp-017; recommends mcp-008, mcp-009; supports mcp-019.
- **Visuals:** hero `01-sampling-vs-direct-call.svg` (sampling against a direct provider call, with the trust and billing boundary marked); the research loop, its budget gate, and every way it can stop; citation provenance.
- **Lab/gate:** Run the research tool with no provider key and confirm the offline summariser is named in the result, then with a key and confirm the label changes; exhaust the page budget and confirm the stop reason.
- **Anchors:** specification "Deprecated features" and "Sampling" pages, revision 2026-07-28; SEP-2577; Anthropic API documentation; MCP Python SDK 2.0.0b2 (`NoBackChannelError`).

## Part V - Production

### mcp-019 - Security: the attacks the protocol does not stop

- **Meta:** Part V; intermediate; core; proof none; code none.
- **Central question:** Which attack classes does the specification defend against, which live only in third-party research, and where does each cross the trust boundary?
- **Promise:** The reader can name each attack class and point at where it crosses the trust boundary, tell a control from a filter in their own server and order them correctly, and audit a host's approval gate against the three ways an attacker gets in before it fires.
- **Coverage:** two lists and only one in the specification, what the specification covers, the session-hijacking section that describes something that no longer exists, tool poisoning, line jumping at `tools/list`, cross-server shadowing, payloads you cannot see, the lethal trifecta, real incidents with numbers, no pinning and the registry is not a filter, four controls from this series and why each holds, a server author's checklist, a host author's checklist.
- **Dependencies:** requires mcp-011; recommends mcp-013, mcp-014, mcp-018; supports mcp-020, mcp-022.
- **Visuals:** hero `01-trust-boundary.svg` (each attack drawn where it crosses); line jumping on a timeline; the lethal trifecta.
- **Lab/gate:** For each of the fifteen attack classes, write down which of the four shipped controls (first server, client, database analyst, first responder) stops it, which filter merely narrows it, and which has no defence in this series; then run both checklists against the reader's own server.
- **Anchors:** specification "Security Best Practices" and "Tools" pages, revision 2026-07-28; "Tool annotations are not enforcement" (2026); registry moderation policy; Security Interest Group charter (2026); Invariant Labs tool poisoning and GitHub MCP reports (2025); Trail of Bits line jumping, conversation history, and ANSI reports (2025); Willison on Supabase MCP (2025); Anthropic, "How we contain Claude" (2026); CVE-2025-6514, CVE-2025-49596, CVE-2025-53109, CVE-2025-53110; SEP-1766.

### mcp-020 - Authorization: OAuth 2.1 for MCP servers

- **Meta:** Part V; advanced; core; proof none; code required.
- **Central question:** If an MCP server is an OAuth 2.1 resource server and nothing more exotic, what exactly does it owe, and what does it validate?
- **Promise:** The reader can decide whether a server needs authorization at all and leave it off correctly on a local server, publish protected resource metadata and the `WWW-Authenticate` challenge that points at it, validate an inbound token including the audience check and answer `401` and `403` the way clients expect, and explain the mix-up attack and the confused deputy to a reviewer.
- **Coverage:** two servers and only one needs this, the three roles and which one you are, what the specification requires, protected resource metadata and the chain it starts, validating the token including the audience check, scopes and 401 against 403, getting a client ID and why Dynamic Client Registration is now the fallback to Client ID Metadata Documents, mix-up, confused deputy, and byte-for-byte issuer comparison, wiring it up with the SDK, testing with four requests.
- **Dependencies:** requires mcp-019; recommends mcp-008; supports mcp-021, mcp-023.
- **Visuals:** hero `01-three-party-flow.svg` (the three-party flow with the MCP server's job highlighted); the metadata discovery chain; token validation as a gate diagram.
- **Lab/gate:** Run the twenty-one tests of `code/20-auth/`, then send the four requests of section 10 by hand (no token, wrong audience, insufficient scope, valid) and match each status and challenge header to the post.
- **Anchors:** specification "Authorization", "Client Registration", "Authorization Server Discovery", "Authorization Security Considerations", and "Security Best Practices" pages, revision 2026-07-28; RFC 9728, RFC 8707, RFC 9207; SEP-991, SEP-2468.

### mcp-021 - Deploying to production: containers, scaling, and observability

- **Meta:** Part V; intermediate; core; proof none; code included.
- **Central question:** Once sessions are gone, what is left of MCP deployment beyond an ordinary web service, and where does the interesting work move?
- **Promise:** The reader can containerize an MCP server with a pinned base image and a non-root user, explain to an infrastructure team why no sticky sessions are needed and what replaced them, propagate an OpenTelemetry trace across host, server, and backend through `_meta`, and set `ttlMs` and `cacheScope` on list results without leaking one user's catalog to another.
- **Coverage:** what statelessness bought, containerizing, configuration, and secrets, behind a load balancer with nothing to pin, two edge cases measured while writing (SDK behaviours recorded in `verify/RESULTS.md`), health and readiness, caching list results, tracing with three unprefixed `_meta` keys, logging to standard error, rate limiting and abuse, where the cost goes.
- **Dependencies:** requires mcp-005, mcp-004; recommends mcp-003, mcp-020; supports mcp-022.
- **Visuals:** hero `01-deployment-topology.svg`; a trace spanning host, server, and backend; where the money goes.
- **Lab/gate:** Build and run `code/21-deploy/` against the unchanged `code/05-first-server/`, run two replicas behind a round-robin proxy, and confirm a two-call handle exchange succeeds across replicas; reproduce the two measured edge cases.
- **Anchors:** specification "Transports: Streamable HTTP", "Caching", and "Deprecated features" pages, revision 2026-07-28; SEP-2567, SEP-2575, SEP-2243, SEP-2549, SEP-414; W3C Trace Context and Baggage; OpenTelemetry Python; `uv` lockfile documentation.

### mcp-022 - Publishing: the registry, server.json, and MCPB bundles

- **Meta:** Part V; intermediate; core; proof none; code included.
- **Central question:** What does it take to make a server something a stranger can install, and what does the registry actually promise them?
- **Promise:** The reader can write a `server.json` that validates against the current schema and matches their package metadata, prove ownership of a namespace by GitHub identity, DNS record, or a file on their domain, automate publishing from continuous integration with GitHub OpenID Connect and no stored secret, and say precisely what the registry promises a consumer.
- **Coverage:** the distribution options compared, read this before you rely on it (preview status, moderation policy), `server.json` field by field, namespaces and proving ownership, publishing with the CLI, automating it with no stored secret, MCPB bundles for the desktop, three places the sources disagree, what the stateless revision changed here, which is nothing.
- **Dependencies:** requires mcp-021; recommends mcp-019; supports mcp-023.
- **Visuals:** hero `01-distribution-paths.svg` (from your repository to a user's host); the publish flow; bundle anatomy.
- **Lab/gate:** Validate the committed `server.json` for `code/05-first-server/` against the schema, run the publish workflow in dry-run mode, and build one `.mcpb` bundle and install it into a desktop host.
- **Anchors:** MCP Registry documentation (about, quickstart, authentication, package types, remote servers, GitHub Actions, versioning, moderation policy, terms); `server.json` schema 2025-12-11 and the registry OpenAPI document; the registry repository CLI reference; the MCPB repository; Anthropic "MCP Bundles" installation documentation.

## Part VI - Interoperability and the frontier

### mcp-023 - Project 4 · One server, every client

- **Meta:** Part VI; intermediate; core; proof none; code required.
- **Central question:** Does one server really work in every host, and when it does not, is the wire format or the configuration to blame?
- **Promise:** The reader can write a correct configuration for Claude Desktop, Claude Code, Cursor, VS Code, Gemini CLI, and Zed, design a server that loses presentation but never capability on a host that supports only tools, serve the same server locally over stdio and remotely over Streamable HTTP from one process, and diagnose the failures behind most "it will not connect" reports.
- **Coverage:** the brief (a team knowledge base with BM25 search), building it once, six hosts and six files, local against remote from one process, the seventh client with no configuration file, graceful degradation asserted rather than claimed, verifying on each client, when this shape is wrong, a troubleshooting appendix.
- **Dependencies:** requires mcp-022; recommends mcp-004, mcp-007, mcp-020; supports mcp-024.
- **Visuals:** hero `01-one-server-five-hosts.svg` (one server, the hosts, and the SDK client, each configuration file named); the capability matrix; local and remote side by side.
- **Lab/gate:** Run `code/23-knowledge-base/` and connect it from at least two of the six hosts using the committed configuration files, then from the SDK client over HTTP; run the degradation test that asserts capability is kept when presentation is dropped.
- **Anchors:** specification "Transports" page, revision 2026-07-28; each host's own configuration documentation; the MCP extensions client support matrix (2026); Robertson and Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond" (2009).

### mcp-024 - MCP Apps, extensions, and where the protocol goes next

- **Meta:** Part VI; advanced; core; proof none; code included.
- **Central question:** If the core protocol is deliberately finished, how does anything new arrive, and what does the first official extension let a server do?
- **Promise:** The reader can declare, detect, and gracefully degrade any MCP extension, ship a tool that returns a rendered interface with a text answer that stands on its own, read the `postMessage` dialect a view and a host speak and tell it apart from MCP, and take a change through the Specification Enhancement Proposal process.
- **Coverage:** a protocol that stopped growing on purpose, MCP Apps and a tool that returns an interface, the sandbox that is two iframes, the `postMessage` dialect, the worked example and what the SDK ships, what is still open (pinning, identity, agent-to-agent), how to change any of this, where to go from here.
- **Dependencies:** requires mcp-023, mcp-009; supports none.
- **Visuals:** hero `01-a-tool-result-that-renders.svg`; the app sandbox and its message channel; the road ahead.
- **Lab/gate:** Run `code/24-mcp-app/` in a host that supports Apps and in one that does not, and confirm the same tool call renders a widget in the first and a complete text answer in the second.
- **Anchors:** MCP Apps extension specification, revision 2026-01-26 (`ext-apps`); SEP-1865 (Stable); SEP-2133 (Final); specification "Versioning" extension negotiation, revision 2026-07-28; extensions overview and client support matrix; the MCP Apps launch announcement (2026).

# 6. Series-specific contracts

## Notation and terminology

- `notation_guide.md` is the single source of truth for terms, typography, and spelling. Protocol method names, `_meta` keys, header names, and result fields are written in code font exactly as the specification spells them (`server/discover`, `resultType`, `Mcp-Method`, `isError`).
- Roles are always host, client, server, in that order, and "client" never means "the application the user runs"; that is the host.
- The series uses American spelling as a review rule; the checker only warns on the minority variant.
- Inline math is `$...$` where it occurs (the $N \times M$ argument); no em-dashes in prose written after the lock, and at most ten per existing post until migration.

## Claim standard and ground truth

- Every claim about the protocol cites the specification page that supports it, at revision 2026-07-28. Where that text is ambiguous or an SDK lags, the post says so in a short note.
- Every transcript was produced against `mcp==2.0.0b2`, Python 3.13.5. `verify/capture.py` regenerates every figure quoted from `code/05-first-server/` and `verify/RESULTS.md` is its committed output; if a post and the record disagree, the post is wrong, except for wall-clock durations, which vary by machine.
- Every other project's figures come from running that project's own test suite, and the post names the command that produces them.
- Research notes with per-claim verification status live in `.research/` and are not published.

## Code companions

- There is no series library (`library: null`). Each project is a self-contained `uv` project under `code/`: `05-first-server`, `10-mcp-client`, `13-postgres-analyst`, `15-devops-responder`, `17-research-browser`, `20-auth`, `21-deploy` (container configuration only, no second copy of the server), `23-knowledge-base`, `24-mcp-app`. Posts 03 and 04 ship `snippets/` instead.
- Every project pins `mcp==2.0.0b2` exactly. Suites run in memory with no subprocess, socket, database, cluster, or browser; external systems are exercised only in the posts' "Running it" sections.
- Posts that extend a project (mcp-006 to mcp-009 on the first server; mcp-014, mcp-016, mcp-018 on their projects) do so in the same directory, so the code at the end of the part is the code the post describes.

## Compute and external services

- `vocab.compute` is empty; nothing in the series needs more than a laptop. PostgreSQL (mcp-013, mcp-014), a local Kubernetes cluster (mcp-015, mcp-016), a Playwright browser (mcp-017, mcp-018), and a model provider key (mcp-018, optional) are the only external dependencies, and each project's tests pass without them.

## Diagrams

- Every figure is a hand-edited SVG, post-local, numbered from 01, with `viewBox` only, `<title>` and `<desc>`, an inlined dark-mode block, and the `--mcp-*` tokens of `assets/diagrams/style/tokens.css`: client side blue, server side terracotta, a request always flowing blue to terracotta, strokes 1.5, 1.0, and 0.75 only, no emoji. Full rules in `templates/diagram-style-guide.md`.
- Excalidraw companions under `posts/NN-slug/diagrams/excalidraw/` are alternates for talks, generated deterministically by `assets/diagrams/excalidraw-generator/`, and never the figure a post embeds.
- Six motifs recur and are redrawn per post rather than shared: the protocol boundary, the stateless request, the MRTR loop, the three primitives, the trust boundary, and the one-page poster at `assets/poster/one-page-of-mcp.svg`.

## Series-specific checks

- The series has no `tools/lint_repo.py` yet; quality has been enforced by the `blog-quality` checker (`checks.py --root .`): sidecar-only frontmatter, contiguous numbering, a `> **TL;DR.**` block, a `## Common pitfalls` section, at most ten em-dashes per post, resolving relative links, and the SVG rules above. The migration pass replaces this with the family validator and keeps the SVG token check as the series-specific rule.

# 7. Sibling handoffs and deliberate omissions

## Assumed from other series

Nothing. The series is self-contained by design: JSON-RPC, OAuth 2.1, and the transport layer are built up in place, and mcp-001 has no prerequisites. No post cites a post of another family series, and no other-series id appears in Section 5.

## Handoffs to siblings not yet locked

- harness-engineering: the host built in mcp-011 is the smallest possible harness, a loop around a model plus a permission gate. Everything beyond that gate (planning, retries, memory, multi-agent orchestration, evaluation of the loop itself) belongs to harness-engineering, and mcp-011 section 9 ("what this host does not do") is the boundary. When harness-engineering is locked, its host-loop posts should recommend mcp-010 and mcp-011, and mcp-011 may gain a `supports` entry pointing at them.
- context-engineering: mcp-017 and mcp-018 make the argument that a tool result's job is to spend the model's context well (the 94.6 and 99.6 percent discards, bounded logs in mcp-015, `resource_link` instead of giant blobs). How a host budgets, compresses, and orders what it puts in front of the model is left entirely to context-engineering; this series only measures what a server hands over. When context-engineering is locked, its tool-result posts should recommend mcp-017 and the relevant ids can be added here.
- ai-security (planned): mcp-019 names fifteen attack classes and shows four controls; prompt injection as a general discipline, red-teaming, and defences beyond a single server's boundary are left to ai-security. mcp-019 is the owner of the MCP-specific threat model only.

## Deliberate omissions

- A survey of MCP servers in the wild, an SDK API reference, and a comparison against other agent protocols: named as non-goals in mcp-001 and not revisited.
- Protocol revisions before 2026-07-28: taught only as "what was removed" in mcp-003, mcp-004, mcp-008, mcp-009, and mcp-018, never as a migration chapter.
- Languages other than Python: the wire formats are language-neutral and the posts say so; no TypeScript, Go, or Rust SDK code appears.
- A deliberately malicious server as a test fixture (`code/19-security/`): named in the pre-lock plan, not built; mcp-019 instead re-reads four shipped controls.
- Running the full OAuth flow against a real identity provider: `code/20-auth/` runs a toy authorization server in process so the three-party flow is reproducible; production IdP configuration is left to the reader's provider documentation.

# 8. Projects

`series.yaml` lists no projects (`projects: []`, `next_project_ordinal: 1`), and there is no `projects/` directory. The four builds the posts call "Project 1" to "Project 4" are posts of Part IV and Part VI with code companions under `code/`, not STANDARD.md Section 15 projects: the PostgreSQL analyst (mcp-013, mcp-014; `code/13-postgres-analyst/`), the Kubernetes first responder (mcp-015, mcp-016; `code/15-devops-responder/`), the research browser (mcp-017, mcp-018; `code/17-research-browser/`), and the team knowledge base (mcp-023; `code/23-knowledge-base/`). If any of them is later released as a service with its own `project.yaml`, it takes `mcp-p01` onward and a `##` here; the knowledge base, already served over both transports to six hosts, is the natural first candidate, after mcp-023.

# 9. Risks and decisions

## Risk register

- Pre-release SDK. Every transcript was produced against `mcp==2.0.0b2`, and the SDK's own `main` documentation already disagrees with it in places. Mitigation: exact pins in every project, `verify/RESULTS.md` as the diff target, and the README's timing note; re-run `verify/capture.py` when a stable `mcp>=2,<3` ships and update the posts from the record.
- Specification drift. The 2026-07-28 revision was a release candidate while the series was written and the Apps extension (2026-01-26) predates the stateless revision. Mitigation: mcp-009, mcp-022, and mcp-024 each name the places where the primary sources disagree; those notes are the first thing to revisit on the next revision.
- Sidecar gap. The sidecars carry only `slug`, `title`, `date`, `tags`, `hero`, `reading_time`, `part`, and now `id`; they lack `summary`, `updated`, `status`, `level`, `prerequisites`, `code`, and `outcomes`. Until migration, this plan is the only record of level and dependencies and the validator cannot cross-check them.
- Registry status. mcp-022 depends on a registry that is in preview and on a moderation policy that keeps vulnerable servers listed; mcp-019 and mcp-022 both say so, and both need re-reading when the registry goes generally available.
- External systems in projects. PostgreSQL, Kubernetes, and Playwright versions move; the suites run without them, but the "Running it" sections will age.
- Spelling. `-ise` and `-ize` are mixed across posts; the checker only warns.

## Decisions locked

- One revision, 2026-07-28, taught exclusively; removed mechanisms appear only as things the reader will meet in older code.
- Wire format before SDK call, in every post from mcp-003 on; mcp-001 and mcp-002 stay code-free.
- Six parts and the shipped order are the lock; the pre-lock plan's numbering already matched the directories, so no renumbering was needed.
- Part strings were normalised in all 24 sidecars: the separator after the roman numeral was an em-dash and is now a spaced hyphen (`Part I - Foundations`); the parts' titles are otherwise unchanged.
- Levels: Part I and mcp-005 beginner; mcp-006 to mcp-019 and mcp-021 to mcp-023 intermediate; mcp-020 and mcp-024 advanced. Decided from the posts' TL;DR and outcomes, since the sidecars carry no `level`.
- Dependencies: `requires` is the smallest set of posts whose code or mechanism a post builds on directly (the project second halves require their first halves and mcp-008; mcp-021 requires mcp-005 and mcp-004; mcp-024 requires mcp-023 and mcp-009); `recommends` follows the posts' own "What to read next" and inline back-references. Decided from the body, since the sidecars carry no `prerequisites`.
- `proof none` for every post: the series makes engineering claims backed by transcripts and tests, not derivations.
- No cross-series ids: harness-engineering and context-engineering are described in prose in Section 7 until they are locked.
- The four "projects" stay posts, not Section 15 projects, until one is released as a service.
- Spec titles keep the sidecar titles verbatim after the `NN · ` prefix is removed, including the `Project N · ` middle dot and title-case product names (`Streamable HTTP`, `OAuth 2.1`, `MCPB`); the middle dots are a house-style divergence (STANDARD.md Section 18.3) recorded here for the migration commit, which will decide between keeping them and rewriting to `Project 1: ...`.

## Recorded divergences

- Sidecars lack `level` and `prerequisites` (and `recommended`, `summary`, `updated`, `status`, `code`, `outcomes`). Section 5 is the sole record. At migration each sidecar gains `level` and `prerequisites` (and `recommended` where Section 5 names one) equal to its spec, `summary` from the TL;DR, `status: complete`, `code` from Meta, and `outcomes` from the "After reading this you will be able to" block.
- Sidecar titles carry the `NN · ` prefix and the body `# NN · Title` heading repeats it; the standard's title form drops the number. Migration work.
- mcp-019 lists three outcomes where every other post lists four; the spec's Promise follows the three.
- mcp-022's sidecar title has `server.json` without code font; the body heading and the pre-lock plan have it in code font. The spec uses the sidecar form.
- The README title is "What The Hell is MCP?" and the registry directory is "MCP What it is"; `series.yaml: title` follows the README.
- "What to read next" sections carry two or three bullets (the family template asks for exactly two); mcp-024's bullets point at the reader's own server and the specification rather than at posts. Migration work, not a curriculum change.
- The posts contain `## Common pitfalls` and `## Further reading` but no `## Exercises`, so the 2.1.0 ban on Exercises in posts is already satisfied; there are no `questions.json` banks yet.
- The posts' em-dash budget (at most ten per post) and `-ise`/`-ize` mix predate the lock and are migration work.
- The pre-lock plan's "Code" line for mcp-019 names a malicious-server fixture that was never built; recorded in Section 7 as an omission, not as a planned post.

# 10. Roadmap

All 24 posts are shipped and every quoted figure is reproducible from `code/` and `verify/RESULTS.md`. Next, in order:

1. Lock commit: `Lock the curriculum`, tag `lock-1.0.0`. This plan, `series.yaml`, the `id:` lines and normalised `part:` strings in the 24 sidecars, and `audits/plan-pre-lock.md` (the former `PLAN.md`) are its contents.
2. Sidecar migration (`series-standard/tools/migrate_2_1_0.py` and a hand pass): add `level`, `prerequisites`, `recommended`, `summary`, `updated`, `status`, `code`, and `outcomes` to every sidecar from Section 5 and the posts; drop the `NN · ` prefix from titles and body headings; decide the `Project N · ` title form; bring "What to read next" to two bullets; then run `validate.py` without `--lock` until the layout, sidecar, and body stages pass.
3. Banks: write `questions.json` for every post from the "After reading this you will be able to" outcomes and the Common pitfalls, to the family floor, and review each in the viewer.
4. Tooling: add `tools/lint_repo.py` wrapping the family validator and carrying the SVG token check; retire the `blog-quality` dependency.
5. Re-verify against the stable SDK: when `mcp>=2,<3` ships, re-run `verify/capture.py` and every project suite, update `verify/RESULTS.md`, and revise the notes in mcp-009, mcp-018, mcp-022, and mcp-024 where the sources had disagreed.
6. Siblings: when harness-engineering and context-engineering lock, add the `supports` edges described in Section 7 and bump this plan to 1.1.0.
