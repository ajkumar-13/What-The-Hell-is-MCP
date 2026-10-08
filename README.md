# What The Hell is MCP?

A free, beginner-first series of 24 posts on the **Model Context Protocol (MCP)**, the open standard that lets AI applications talk to tools and data, taught from zero against protocol revision **2026-07-28** and nothing else. It starts with the $N \times M$ integration argument, reads the wire format and both transports field by field before any SDK call, and then builds a server, a client, and a permission-gated host in Python. Six posts build four real projects: a PostgreSQL analyst, a Kubernetes first responder, a research browser, and a knowledge base served to six hosts. Four more cover security, authorization, deployment, and publishing. A reader who finishes it can design, build, test, secure, deploy, and publish an MCP server that real hosts connect to, write a client and a host from a blank file, and read the specification itself without a guide.

Every code sample is Python. The protocol itself is language-neutral, and the wire formats shown here are exactly what a TypeScript, Go, or Rust server would send; only the SDK calls would differ.

## What a reader builds

- A system-information server, grown across Part II from one tool to tools with structured output, resources, a prompt, completions, an elicitation-gated tool, and long-running work.
- A client that connects over stdio, Streamable HTTP, or in memory through one code path, and a host that merges many servers into one catalog behind a permission gate.
- An in-memory test suite for each of them, with a real client on the other end and no subprocess, socket, or host.
- Four projects: a read-only PostgreSQL analyst that then gains audited, human-approved writes; a Kubernetes first responder that diagnoses and then remediates with approval and blast-radius limits; a research browser that measures what it discards and calls a model of its own; and one knowledge-base server configured for six hosts and a plain Python client.
- The production layer: an OAuth 2.1 resource server, a container behind a round-robin load balancer with tracing, and a `server.json` ready for the registry.

## The posts

### Part I - Foundations

After this part a reader can state the $N \times M$ argument, assign any responsibility to the host, the client, or the server, write a valid MCP request by hand with nothing but an HTTP client, and choose a transport from the shape of a deployment.

1. [01 - What MCP is, and the problem it solves](posts/01-what-is-mcp/index.md)
2. [02 - The architecture: hosts, clients, and servers](posts/02-architecture/index.md)
3. [03 - The wire protocol: JSON-RPC, discovery, and the stateless model](posts/03-wire-protocol/index.md)
4. [04 - Transports: stdio and Streamable HTTP](posts/04-transports/index.md)

### Part II - Building servers

After this part a reader has a running server with tools, a templated resource, a prompt, a completion handler, an elicitation-gated tool, and a long-running task, and can say which primitive a given piece of data belongs behind.

5. [05 - Your first MCP server](posts/05-first-server/index.md)
6. [06 - Tools in depth: schemas, structured output, and annotations](posts/06-tools-in-depth/index.md)
7. [07 - Resources and prompts: the primitives that are not tools](posts/07-resources-and-prompts/index.md)
8. [08 - Elicitation and MRTR: asking the user mid-call](posts/08-elicitation-and-mrtr/index.md)
9. [09 - Tasks: work that outlives a single request](posts/09-tasks/index.md)

### Part III - Clients, hosts, and testing

After this part a reader has written a client and a permission-gated multi-server host and can test any server in memory with no subprocess and no human.

10. [10 - Building your own MCP client](posts/10-mcp-client/index.md)
11. [11 - Building a host: the tool loop, many servers, and permissions](posts/11-building-a-host/index.md)
12. [12 - Testing and debugging MCP](posts/12-testing-and-debugging/index.md)

### Part IV - Projects

After this part a reader has built three projects whose refusals, approvals, and byte counts are checked by committed tests, and can put a human on the critical path of a mutation in a parameter the model cannot forge.

