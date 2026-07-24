# References

Every citation used anywhere in this series, grouped by kind. Posts cite by title and year
and link here for the full entry. If a post makes a claim about the protocol, the
supporting page is in section 1.

Everything below was checked on **2026-07-26**. Documentation URLs in this ecosystem move
often; where a page has already moved once, the old location is noted.

---

## 1. The specification

The series targets revision **2026-07-28**. While that revision was a release candidate it
lived at `/specification/draft/`; after it goes final the same pages are served from
`/specification/2026-07-28/`.

| Topic | URL |
|---|---|
| Versioning and revision index | https://modelcontextprotocol.io/specification/versioning |
| 2026-07-28 changelog | https://modelcontextprotocol.io/specification/draft/changelog |
| Transports | https://modelcontextprotocol.io/specification/draft/basic/transports |
| Tools | https://modelcontextprotocol.io/specification/draft/server/tools |
| Resources | https://modelcontextprotocol.io/specification/draft/server/resources |
| Prompts | https://modelcontextprotocol.io/specification/draft/server/prompts |
| Completion | https://modelcontextprotocol.io/specification/draft/server/utilities/completion |
| Pagination | https://modelcontextprotocol.io/specification/draft/server/utilities/pagination |
| Elicitation | https://modelcontextprotocol.io/specification/draft/client/elicitation |
| Authorization | https://modelcontextprotocol.io/specification/draft/basic/authorization |
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
- Model Context Protocol, *"MCP Apps"* (2025).
  https://blog.modelcontextprotocol.io/posts/2025-11-21-mcp-apps/
- Model Context Protocol, *"Tool annotations are not enforcement"* (2026).
  https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/

---

## 2. Specification Enhancement Proposals

The SEPs this series leans on. Each explains *why* a thing is the way it is, which is often
more useful than the normative text.

| SEP | Subject |
|---|---|
| SEP-2567 | Make MCP stateless: remove protocol sessions and `Mcp-Session-Id` |
| SEP-2575 | Remove the `initialize` handshake |
| SEP-2322 | Multi Round-Trip Requests (MRTR) |
| SEP-2663 | Move Tasks from core into an extension |
| SEP-2133 | The Extensions framework |
| SEP-2549 | `CacheableResult`, `ttlMs`, and `cacheScope` |
| SEP-2243 | `Mcp-Method` and `Mcp-Name` routing headers |
| SEP-2596 | Feature lifecycle and deprecation policy |
| SEP-414 | OpenTelemetry trace context in `_meta` |
| SEP-986 | Tool naming guidance |
| SEP-1613 | JSON Schema 2020-12 as the default dialect |
| SEP-1036 | URL-mode elicitation |
| SEP-1034 | Elicitation defaults |
| SEP-1865 | MCP Apps |
| SEP-991 | OAuth Client ID Metadata Documents (CIMD) |
| SEP-2468 | RFC 9207 issuer validation |
| SEP-1024 | Client security requirements for local servers |

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
- Willison, S. *"The lethal trifecta"* (2025).
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
https://nvd.nist.gov/vuln/detail/CVE-2025-49596

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
- MCPB bundles. https://github.com/modelcontextprotocol/mcpb
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
| OpenAI Apps SDK | https://developers.openai.com/apps-sdk |
| Extensions client matrix | https://modelcontextprotocol.io/extensions/client-matrix |

---

## 7. Libraries used by the projects

- `asyncpg`. https://magicstack.github.io/asyncpg/current/
- `sqlglot`. https://github.com/tobymao/sqlglot
- PostgreSQL roles and privileges.
  https://www.postgresql.org/docs/current/user-manag.html
- Kubernetes Python client. https://github.com/kubernetes-client/python
- Playwright for Python. https://playwright.dev/python/
- `trafilatura`. https://trafilatura.readthedocs.io/
- OpenTelemetry Python. https://opentelemetry.io/docs/languages/python/
- `uv`. https://docs.astral.sh/uv/

---

## 8. Background reading

- JSON-RPC 2.0 specification. https://www.jsonrpc.org/specification
- RFC 6570, *URI Template*. https://www.rfc-editor.org/rfc/rfc6570
- JSON Schema 2020-12. https://json-schema.org/draft/2020-12/release-notes
- RFC 5424, *The Syslog Protocol* (the log-level vocabulary).
  https://www.rfc-editor.org/rfc/rfc5424
