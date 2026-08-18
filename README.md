# What The Hell is MCP?

A free, beginner-first series on the **Model Context Protocol (MCP)** — the open standard
that lets AI applications talk to your tools and your data — taught from absolute zero
against protocol revision **2026-07-28**.

No protocol background, no networking background, and no OAuth background are assumed.
If you can read Python and run a command in a terminal, you have the prerequisites.

This repository contains the source for all 24 posts, every diagram as a self-contained SVG,
eight runnable project codebases, and a one-page poster of the whole protocol.

Every code sample is Python. The protocol itself is language-neutral, and the wire formats
shown here are exactly what a TypeScript, Go, or Rust server would send; only the SDK calls
would differ.

> **License:** prose CC-BY 4.0 · code MIT
> **Protocol revision:** 2026-07-28, throughout. Post 01 explains why.
> **Python SDK:** `mcp==2.0.0b2`, pinned exactly in every project.

> **A note on timing, in the open.** This edition was written as the 2026-07-28 revision
> went final and while the Python SDK's 2.0 line was still in beta. Every code sample here
> was executed against `mcp==2.0.0b2` — not transcribed from documentation — but that is a
> pre-release, and the SDK repository's own `main` documentation already disagrees with it
> in a few places. Each project pins the exact version it was tested against. If you are
> reading this some months later, expect a stable `mcp>=2,<3` to exist, and expect a
> handful of the smaller details here to have moved.

---

## Start here

Read the posts in order, or jump to any one. Each stands alone and links forward and back.

If you have never built an MCP server, **start at Post 01.** Its section 3 gets a published
server running in your own host in about ten minutes, before any theory. From there, either
read Part I in order, or go straight to Post 05 and write a server and come back for the wire
format later. Post 01 section 9 lays out both routes. The four foundation posts are written
so that someone who has never seen JSON-RPC can follow every step.

If you already build servers and only want what changed in 2026-07-28, read these three:

1. [Post 03 — The wire protocol](posts/03-wire-protocol/index.md), for the stateless model
2. [Post 08 — Elicitation and MRTR](posts/08-elicitation-and-mrtr/index.md), for what
   replaced the server-to-client channel
3. [Post 09 — Tasks](posts/09-tasks/index.md), for long-running work and the extension model

If you have ten minutes, read the [cheatsheet](CHEATSHEET.md).

If you are about to ship a server to other people, read
[Post 19 — Security](posts/19-security/index.md) first. It is the post most likely to
change what you build.

---

## The series

### Part I — Foundations

| #  | Title | |
|----|-------|-|
| 01 | What MCP is, and the problem it solves | [read](posts/01-what-is-mcp/index.md) |
| 02 | The architecture: hosts, clients, and servers | [read](posts/02-architecture/index.md) |
| 03 | The wire protocol: JSON-RPC, discovery, and the stateless model | [read](posts/03-wire-protocol/index.md) |
| 04 | Transports: stdio and Streamable HTTP | [read](posts/04-transports/index.md) |

### Part II — Building servers

| #  | Title | |
|----|-------|-|
| 05 | Your first MCP server | [read](posts/05-first-server/index.md) |
| 06 | Tools in depth: schemas, structured output, and annotations | [read](posts/06-tools-in-depth/index.md) |
| 07 | Resources and prompts: the primitives that are not tools | [read](posts/07-resources-and-prompts/index.md) |
| 08 | Elicitation and MRTR: asking the user mid-call | [read](posts/08-elicitation-and-mrtr/index.md) |
| 09 | Tasks: work that outlives a single request | [read](posts/09-tasks/index.md) |

### Part III — Clients, hosts, and testing

| #  | Title | |
|----|-------|-|
| 10 | Building your own MCP client | [read](posts/10-mcp-client/index.md) |
| 11 | Building a host: the tool loop, many servers, and permissions | [read](posts/11-building-a-host/index.md) |
| 12 | Testing and debugging MCP | [read](posts/12-testing-and-debugging/index.md) |

### Part IV — Projects

| #  | Title | |
|----|-------|-|
| 13 | Project 1 · A secure database analyst | [read](posts/13-database-analyst/index.md) |
| 14 | Project 1 · Writes, transactions, and an audit trail | [read](posts/14-database-writes/index.md) |
| 15 | Project 2 · A DevOps first responder | [read](posts/15-devops-responder/index.md) |
| 16 | Project 2 · Safe remediation with approval | [read](posts/16-devops-remediation/index.md) |
| 17 | Project 3 · A deep research browser | [read](posts/17-research-browser/index.md) |
| 18 | Project 3 · Server-side model calls and multi-page research | [read](posts/18-server-side-models/index.md) |

