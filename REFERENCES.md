# References

Every citation used anywhere in this series, grouped by kind, together with the primary
sources behind claims the posts make without naming them. Posts cite by title and year and
link here for the full entry. If a post makes a claim about the protocol, the supporting
page is in section 1.

Everything below was checked on **2026-07-26**. Documentation URLs in this ecosystem move
often; where a page has already moved once, the old location is noted. A dagger (†) marks a
path inferred from the site's URL scheme rather than printed by a post: re-verify those
before relying on them.

---

## 1. The specification

The series targets revision **2026-07-28**. While that revision was a release candidate it
lived at `/specification/draft/`; after it goes final the same pages are served from
`/specification/2026-07-28/`.

| Topic | URL |
|---|---|
| Versioning and revision index | https://modelcontextprotocol.io/specification/versioning |
| 2026-07-28 changelog | https://modelcontextprotocol.io/specification/draft/changelog |
| Base protocol (message shapes, `server/discover`, error codes, `_meta`, `resultType`) | https://modelcontextprotocol.io/specification/draft/basic |
| Architecture † | https://modelcontextprotocol.io/specification/draft/architecture |
| Transports | https://modelcontextprotocol.io/specification/draft/basic/transports |
| Multi Round-Trip Requests (MRTR) | https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr |
| Progress | https://modelcontextprotocol.io/specification/draft/basic/patterns/progress |
| Tools | https://modelcontextprotocol.io/specification/draft/server/tools |
| Resources | https://modelcontextprotocol.io/specification/draft/server/resources |
| Prompts | https://modelcontextprotocol.io/specification/draft/server/prompts |
| Completion | https://modelcontextprotocol.io/specification/draft/server/utilities/completion |
| Pagination | https://modelcontextprotocol.io/specification/draft/server/utilities/pagination |
| Caching (`ttlMs`, `cacheScope`) | https://modelcontextprotocol.io/specification/draft/server/utilities/caching |
| Subscriptions † | https://modelcontextprotocol.io/specification/draft/server/utilities/subscriptions |
| Elicitation | https://modelcontextprotocol.io/specification/draft/client/elicitation |
| Sampling (deprecated in 2026-07-28) | https://modelcontextprotocol.io/specification/draft/client/sampling |
| Authorization (incl. § Client Registration, § Authorization Server Discovery, § Security Considerations) | https://modelcontextprotocol.io/specification/draft/basic/authorization |
| Deprecated features registry | https://modelcontextprotocol.io/specification/draft/deprecated |
| Feature lifecycle policy | https://modelcontextprotocol.io/community/feature-lifecycle |
| Canonical schema (TypeScript) | https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/draft/schema.ts |

### Earlier revisions, for context only

| Revision | Changelog |
|---|---|
| 2025-03-26 | https://modelcontextprotocol.io/specification/2025-03-26/changelog |
| 2025-06-18 | https://modelcontextprotocol.io/specification/2025-06-18/changelog |
| 2025-11-25 | https://modelcontextprotocol.io/specification/2025-11-25/changelog |

### Announcements

- Model Context Protocol, *"The 2026-07-28 MCP Specification Release Candidate"* (2026).
  https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
- Model Context Protocol, *"Beta SDKs for the 2026-07-28 MCP Spec Release Candidate Are
  Here"* (2026). https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/
- Model Context Protocol, *"MCP joins the Agentic AI Foundation"* (2025).
  https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/
- Linux Foundation, *"Formation of the Agentic AI Foundation"* (2025).
  https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- Model Context Protocol, *"MCP Apps"* (2025), the preview announcement.
  https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/
- Model Context Protocol, *"MCP Apps"* (2026), the general-availability launch. This is the
  one post 24 cites.
  https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026).
  https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/

---

## 2. Specification Enhancement Proposals

The SEPs this series leans on. Each explains *why* a thing is the way it is, which is often
more useful than the normative text.

