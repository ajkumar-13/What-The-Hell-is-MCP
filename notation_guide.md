# Notation and naming guide

One convention, shared by every post in this series and aligned with the sibling
"from scratch" series (`ME_IT`, `nnfs`, `rnn_from_scratch`). The goal is that a term
means the same thing in every post: a reader who learns in Post 02 that "client" is
the per-server connection object never has to relearn it in Post 19.

Two rules govern everything below:

1. **One concept, one name — everywhere.** Never call the same thing a "connector" in
   one post and an "adapter" in the next.
2. **Re-introduce each term on first use in *every* post**, because readers arrive
   mid-series from a search engine.

---

## 1. The three roles (the most-confused vocabulary in MCP)

| Term | What it is | What it is **not** |
|---|---|---|
| **Host** | The application the user interacts with, and the thing that owns the model, the conversation, and the permission decisions. Claude Desktop, Cursor, VS Code, your own CLI. | Not "the client". A host contains clients. |
| **Client** | The protocol-speaking object inside the host. Exactly one per connected server. Owns transport, request-id correlation, and the capability cache. | Not the UI, and not something the reader usually writes by hand until Post 10. |
| **Server** | The process that exposes tools, resources, and prompts. Usually the reader's own code. | Not necessarily remote, and not necessarily a web service. |

Write them lower-case in prose ("the host decides"), capitalized only at the start of a
sentence. Never write them in ALL-CAPS.

## 2. Protocol vocabulary

| Use | Never use |
|---|---|
| **revision** (for `2026-07-28`) | "version" when you mean the protocol revision |
| **method** (for `tools/call`) | "endpoint", "function", "RPC call" |
| **primitive** (for tools/resources/prompts) | "feature", "capability" when you mean a primitive |
| **capability** (for what a party declares it supports) | "primitive" |
| **extension** (for `io.modelcontextprotocol/tasks`) | "plugin", "add-on" |
| **tool call** | "function call", "tool invocation" |
| **the model** | "the AI", "the LLM" in body prose (fine on first expansion) |

These bans govern *your* prose, not the specification's. Quote the spec exactly when you
quote it — it says "tool invocations" and "the LLM" in places — but never paraphrase spec
text into body prose using the banned word.

### Who controls each primitive

The specification's own words are **model-controlled** (tools), **application-driven**
(resources), and **user-controlled** (prompts). Use the spec's wording. Some sources, and
this series' own earlier shorthand, say "application-controlled" for resources; that phrase
means the same thing but is not what the spec says, so prefer *application-driven* and do
not mix the two within a post.

### Tool names

A tool name may contain ASCII letters, digits, `_`, `-`, and `.` only. **A slash is not
allowed.** When a host has to disambiguate colliding names from two servers, the prefix
separator is therefore a dot (`files.search`), never a slash. Some model providers narrow the
set further, to letters, digits, `_`, and `-` with no dot, so a host may fall back to an
underscore (`files_search`); a slash is never an option on either side. A server's
self-reported name is not guaranteed unique, and the specification says it **SHOULD NOT** be
relied upon for disambiguation. Take the prefix from the key you gave the connection in your
own configuration instead.

## 3. How identifiers are typeset

| Kind | Style | Example |
|---|---|---|
| Protocol method | code span, exact casing | `tools/call`, `server/discover`, `subscriptions/listen` |
| JSON field | code span, exact casing | `structuredContent`, `resultType`, `inputRequests` |
| HTTP header | code span, exact casing | `MCP-Protocol-Version`, `Mcp-Method` |
| Reserved `_meta` key | code span, full reverse-DNS | `io.modelcontextprotocol/protocolVersion` |
| Extension id | code span, full reverse-DNS | `io.modelcontextprotocol/tasks` |
| Python symbol | code span | `MCPServer`, `@mcp.tool()` |
| File path | link if it exists in the repo, else code span | [code/13-postgres-analyst/](code/13-postgres-analyst/) |
| Error code | code span; name it in parentheses on first use in a post | `-32602` (`Invalid params`), then `-32602` |

Field names are **camelCase on the wire** and **snake_case in Python**. When a post shows
both, say so once rather than silently switching:

> On the wire the field is `structuredContent`; the Python SDK exposes it as
> `structured_content`.

## 4. Spelling

This series is **American-majority**: *serialize*, *authorization*, *behavior*, *labeled*
and *traveled* (one `l`), *catalog* (no `-ue`), *canceled* (one `l`, matching the spec's
`notifications/cancelled` only when quoting the literal method name — the method itself is
spelled with two `l`s, so quote it exactly and do not "fix" it in code spans).

The same holds for identifiers in [code/](code/), with no grandfathered exceptions: an
identifier is renamed rather than quoted around. Where a post shows a function or a test
name, the post and the source move together, so a quoted name is always byte-identical to
the code it comes from.

The checker flags minority variants of `-ise`/`-ize`, `-isation`/`-ization`,
`-iser`/`-izer`, `-ised`/`-ized`. It does **not** catch the doubled-consonant family
(*labelled*, *travelled*, *modelled*) or `-ogue` (*catalogue*), so those are on review.
Keep to the `z` and single-`l` forms in prose.

## 5. Numbers, versions, and dates

- Protocol revisions are always the full date string: **2026-07-28**, never "the July spec".
- SDK versions are always pinned in prose the way `pyproject.toml` pins them:
  `mcp[cli]==2.0.0b2` in a dependency list, `mcp==2.0.0b2` when naming the build a result
  was measured on. Never a range.
- Never invent a benchmark, a latency figure, or a tool output. If a number appears in a
  post it must come from a citation in [REFERENCES.md](REFERENCES.md), or from a script in
  [code/](code/) that the reader
  can run.
- Where a figure was measured against a throwaway server that is not committed, say so in
  the sentence that quotes it, and give the exact package version and configuration so the
  reader can rebuild it. Never let such a figure sit behind a blanket "produced by running
  the code" claim.

## 6. Math typography (rare here, but shared with the sibling series)

| Kind | Style | Example |
|---|---|---|
| Matrix | bold upper-case | $\mathbf{W}$ |
| Vector | bold lower-case | $\mathbf{x}$ |
| Scalar | italic lower-case | $i$, $\alpha$ |

Canonical symbols, identical across all sibling series. The parenthesized alternatives below
are banned and the checker enforces them: learning rate is $\alpha$ (never $\eta$), loss is
$L$ (never $\mathcal{L}$), bias is lower-case $\mathbf{b}$ (never $\mathbf{B}$), and the
small numerical constant is $\epsilon$ (never $\varepsilon$).

## 7. Checklist before publishing a post

- [ ] Host / client / server used exactly as defined in §1.
- [ ] Every protocol method, JSON field, and header spelled exactly as the 2026-07-28
      spec spells it, in a code span.
- [ ] Every acronym expanded on first use *in this post*.
- [ ] American spelling in prose.
- [ ] No invented numbers or fabricated tool output.
- [ ] `python <skill>/checks.py --root . --only notation,spelling` passes.