13. [13 - Project 1 · A secure database analyst](posts/13-database-analyst/index.md)
14. [14 - Project 1 · Writes, transactions, and an audit trail](posts/14-database-writes/index.md)
15. [15 - Project 2 · A DevOps first responder](posts/15-devops-responder/index.md)
16. [16 - Project 2 · Safe remediation with approval](posts/16-devops-remediation/index.md)
17. [17 - Project 3 · A deep research browser](posts/17-research-browser/index.md)
18. [18 - Project 3 · Server-side model calls and multi-page research](posts/18-server-side-models/index.md)

### Part V - Production

After this part a reader can name every attack class by where it crosses the trust boundary, validate a token for an audience that is the server itself, containerize a server behind a round-robin load balancer, and publish it to the registry.

19. [19 - Security: the attacks the protocol does not stop](posts/19-security/index.md)
20. [20 - Authorization: OAuth 2.1 for MCP servers](posts/20-authorization/index.md)
21. [21 - Deploying to production: containers, scaling, and observability](posts/21-deploying/index.md)
22. [22 - Publishing: the registry, server.json, and MCPB bundles](posts/22-publishing/index.md)

### Part VI - Interoperability and the frontier

After this part a reader can configure the same server in six hosts, declare and degrade any extension, ship a tool that returns an interface, and take a change through the Specification Enhancement Proposal process.

23. [23 - Project 4 · One server, every client](posts/23-multi-client/index.md)
24. [24 - MCP Apps, extensions, and where the protocol goes next](posts/24-mcp-apps-and-frontier/index.md)

## Where to start

A reader who has never built an MCP server starts at Post 01, which gets a published server running in a host the reader already uses, before any theory. From there the reader can take Part I in order, or go straight to Post 05, write a server, and come back for the wire format later.

A reader who already builds servers against an older revision and wants only what changed in 2026-07-28 reads three posts: [Post 03](posts/03-wire-protocol/index.md) for the stateless model, [Post 08](posts/08-elicitation-and-mrtr/index.md) for what replaced the server-to-client channel, and [Post 09](posts/09-tasks/index.md) for long-running work and the extension model.

A reader about to ship a server to other people reads [Post 19](posts/19-security/index.md) first. It is the post most likely to change what gets built. A reader with ten minutes reads the [cheatsheet](CHEATSHEET.md).

## Before starting

The series assumes comfort reading Python (functions, classes, `async` and `await`) and a willingness to run commands in a terminal. It assumes no protocol, networking, OAuth, or AI-engineering background: JSON-RPC 2.0, OAuth 2.1, and the transport layer are each built up from scratch where they are needed, and every acronym is expanded on first use in every post. It depends on no other series. The tools are Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and a terminal; the projects of Part IV also talk to PostgreSQL, a local Kubernetes cluster, and a headless browser in their "Running it" sections, but their test suites need none of them.

## How a post is organized

Each post opens with a short summary, the prerequisites, and what the reader will be able to do afterwards, then works through numbered sections from the question to the code, and closes with what can go wrong, the common pitfalls, further reading, and what to read next. The order inside a post is always picture, then intuition, then wire format, then code: the JSON that crosses the wire is shown before the SDK call that produces it, so a reader can debug with `curl` and a text editor rather than trusting a library. Every post has hand-made figures that read correctly in light and dark mode, in a colorblind-safe palette shared across the series: client side blue, server side terracotta, and a request always flowing from blue to terracotta. The series website shows each post's questions, with hints and worked explanations, beside the post.

## The protocol revision and the SDK

- **Protocol revision:** 2026-07-28, throughout. Removed mechanisms (`initialize`, sessions, the server-to-client request channel) appear only as things a reader will meet in older code.
- **Python SDK:** `mcp==2.0.0b2`, pinned exactly in every project and in the root environment.

**A note on timing.** This edition was written as the 2026-07-28 revision went final and while the Python SDK's 2.0 line was still in beta. Every code sample was executed against `mcp==2.0.0b2`, not transcribed from documentation, but that is a pre-release, and the SDK repository's own `main` documentation already disagrees with it in a few places. Each project pins the exact version it was tested against. A reader arriving some months later should expect a stable `mcp>=2,<3` to exist, and a handful of the smaller details here to have moved.

## What is measured

