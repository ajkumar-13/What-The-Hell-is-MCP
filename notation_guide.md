# Notation and naming guide

One convention, shared by every post in this series. The family of series this one belongs to shares one palette and one typographic convention, and this page records how that convention applies to MCP. The goal is that a term means the same thing in every post: a reader who learns in Post 02 that "client" is the per-server connection object never has to relearn it in Post 19.

Two rules govern everything below:

1. **One concept, one name, everywhere.** The same thing is never a "connector" in one post and an "adapter" in the next.
2. **Each term is reintroduced on first use in every post**, because readers arrive mid-series from a search engine.

---

## 1. The three roles (the most-confused vocabulary in MCP)

| Term | What it is | What it is **not** |
|---|---|---|
| **Host** | The application the user interacts with, and the thing that owns the model, the conversation, and the permission decisions. Claude Desktop, Cursor, VS Code, a command-line host of the reader's own. | Not "the client". A host contains clients. |
| **Client** | The protocol-speaking object inside the host. Exactly one per connected server. Owns the transport, request-id correlation, and the capability cache. | Not the user interface, and not something the reader writes by hand until Post 10. |
| **Server** | The process that exposes tools, resources, and prompts. Usually the reader's own code. | Not necessarily remote, and not necessarily a web service. |

In prose the three are lower case ("the host decides") and capitalized only at the start of a sentence, never in all capitals. When the three appear together they are named in the order host, client, server.

## 2. Protocol vocabulary

| Use | Never use |
|---|---|
| **revision** (for `2026-07-28`) | "version" when the protocol revision is meant |
| **method** (for `tools/call`) | "endpoint", "function", "RPC call" |
| **primitive** (for tools, resources, and prompts) | "feature", or "capability" when a primitive is meant |
| **capability** (for what a party declares it supports) | "primitive" |
| **extension** (for `io.modelcontextprotocol/tasks`) | "plugin", "add-on" |
| **tool call** | "function call", "tool invocation" |
| **the model** | "the AI", or "the LLM" in body prose (fine on first expansion) |

These bans govern the series' own prose, not the specification's. A quotation from the specification is exact, and the specification says "tool invocations" and "the LLM" in places; a paraphrase of specification text into body prose never uses the banned word.

### Who controls each primitive

The specification's own words are **model-controlled** (tools), **application-driven** (resources), and **user-controlled** (prompts), and the series uses them. Some sources, and this series' own earlier shorthand, say "application-controlled" for resources; that phrase means the same thing but is not what the specification says, so the series prefers *application-driven* and never mixes the two within a post.

### Tool names

The specification says a tool name **SHOULD** use only ASCII letters, digits, `_`, `-`, and `.`, and **SHOULD** be 1 to 128 characters long. A slash is outside that set. When a host has to disambiguate colliding names from two servers, the prefix separator is therefore a dot (`files.search`), never a slash. Some model providers narrow the set further, to letters, digits, `_`, and `-` with no dot, so a host may fall back to an underscore (`files_search`); a slash is never an option on either side. A server's self-reported name is not guaranteed unique, and the specification says it **SHOULD NOT** be relied upon for disambiguation. The prefix comes from the key the user gave the connection in the host's own configuration instead.

## 3. How identifiers are typeset

| Kind | Style | Example |
|---|---|---|
| Protocol method | code span, exact casing | `tools/call`, `server/discover`, `subscriptions/listen` |
| JSON field | code span, exact casing | `structuredContent`, `resultType`, `inputRequests` |
| HTTP header | code span, exact casing | `MCP-Protocol-Version`, `Mcp-Method` |
| Reserved `_meta` key | code span, full reverse-DNS | `io.modelcontextprotocol/protocolVersion` |
| Extension id | code span, full reverse-DNS | `io.modelcontextprotocol/tasks` |
| Python symbol | code span | `MCPServer`, `@mcp.tool()` |
| File path | link if it exists in the repository, else code span | [code/13-postgres-analyst/](code/13-postgres-analyst/) |
| Error code | code span; named in parentheses on first use in a post | `-32602` (`Invalid params`), then `-32602` |

Field names are **camelCase on the wire** and **snake_case in Python**. When a post shows both, it says so once rather than silently switching:

> On the wire the field is `structuredContent`; the Python SDK exposes it as `structured_content`.

## 4. Spelling

The series uses **American spelling**: *serialize*, *authorization*, *behavior*, *labeled* and *traveled* (one `l`), *catalog* (no `-ue`), *canceled* (one `l`). The one exception is a literal quoted exactly: the method is spelled `notifications/cancelled`, with two `l`s, and a code span keeps that spelling rather than "fixing" it.

The same holds for identifiers in [code/](code/), with no grandfathered exceptions: an identifier is renamed rather than quoted around. Where a post shows a function or a test name, the post and the source move together, so a quoted name is always byte-identical to the code it comes from.

A search for `-ise`, `-ised`, `-iser`, and `-isation` catches most departures mechanically. It does not catch the doubled-consonant family (*labelled*, *travelled*, *modelled*) or `-ogue` (*catalogue*), so those are checked on review.

## 5. Numbers, versions, and dates

- Protocol revisions are always the full date string, **2026-07-28**, never "the July spec".
- SDK versions are pinned in prose the way `pyproject.toml` pins them: `mcp[cli]==2.3.0` in a dependency list, `mcp==2.3.0` when naming the build a result was measured on. Never a range.
- No benchmark, latency figure, or tool output is invented. A number in a post comes from a citation in [REFERENCES.md](REFERENCES.md) or from a script in [code/](code/) or in the post's own snippets that the reader can run, and the post names the command.
- Where a figure was measured against a throwaway server that is not committed, the sentence that quotes it says so and gives the exact package version and configuration, so a reader can rebuild it. Such a figure never sits behind a blanket "produced by running the code" claim.

## 6. Math typography

Math is rare in this series (the $N \times M$ integration count is the main case), and it follows the family's convention:

| Kind | Style | Example |
|---|---|---|
| Matrix | bold upper case | $\mathbf{W}$ |
| Vector | bold lower case | $\mathbf{x}$ |
| Scalar | italic lower case | $i$, $\alpha$ |

Inline math is written `$...$`. The family's canonical symbols apply where they occur: the learning rate is $\alpha$ (never $\eta$), the loss is $L$ (never $\mathcal{L}$), a bias is lower-case $\mathbf{b}$ (never $\mathbf{B}$), and the small numerical constant is $\epsilon$ (never $\varepsilon$).

## 7. Checklist before publishing a post

- [ ] Host, client, and server used exactly as Section 1 defines them.
- [ ] Every protocol method, JSON field, and header spelled exactly as the 2026-07-28 specification spells it, in a code span.
- [ ] Every acronym expanded on first use in this post.
- [ ] American spelling in prose.
- [ ] No invented numbers or fabricated tool output.
- [ ] Every relative link resolves, forward slashes only.