### Part V — Production

| #  | Title | |
|----|-------|-|
| 19 | Security: the attacks the protocol does not stop | [read](posts/19-security/index.md) |
| 20 | Authorization: OAuth 2.1 for MCP servers | [read](posts/20-authorization/index.md) |
| 21 | Deploying to production: containers, scaling, and observability | [read](posts/21-deploying/index.md) |
| 22 | Publishing: the registry, `server.json`, and MCPB bundles | [read](posts/22-publishing/index.md) |

### Part VI — Interoperability and the frontier

| #  | Title | |
|----|-------|-|
| 23 | Project 4 · One server, every client | [read](posts/23-multi-client/index.md) |
| 24 | MCP Apps, extensions, and where the protocol goes next | [read](posts/24-mcp-apps-and-frontier/index.md) |

---

## The four projects

Each project is a real codebase under [code/](code/), not a snippet. Each one exists to
teach a specific hard part of MCP that a toy server never reaches.

**1. A secure database analyst** (posts 13 and 14) — read access to PostgreSQL that is
safe because the *server* decides what "read" means, then write access that is defensible
because every mutation is previewed to a human, wrapped in a transaction, and audited.

**2. A DevOps first responder** (posts 15 and 16) — a read-only Kubernetes diagnostic
server, then remediation with preview, approval, verification, and blast-radius limits
checked before anyone is asked.

**3. A deep research browser** (posts 17 and 18) — headless browsing that throws away the
95 percent of a page that would waste the model's context, then multi-page research with
citations, and an honest look at what replaced sampling.

**4. A multi-client knowledge base** (post 23) — one server, proven against Claude Desktop,
Claude Code, Cursor, VS Code, Gemini CLI, Zed, and a plain Python client, with the
configuration file each one actually wants.

---

## Why this edition targets 2026-07-28

The 2026-07-28 revision is the largest change to MCP since it launched. The protocol became
**stateless**: there is no `initialize` handshake, no session id, and no channel for a
server to call back into a client.

That is not a footnote. It changes how you ask the user a question mid-tool-call, how a
long-running job reports progress, and how you scale a server. A series that taught the old
model and appended a migration chapter would be teaching a shape of thinking that no longer
fits.

So this edition teaches the new model from post 01, and names the old one only where a
reader will meet it in existing code. [Post 03](posts/03-wire-protocol/index.md) has the
full table of what changed.

---

## Repository layout

```
posts/NN-slug/
├── index.md          # the post
├── frontmatter.yaml  # title, date, tags, hero, reading time
└── diagrams/         # the figures it embeds, as SVG

code/                 # eight runnable projects, plus the deployment configs for post 21
poster/               # the one-page reference sheet
```

Each post is plain Markdown with relative links, so it reads on GitHub as-is. Nothing here
needs a site generator.

---

## Reference material

- [GLOSSARY.md](GLOSSARY.md) — every term, one line each.
- [CHEATSHEET.md](CHEATSHEET.md) — one printable page: the method table, the `_meta` keys,
  the MRTR loop, the security checklist, the client configuration matrix.
- [REFERENCES.md](REFERENCES.md) — every citation in the series.
- [notation_guide.md](notation_guide.md) — naming and typography conventions.
- [poster/one-page-of-mcp.svg](poster/one-page-of-mcp.svg) — the whole protocol on one
  canvas, drawn by hand and designed to print at A2.
- [code/](code/) — eight runnable projects. Every measured figure a post quotes comes from
  running one of them, and each ships a test suite that reproduces it.

---

## The diagrams

Every diagram is hand-drawn, and every one is a self-contained SVG that reads correctly in
both light and dark mode. The palette is colorblind-safe and consistent across all 24 posts:
client side is blue, server side is terracotta, and a request always flows blue to
terracotta.

You are welcome to copy, remix, or translate them under CC-BY 4.0. Please credit the series
and link back.

---

## Contributing

Typos, broken links, clarity fixes, and spec-drift corrections are all welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md). For larger changes please open an issue first.

Spec drift is the most valuable kind of report. This protocol is young and its
documentation moves; if a link here is dead or a claim is stale, that is a real bug.

---

## Why free?

MCP is becoming the way software talks to models, and the on-ramp should not sit behind a
paywall. If this series helps you, the best thank-you is to send it to one other person who
is trying to work out what the hell MCP is.

---

## License

Prose and diagrams are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Code is licensed under the MIT License. See [LICENSE](LICENSE) for the full text and for
which files fall under which.