- Every transcript in a post was produced against `mcp==2.0.0b2`, and every claim about the protocol cites the specification page that supports it.
- Every project ships a test suite that runs in memory, with no subprocess, socket, database, cluster, or browser. Run with `uv run --extra dev pytest` in each project directory: `code/05-first-server/` passes 19 tests, `code/10-mcp-client/` 68, `code/13-postgres-analyst/` 85 (with 12 more that run only against a live PostgreSQL), `code/15-devops-responder/` 78, `code/17-research-browser/` 75 (with one more that needs the optional model-provider extra), `code/20-auth/` 21, `code/23-knowledge-base/` 51, and `code/24-mcp-app/` 11.
- Where a post quotes a number, it names the script or the test suite that produces it. Wall-clock durations vary by machine and are given as examples, not as results.

## The code

There is no library to install: each project is a self-contained `uv` project under [code/](code/), and the posts that extend a project (Posts 06 to 09 on the first server, and the second halves of the projects) do so in the same directory, so the code at the end of a part is the code the posts describe.

```text
posts/NN-slug/   the post (index.md), its figures, and the scripts it runs (snippets/)
code/            the projects, each with its own README, pyproject.toml, uv.lock, and tests
poster/          the one-page reference sheet
```

| Directory | What it is | Posts |
|---|---|---|
| [code/05-first-server/](code/05-first-server/) | the system-information server | 05 to 09, 12, 21, 22 |
| [code/10-mcp-client/](code/10-mcp-client/) | the client and the multi-server host | 10 to 12 |
| [code/13-postgres-analyst/](code/13-postgres-analyst/) | Project 1, the database analyst | 13, 14 |
| [code/15-devops-responder/](code/15-devops-responder/) | Project 2, the DevOps first responder | 15, 16 |
| [code/17-research-browser/](code/17-research-browser/) | Project 3, the research browser | 17, 18 |
| [code/20-auth/](code/20-auth/) | the OAuth 2.1 resource server | 20 |
| [code/21-deploy/](code/21-deploy/) | container configuration for the first server, with no second copy of it | 21 |
| [code/23-knowledge-base/](code/23-knowledge-base/) | Project 4, one server for every host | 23 |
| [code/24-mcp-app/](code/24-mcp-app/) | a tool that returns an MCP Apps interface | 24 |

To run a project and its tests:

```bash
cd code/05-first-server
uv sync --extra dev
uv run python -m system_info              # stdio, what a desktop host spawns
uv run pytest
```

The scripts under a post's `snippets/` run from the series root in the root environment (`pyproject.toml` and `uv.lock` at the root, which pin the same SDK):

```bash
uv run python posts/03-wire-protocol/snippets/raw_discover.py
```

Everything runs on a laptop. A stdio server writes nothing to standard output except protocol messages; every project logs to standard error.

## Reference pages

- [Glossary](GLOSSARY.md): every term the series uses, including the ones a reader will meet only in older code.
- [Cheatsheet](CHEATSHEET.md): one printable page with the method table, the `_meta` keys, the MRTR loop, the error codes, the SDK changes, the host configuration matrix, and a security checklist.
- [Notation guide](notation_guide.md): the names, typography, and spelling the posts use.
- [References](REFERENCES.md): every specification page, proposal, paper, and piece of documentation the posts cite.

## The poster

[poster/one-page-of-mcp.svg](poster/one-page-of-mcp.svg) puts the whole protocol on one canvas, in eight panels: the three roles, the three primitives and who triggers each, the shape of a self-describing request, every method including what this revision removed, the MRTR loop, the split between protocol errors and tool execution errors, the two transports, and a security checklist.

## Corrections and feedback

This repository is the published source of the series and is maintained by its author. It does not take pull requests or issues. The protocol is young and its documentation moves, so a dead link or a stale claim is a real bug: corrections and suggestions go through the series website.

## License

Prose, questions, and figures are licensed under CC BY 4.0. Code is licensed under MIT. See [LICENSE](LICENSE).