| SEP | Subject |
|---|---|
| SEP-2567 | *Sessionless MCP*: remove protocol sessions and `Mcp-Session-Id`, and replace cross-call state with server-minted handles |
| SEP-2575 | *Stateless MCP*: remove the `initialize` handshake and `ping`, add `server/discover`, drop stream resumability, and require the two transports not to diverge |
| SEP-2322 | Multi Round-Trip Requests (MRTR) |
| SEP-2577 | Deprecate roots, sampling, and logging |
| SEP-2663 | Move Tasks from core into an extension |
| SEP-2133 | The Extensions framework |
| SEP-2549 | `CacheableResult`, `ttlMs`, and `cacheScope` |
| SEP-2243 | `Mcp-Method` and `Mcp-Name` routing headers |
| SEP-2596 | Feature lifecycle and deprecation policy |
| SEP-414 | OpenTelemetry trace context in `_meta` |
| SEP-986 | Tool naming guidance |
| SEP-2106 | Arbitrary JSON Schema 2020-12 keywords in `inputSchema` |
| SEP-1303 | Input validation errors as tool execution errors |
| SEP-1613 | JSON Schema 2020-12 as the default dialect |
| SEP-1036 | URL-mode elicitation |
| SEP-1034 | Elicitation defaults |
| SEP-1865 | MCP Apps |
| SEP-991 | OAuth Client ID Metadata Documents (CIMD) |
| SEP-2468 | RFC 9207 issuer validation |
| SEP-1024 | Client security requirements for local servers |
| SEP-1766 | Digest-pinned tool versioning (opened 2025-11-05, closed 2026-06-24, no sponsor) |

Index: https://modelcontextprotocol.io/seps

---

## 3. Security

### Official guidance

- Model Context Protocol, *"Security Best Practices"*.
  https://modelcontextprotocol.io/specification/draft/basic/security_best_practices
- Model Context Protocol, *"Registry moderation policy"*.
  https://modelcontextprotocol.io/registry/moderation-policy
- Model Context Protocol, *"Security Interest Group"*.
  https://modelcontextprotocol.io/community/interest-groups/security

### Attack research

These attack classes are **not** covered by the normative specification. All are
third-party research, and all are live concerns for a server author.

- Invariant Labs, *"MCP Security Notification: Tool Poisoning Attacks"* (2025).
  https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks
- Invariant Labs, *"Toxic agent flows in the GitHub MCP server"* (2025).
  https://invariantlabs.ai/blog/mcp-github-vulnerability
- Trail of Bits, *"Jumping the line: how MCP servers can attack you before you ever use
  them"* (2025).
  https://blog.trailofbits.com/2025/04/21/jumping-the-line-how-mcp-servers-can-attack-you-before-you-ever-use-them/
- Trail of Bits, *"How MCP servers can steal your conversation history"* (2025).
  https://blog.trailofbits.com/2025/04/23/how-mcp-servers-can-steal-your-conversation-history/
- Trail of Bits, *"Deceiving users with ANSI terminal codes in MCP"* (2025).
  https://blog.trailofbits.com/2025/04/29/deceiving-users-with-ansi-terminal-codes-in-mcp/
- Willison, S. *"Supabase MCP can leak your entire SQL database"* (2025), the post that
  coined *"the lethal trifecta"*. Post 13 cites it by the coined phrase, post 19 by the
  title.
  https://simonwillison.net/2025/Jul/6/supabase-mcp-lethal-trifecta/
- Anthropic, *"How we contain Claude"* (2026).
  https://www.anthropic.com/engineering/how-we-contain-claude
- Anthropic, *"Code execution with MCP"*.
  https://www.anthropic.com/engineering/code-execution-with-mcp

### Vulnerabilities

| Identifier | Subject | Severity |
|---|---|---|
| CVE-2025-6514 | `mcp-remote` command injection | 9.6 Critical |
| CVE-2025-49596 | MCP Inspector unauthenticated RCE before 0.14.1 | 9.4 Critical |
| CVE-2025-53109 / CVE-2025-53110 | Filesystem server symlink and path traversal | 7.3 High |

https://nvd.nist.gov/vuln/detail/CVE-2025-6514 ·
https://nvd.nist.gov/vuln/detail/CVE-2025-49596 ·
https://nvd.nist.gov/vuln/detail/CVE-2025-53109 ·
https://nvd.nist.gov/vuln/detail/CVE-2025-53110

---

## 4. Authorization standards

- OAuth 2.1 (draft). https://oauth.net/2.1/
- RFC 9728, *OAuth 2.0 Protected Resource Metadata*. https://www.rfc-editor.org/rfc/rfc9728
- RFC 8707, *Resource Indicators for OAuth 2.0*. https://www.rfc-editor.org/rfc/rfc8707
- RFC 8414, *OAuth 2.0 Authorization Server Metadata*. https://www.rfc-editor.org/rfc/rfc8414
- RFC 9068, *JWT Profile for OAuth 2.0 Access Tokens*. https://www.rfc-editor.org/rfc/rfc9068
- RFC 9207, *OAuth 2.0 Authorization Server Issuer Identification*.
  https://www.rfc-editor.org/rfc/rfc9207
- RFC 7636, *Proof Key for Code Exchange (PKCE)*. https://www.rfc-editor.org/rfc/rfc7636
- RFC 7591, *OAuth 2.0 Dynamic Client Registration Protocol*. Deprecated for MCP in favor of
  CIMD. https://www.rfc-editor.org/rfc/rfc7591
- OpenID Connect Discovery 1.0.
  https://openid.net/specs/openid-connect-discovery-1_0.html

---

## 5. SDKs and tooling

- Python SDK. https://github.com/modelcontextprotocol/python-sdk ·
  https://pypi.org/project/mcp/
- TypeScript SDK. https://github.com/modelcontextprotocol/typescript-sdk
- SDK tiers and conformance commitments.
  https://modelcontextprotocol.io/community/sdk-tiers
- Conformance suite. https://github.com/modelcontextprotocol/conformance
- MCP Inspector. https://github.com/modelcontextprotocol/inspector
- Registry. https://github.com/modelcontextprotocol/registry ·
  https://registry.modelcontextprotocol.io/docs
- `server.json` schema, version `2025-12-11`.
  https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json
- MCPB bundles. https://github.com/modelcontextprotocol/mcpb — with Anthropic's
  *"MCP Bundles"* installation documentation for how a desktop host installs a `.mcpb`.
- OpenAI Apps SDK. https://developers.openai.com/apps-sdk
- Anthropic API documentation (model identifiers, `output_config.effort`).
  https://platform.claude.com/docs
- Extensions index. https://modelcontextprotocol.io/extensions/overview
- Tasks extension. https://github.com/modelcontextprotocol/ext-tasks
- Apps extension. https://github.com/modelcontextprotocol/ext-apps

---

## 6. Host configuration documentation

Each host documents its own configuration file, and they disagree with each other. Post 23
tabulates the differences; these are the sources.

| Host | Documentation |
|---|---|
| Claude Desktop | https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop |
| Claude Code | https://code.claude.com/docs/en/mcp |
| Cursor | https://cursor.com/docs/context/mcp |
| VS Code | https://code.visualstudio.com/docs/agents/reference/mcp-configuration |
| Gemini CLI | https://geminicli.com/docs/tools/mcp-server/ |
| Zed | https://zed.dev/docs/ai/mcp |
| Extensions client matrix | https://modelcontextprotocol.io/extensions/client-matrix |

---

## 7. Libraries used by the projects

- `psutil`. https://psutil.readthedocs.io/
- `asyncpg`. https://magicstack.github.io/asyncpg/current/
- `sqlglot`. https://github.com/tobymao/sqlglot
- PostgreSQL roles and privileges.
  https://www.postgresql.org/docs/current/user-manag.html
- Kubernetes Python client. https://github.com/kubernetes-client/python
- Kubernetes, *"Debug Running Pods"*.
  https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/
- Kubernetes, *"Rolling Back a Deployment"*.
  https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#rolling-back-a-deployment
- Kubernetes, *"Owners and Dependents"*.
  https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/
- Playwright for Python. https://playwright.dev/python/
- `trafilatura`. https://trafilatura.readthedocs.io/
- W3C, *Trace Context*. https://www.w3.org/TR/trace-context/
- W3C, *Baggage*. https://www.w3.org/TR/baggage/
- OpenTelemetry Python. https://opentelemetry.io/docs/languages/python/
- `pytest` and `pytest-asyncio`. https://docs.pytest.org/ ·
  https://pytest-asyncio.readthedocs.io/
- `uv`. https://docs.astral.sh/uv/

---

## 8. Background reading

- JSON-RPC 2.0 specification. https://www.jsonrpc.org/specification
- RFC 6570, *URI Template*. https://www.rfc-editor.org/rfc/rfc6570
- RFC 3986, *Uniform Resource Identifier (URI): Generic Syntax*.
  https://www.rfc-editor.org/rfc/rfc3986
- RFC 9110 section 5.1, *HTTP Semantics*. The field-name token syntax an `x-mcp-header`
  value must satisfy. https://datatracker.ietf.org/doc/html/rfc9110#section-5.1
- JSON Schema 2020-12. https://json-schema.org/draft/2020-12/release-notes
- RFC 5424, *The Syslog Protocol* (the log-level vocabulary behind `LoggingLevel`).
  https://www.rfc-editor.org/rfc/rfc5424
- Robertson, S. and Zaragoza, H., *"The Probabilistic Relevance Framework: BM25 and Beyond"*
  (2009). *Foundations and Trends in Information Retrieval* 3(4), 333–389. The `k1` and `b`
  parameters behind the knowledge-base project's ranking.
  https://www.staff.city.ac.uk/~sbrp622/papers/foundations_bm25_review.pdf
