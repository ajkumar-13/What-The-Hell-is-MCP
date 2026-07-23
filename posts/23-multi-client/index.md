# Blog 13: Multi-Client MCP
## One Server, Every Client

*Reading Time: 22 minutes*

---

> *"We've been building servers for Claude Desktop. But MCP's real power is interoperability—one server that works with Claude, Cursor, VS Code, and programmatic agents. Let's prove it."*

---

## Introduction

Throughout this series, we've built MCP servers and connected them to **Claude Desktop**. That's one client. But the entire promise of MCP—the "USB-C for AI" analogy—is that **one server works with every client**.

Think about USB-C: you don't build a different charger for each phone. You build one cable that works everywhere. MCP does the same for AI tools:

- **Build once** — a single server with your tools, resources, and prompts
- **Connect anywhere** — Claude Desktop, Cursor, VS Code, command-line agents, web apps
- **Same protocol** — JSON-RPC over stdio or Streamable HTTP

In this final blog, we'll build a **Team Knowledge Base Server** and connect it to four different clients, proving that interoperability isn't just theory—it works today.

---

## 1. The Knowledge Base Server

Our server exposes three tools that make team knowledge searchable from any AI client:

| Tool | Purpose | Annotation |
|------|---------|------------|
| `search_docs` | Full-text search across Markdown documentation | `readOnlyHint: True` |
| `get_snippet` | Retrieve named code snippets | `readOnlyHint: True` |
| `ask_faq` | Query frequently asked questions | `readOnlyHint: True` |

All read-only—safe to run from any client without side effects.

### Project Structure

```
team-knowledge/
├── src/
│   └── server.py
├── knowledge/
│   ├── docs/
│   │   ├── getting-started.md
│   │   ├── authentication.md
│   │   └── deployment.md
│   ├── snippets.json
│   └── faq.json
└── pyproject.toml
```

### The Server

```python
# src/server.py
"""Team Knowledge Base — works with ANY MCP client."""

import json
import logging
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Logging (never print to stdout) ─────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("team-knowledge")

# ── Knowledge paths ─────────────────────────────────────────────
KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
DOCS_DIR = KNOWLEDGE_DIR / "docs"
SNIPPETS_FILE = KNOWLEDGE_DIR / "snippets.json"
FAQ_FILE = KNOWLEDGE_DIR / "faq.json"

# ── Server ───────────────────────────────────────────────────────
mcp = FastMCP(
    "team-knowledge",
    instructions=(
        "Team Knowledge Base server. Use search_docs to find documentation, "
        "get_snippet for code examples, and ask_faq for common questions."
    ),
)


# ── Tools ────────────────────────────────────────────────────────
@mcp.tool(
    annotations={
        "title": "Search Documentation",
        "readOnlyHint": True,
        "openWorldHint": True,
    }
)
def search_docs(query: str, max_results: int = 5) -> str:
    """Search team documentation by keyword.

    Args:
        query: Search term (case-insensitive)
        max_results: Maximum number of results to return
    """
    if not DOCS_DIR.exists():
        return json.dumps({"error": "No docs directory found"})

    query_lower = query.lower()
    results = []

    for doc in sorted(DOCS_DIR.glob("*.md")):
        content = doc.read_text(encoding="utf-8")
        if query_lower in content.lower():
            # Extract a context snippet around the first match
            idx = content.lower().index(query_lower)
            start = max(0, idx - 100)
            end = min(len(content), idx + 200)
            snippet = content[start:end].strip()
            results.append({
                "file": doc.name,
                "preview": f"...{snippet}...",
            })
            if len(results) >= max_results:
                break

    return json.dumps({
        "query": query,
        "total_results": len(results),
        "results": results,
    }, indent=2)


@mcp.tool(
    annotations={
        "title": "Get Code Snippet",
        "readOnlyHint": True,
        "openWorldHint": False,
    }
)
def get_snippet(name: str) -> str:
    """Retrieve a named code snippet from the team snippet library.

    Args:
        name: Exact name of the snippet (e.g., 'auth-middleware', 'db-connect')
    """
    if not SNIPPETS_FILE.exists():
        return json.dumps({"error": "Snippets file not found"})

    snippets = json.loads(SNIPPETS_FILE.read_text(encoding="utf-8"))

    if name in snippets:
        entry = snippets[name]
        return json.dumps({
            "name": name,
            "language": entry.get("language", "python"),
            "description": entry.get("description", ""),
            "code": entry["code"],
        }, indent=2)

    # Fuzzy match: suggest similar names
    available = list(snippets.keys())
    suggestions = [s for s in available if name.lower() in s.lower()]

    return json.dumps({
        "error": f"Snippet '{name}' not found",
        "available_snippets": available,
        "suggestions": suggestions,
    }, indent=2)


@mcp.tool(
    annotations={
        "title": "Ask FAQ",
        "readOnlyHint": True,
        "openWorldHint": True,
    }
)
def ask_faq(question: str) -> str:
    """Query the team's frequently asked questions.

    Args:
        question: Natural language question (matched against FAQ entries)
    """
    if not FAQ_FILE.exists():
        return json.dumps({"error": "FAQ file not found"})

    faq_entries = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
    question_lower = question.lower()

    # Score each FAQ by keyword overlap
    scored = []
    for entry in faq_entries:
        q = entry["question"].lower()
        keywords = set(question_lower.split())
        matches = sum(1 for kw in keywords if kw in q)
        if matches > 0:
            scored.append((matches, entry))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        best = scored[0][1]
        return json.dumps({
            "matched_question": best["question"],
            "answer": best["answer"],
            "confidence": "high" if scored[0][0] >= 3 else "medium",
            "related": [
                s[1]["question"] for s in scored[1:4]
            ],
        }, indent=2)

    return json.dumps({
        "error": "No matching FAQ found",
        "suggestion": "Try different keywords or browse docs with search_docs",
    }, indent=2)


# ── Resources ────────────────────────────────────────────────────
@mcp.resource("knowledge://overview")
def knowledge_overview() -> str:
    """Overview of available knowledge base content."""
    doc_count = len(list(DOCS_DIR.glob("*.md"))) if DOCS_DIR.exists() else 0

    snippet_count = 0
    if SNIPPETS_FILE.exists():
        snippets = json.loads(SNIPPETS_FILE.read_text(encoding="utf-8"))
        snippet_count = len(snippets)

    faq_count = 0
    if FAQ_FILE.exists():
        faq = json.loads(FAQ_FILE.read_text(encoding="utf-8"))
        faq_count = len(faq)

    return json.dumps({
        "documentation_files": doc_count,
        "code_snippets": snippet_count,
        "faq_entries": faq_count,
        "tools": ["search_docs", "get_snippet", "ask_faq"],
    }, indent=2)


# ── Prompts ──────────────────────────────────────────────────────
@mcp.prompt()
def onboard_new_dev(team: str = "backend") -> str:
    """Generate an onboarding checklist for a new developer."""
    return f"""You are onboarding a new developer joining the {team} team.

Use the available tools to:
1. Search docs for "getting started" to find setup instructions
2. Get code snippets for common patterns (try 'db-connect', 'auth-middleware')
3. Check the FAQ for common new-developer questions

Compile a concise onboarding guide with:
- Environment setup steps
- Key code patterns to know
- Answers to frequently asked questions
"""


# ── Entry point ──────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### Sample Knowledge Data

Here's what populates the knowledge base:

**`knowledge/snippets.json`**
```json
{
  "db-connect": {
    "language": "python",
    "description": "Standard database connection pattern",
    "code": "import asyncpg\n\nasync def get_pool():\n    return await asyncpg.create_pool(\n        dsn=os.environ['DATABASE_URL'],\n        min_size=2,\n        max_size=10,\n    )"
  },
  "auth-middleware": {
    "language": "python",
    "description": "FastAPI authentication middleware",
    "code": "from fastapi import Header, HTTPException\n\nasync def verify_token(authorization: str = Header(...)):\n    if not authorization.startswith('Bearer '):\n        raise HTTPException(401, 'Invalid token format')\n    token = authorization[7:]\n    return await validate_jwt(token)"
  },
  "retry-decorator": {
    "language": "python",
    "description": "Exponential backoff retry decorator",
    "code": "import asyncio\nimport functools\n\ndef retry(max_attempts=3, base_delay=1.0):\n    def decorator(func):\n        @functools.wraps(func)\n        async def wrapper(*args, **kwargs):\n            for attempt in range(max_attempts):\n                try:\n                    return await func(*args, **kwargs)\n                except Exception as e:\n                    if attempt == max_attempts - 1:\n                        raise\n                    delay = base_delay * (2 ** attempt)\n                    await asyncio.sleep(delay)\n        return wrapper\n    return decorator"
  }
}
```

**`knowledge/faq.json`**
```json
[
  {
    "question": "How do I set up my local development environment?",
    "answer": "1. Install Python 3.11+  2. Clone the repo  3. Run 'uv sync' to install dependencies  4. Copy .env.example to .env  5. Run 'uv run python -m pytest' to verify"
  },
  {
    "question": "How do I connect to the staging database?",
    "answer": "Use the DATABASE_URL from 1Password vault 'Engineering'. Never hardcode credentials. Use 'uv run python scripts/test_db.py' to verify connectivity."
  },
  {
    "question": "What is the deployment process?",
    "answer": "1. Create a PR with your changes  2. CI runs tests and linting  3. Get one approval  4. Merge to main  5. Auto-deploys to staging  6. Manual promotion to production via the release workflow"
  },
  {
    "question": "How do I add a new API endpoint?",
    "answer": "1. Add the route in src/routes/  2. Add the schema in src/schemas/  3. Write tests in tests/  4. Update the OpenAPI docs  5. Add to CHANGELOG.md"
  }
]
```

This server is simple, stateless, read-only—and it works with **every MCP client**.

---

## 2. Client Configurations

Here's the key insight: **every MCP client speaks the same protocol**. They differ only in:
- Where the configuration file lives
- The JSON format of that configuration
- Which MCP features they support

The server code doesn't change. Not one line.

### Transport Modes

Before diving into configs, remember the two transports from Blog 12:

| Transport | When to Use | Config Key |
|-----------|-------------|------------|
| **stdio** | Server runs locally, launched by the client | `"command"` + `"args"` |
| **Streamable HTTP** | Server already running remotely | `"url"` |

Every client below supports both.

---

### 2.1 Claude Desktop

**What it is:** Anthropic's desktop application—the original MCP client.

**Config file location:**
```
Windows:  %APPDATA%\Claude\claude_desktop_config.json
macOS:    ~/Library/Application Support/Claude/claude_desktop_config.json
```

**Local (stdio):**
```json
{
  "mcpServers": {
    "team-knowledge": {
      "command": "uv",
      "args": [
        "run", "--directory", "C:\\projects\\team-knowledge",
        "python", "src/server.py"
      ]
    }
  }
}
```

**Remote (Streamable HTTP):**
```json
{
  "mcpServers": {
    "team-knowledge": {
      "url": "https://knowledge.fly.dev/mcp/"
    }
  }
}
```

**Supported MCP features:** Resources, Prompts, Tools, Sampling

Claude Desktop is what we've used throughout this series. It launches the server as a subprocess (stdio) or connects to a URL (Streamable HTTP). Tools appear directly in the chat interface.

---

### 2.2 Cursor IDE

**What it is:** An AI-powered code editor built on VS Code, with deep MCP integration in its Composer agent.

**Config file location:**
```
Project-scoped:  .cursor/mcp.json     (in your project root)
Global:          ~/.cursor/mcp.json
```

**Local (stdio):**
```json
{
  "mcpServers": {
    "team-knowledge": {
      "command": "uv",
      "args": [
        "run", "--directory", "/path/to/team-knowledge",
        "python", "src/server.py"
      ]
    }
  }
}
```

**Remote (Streamable HTTP):**
```json
{
  "mcpServers": {
    "team-knowledge": {
      "url": "https://knowledge.fly.dev/mcp/"
    }
  }
}
```

**Supported MCP features:** Prompts, Tools, Roots, Elicitation

Notice the format is almost identical to Claude Desktop. The only difference is:
- Claude Desktop: one global config file
- Cursor: project-scoped (`.cursor/mcp.json`) or global (`~/.cursor/mcp.json`)

Project-scoped configs are powerful for teams—commit `.cursor/mcp.json` to your repo and everyone automatically gets the same tools.

---

### 2.3 VS Code (GitHub Copilot)

**What it is:** Visual Studio Code with GitHub Copilot's agent mode—VS Code now has **native MCP support** without any additional extensions.

**Config file location:**
```
Workspace:  .vscode/mcp.json     (in your project root — commit to source control)
User profile:  mcp.json in your VS Code user profile (open via MCP: Open User Configuration)
```

**Local (stdio) — `.vscode/mcp.json`:**
```json
{
  "servers": {
    "team-knowledge": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run", "--directory", "${workspaceFolder}/team-knowledge",
        "python", "src/server.py"
      ]
    }
  }
}
```

**Remote (Streamable HTTP) — `.vscode/mcp.json`:**
```json
{
  "servers": {
    "team-knowledge": {
      "type": "http",
      "url": "https://knowledge.fly.dev/mcp/"
    }
  }
}
```

> **Compatibility note:** Current VS Code builds use `"type": "http"` for remote MCP servers. VS Code first tries Streamable HTTP and can fall back to legacy SSE for older servers, but new deployments should target Streamable HTTP endpoints.

**With input variables (for API keys):**
```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "kb-api-key",
      "description": "Knowledge Base API Key",
      "password": true
    }
  ],
  "servers": {
    "team-knowledge": {
      "type": "http",
      "url": "https://knowledge.fly.dev/mcp/",
      "headers": {
        "Authorization": "Bearer ${input:kb-api-key}"
      }
    }
  }
}
```

**Documented MCP capabilities:** Tools plus resources, prompts, and MCP Apps. Other negotiated capabilities, such as sampling, depend on the connected server and your current VS Code version/settings.

VS Code's native MCP support is broad and actively evolving. Key differences from Cursor:
- Config uses `"servers"` (not `"mcpServers"`)
- Each server needs an explicit `"type"` field (`"stdio"` or `"http"`)
- Supports `"inputs"` for secure credential prompts
- Supports `${workspaceFolder}` and other VS Code variables
- MCP tools appear in Copilot's agent mode chat

> **Note:** You may see older references to the **Continue extension** for MCP in VS Code. While Continue still works, VS Code's native MCP support (via GitHub Copilot) is now the recommended approach. No extra extension needed.

---

### 2.4 Programmatic Clients (Python SDK)

**What it is:** Direct MCP client connections from Python code—for scripts, automation, CI/CD pipelines, and custom applications.

```python
# programmatic_client.py
"""Connect to any MCP server programmatically."""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    # ── Connect via stdio ──────────────────────────────────────
    server_params = StdioServerParameters(
        command="uv",
        args=[
            "run", "--directory", "/path/to/team-knowledge",
            "python", "src/server.py",
        ],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()

            # Discover available tools
            tools_result = await session.list_tools()
            print(f"Available tools: {[t.name for t in tools_result.tools]}")

            # Call a tool
            result = await session.call_tool(
                "search_docs",
                arguments={"query": "authentication"},
            )
            print(f"Search results:\n{result.content[0].text}")

            # Use a prompt
            prompt_result = await session.get_prompt(
                "onboard_new_dev",
                arguments={"team": "backend"},
            )
            print(f"Onboarding prompt:\n{prompt_result.messages[0].content.text}")


if __name__ == "__main__":
    asyncio.run(main())
```

**For remote servers (Streamable HTTP):**

```python
import httpx
from mcp.client.streamable_http import streamable_http_client

async def connect_remote():
    async with httpx.AsyncClient() as http_client:
        async with streamable_http_client(
            "https://knowledge.fly.dev/mcp/",
            http_client=http_client,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                print(f"Remote tools: {[t.name for t in tools.tools]}")
```

> **SDK note:** Current Python SDK releases use `streamable_http_client()`; the older `streamablehttp_client()` helper was removed.

Programmatic clients are ideal for:
- **CI/CD pipelines** — automated documentation checking
- **Chatbots** — Slack/Discord bots with MCP tools
- **Batch jobs** — process documents against your knowledge base
- **Testing** — automated integration tests for your MCP server

---

## 3. Remote Access with Streamable HTTP

For all clients to share **one deployed server**, add Streamable HTTP transport. As covered in Blog 12:

```python
# src/server_remote.py
"""Team Knowledge Base — remote access via Streamable HTTP."""

import json
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("team-knowledge")

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
DOCS_DIR = KNOWLEDGE_DIR / "docs"
SNIPPETS_FILE = KNOWLEDGE_DIR / "snippets.json"
FAQ_FILE = KNOWLEDGE_DIR / "faq.json"

mcp = FastMCP(
    "team-knowledge",
    instructions=(
        "Team Knowledge Base server. Use search_docs to find documentation, "
        "get_snippet for code examples, and ask_faq for common questions."
    ),
)

# ... same tools as above (search_docs, get_snippet, ask_faq) ...
# ... same resources and prompts ...

# ── Entry point ──────────────────────────────────────────────────
if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")

    if transport == "streamable-http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
    else:
        mcp.run(transport="stdio")
```

Now one server binary handles both:
- `python src/server_remote.py` → stdio (for local clients)
- `MCP_TRANSPORT=streamable-http python src/server_remote.py` → HTTP on port 8000

### Deploy to the Cloud

**Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir mcp[cli]

ENV MCP_TRANSPORT=streamable-http
EXPOSE 8000

CMD ["python", "src/server_remote.py"]
```

**Deploy (fly.io example):**
```bash
fly launch --name team-knowledge --internal-port 8000
fly deploy
```

Once deployed, every client connects with the same URL:
```
https://team-knowledge.fly.dev/mcp/
```

No bridge tools. No adapters. Just a URL.

---

## 4. Comparison: Same Server, Different Experiences

| | Claude Desktop | Cursor | VS Code | Programmatic |
|---|---|---|---|---|
| **Best for** | Research, Q&A | In-editor coding | Agent-mode dev | Automation, CI/CD |
| **Config file** | `claude_desktop_config.json` | `.cursor/mcp.json` | `.vscode/mcp.json` | Python code |
| **Server key** | `"mcpServers"` | `"mcpServers"` | `"servers"` | `StdioServerParameters` |
| **Type field** | Implicit | Implicit | Required (`"stdio"` / `"http"`) | Code-level |
| **Remote** | `"url"` | `"url"` | `"url"` + `"type": "http"` | `streamable_http_client()` |
| **Credentials** | Environment vars | Environment vars | `"inputs"` with secure prompt | Code / env vars |
| **Team sharing** | Manual copy | Commit `.cursor/mcp.json` | Commit `.vscode/mcp.json` | Commit script |

Key observation: **Claude Desktop and Cursor use nearly identical formats.** VS Code differs slightly (explicit types, input variables). Programmatic clients replace config with code.

---

## 5. Config Quick Reference

### Config File Locations

| Client | Path |
|--------|------|
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor (project) | `.cursor/mcp.json` |
| Cursor (global) | `~/.cursor/mcp.json` |
| VS Code (workspace) | `.vscode/mcp.json` |
| VS Code (user) | User profile `mcp.json` via `MCP: Open User Configuration` |

### Minimal Config Templates

**Claude Desktop / Cursor (local):**
```json
{
  "mcpServers": {
    "SERVER_NAME": {
      "command": "uv",
      "args": ["run", "--directory", "PATH", "python", "src/server.py"]
    }
  }
}
```

**Claude Desktop / Cursor (remote):**
```json
{
  "mcpServers": {
    "SERVER_NAME": {
      "url": "https://YOUR_SERVER/mcp/"
    }
  }
}
```

**VS Code (local):**
```json
{
  "servers": {
    "SERVER_NAME": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "${workspaceFolder}", "python", "src/server.py"]
    }
  }
}
```

**VS Code (remote):**
```json
{
  "servers": {
    "SERVER_NAME": {
      "type": "http",
      "url": "https://YOUR_SERVER/mcp/"
    }
  }
}
```

---

## 6. Tips for Multi-Client Servers

### Tool Design

Tools that work well across clients share these traits:

1. **Return JSON strings** — every client can parse structured data
2. **Use descriptive names** — clients use tool names and descriptions to decide when to invoke them
3. **Include annotations** — `readOnlyHint`, `destructiveHint` etc. help clients apply appropriate guardrails
4. **Validate inputs** — don't assume the client validated for you

### Server Instructions

The `instructions` parameter in `FastMCP()` is sent to the client on initialization. Write them like a README:

```python
mcp = FastMCP(
    "team-knowledge",
    instructions=(
        "Team Knowledge Base server.\n"
        "- search_docs: Full-text search across team Markdown docs\n"
        "- get_snippet: Retrieve named code snippets (exact name match)\n"
        "- ask_faq: Keyword-matched FAQ lookup\n"
        "Start with search_docs for broad queries, get_snippet for specific code."
    ),
)
```

### Environment Variable Patterns

Make servers configurable without code changes:

```python
import os

KNOWLEDGE_DIR = Path(os.environ.get(
    "KNOWLEDGE_DIR",
    str(Path(__file__).parent.parent / "knowledge"),
))
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
```

This lets different clients point the same server at different data:

```json
{
  "mcpServers": {
    "team-knowledge": {
      "command": "uv",
      "args": ["run", "python", "src/server.py"],
      "env": {
        "KNOWLEDGE_DIR": "/path/to/my-team/knowledge"
      }
    }
  }
}
```

---

## 7. The Interoperability Promise

Here's what we just proved:

```
┌──────────────────────────────────────────────────────────┐
│                                                          │
│    ┌─────────────┐                                       │
│    │   Claude     │──┐                                   │
│    │   Desktop    │  │                                   │
│    └─────────────┘  │                                   │
│                      │     ┌──────────────────────┐      │
│    ┌─────────────┐  ├────▶│                      │      │
│    │   Cursor     │──┤     │   Team Knowledge     │      │
│    │   IDE        │  │     │   Base Server        │      │
│    └─────────────┘  │     │                      │      │
│                      │     │   search_docs()      │      │
│    ┌─────────────┐  │     │   get_snippet()       │      │
│    │   VS Code    │──┤     │   ask_faq()           │      │
│    │   + Copilot  │  │     │                      │      │
│    └─────────────┘  │     └──────────────────────┘      │
│                      │          ▲                         │
│    ┌─────────────┐  │          │                         │
│    │   Python     │──┘          │                         │
│    │   Script     │     (stdio or Streamable HTTP)       │
│    └─────────────┘                                       │
│                                                          │
│           The MCP Interoperability Model                 │
└──────────────────────────────────────────────────────────┘
```

**One server. Four clients. Zero changes.**

The server doesn't know or care which client is connected. It speaks MCP. Every client speaks MCP. That's the entire point.

---

## Key Takeaways

1. **Build once, connect anywhere** — one MCP server works with Claude Desktop, Cursor, VS Code, and programmatic clients without changing a single line of server code
2. **Config differences are cosmetic** — Claude Desktop and Cursor use `"mcpServers"`, VS Code uses `"servers"` with explicit type fields, but the underlying protocol is identical
3. **Two transports cover every scenario** — stdio for local dev (client launches server), Streamable HTTP for remote/shared deployment (server already running)
4. **Team sharing is a config file** — commit `.cursor/mcp.json` or `.vscode/mcp.json` to your repo and every teammate gets the same tools automatically
5. **Tools should return JSON strings** — structured output works across all clients; use annotations (`readOnlyHint`, `destructiveHint`) so clients apply appropriate guardrails
6. **Server instructions matter** — the `instructions` parameter acts as a README for the LLM, helping every client use your tools effectively

---

## Series Wrap-Up

### What You've Learned

**Foundation (Blogs 1–4)**
- What MCP is, and why it matters for AI tool integration
- Architecture: Host → Client → Server
- The three server primitives: Tools, Resources, Prompts, plus where sampling fits as a client capability
- Transports: stdio and Streamable HTTP
- Building both servers (Blog 3) and host applications (Blog 4)

**Real-World Projects (Blogs 5–11)**
- **Database Analyst (5–6):** SQL validation, security layers, human-in-the-loop writes, audit logging
- **DevOps First Responder (7–8):** Kubernetes diagnostics, safe remediation with tool annotations, RBAC
- **Deep Research Browser (9–11):** Headless browsing, server-side summarization via Sampling, multi-page research sessions

**Production & Interoperability (Blogs 12–13)**
- Docker containerization and cloud deployment
- Streamable HTTP for remote access
- Authentication and security
- Multi-client configuration: Claude Desktop, Cursor, VS Code, programmatic

### The Bigger Picture

MCP is still young. The specification evolves (the 2025-11-25 revision is the latest as of this writing), new clients appear every week, and the ecosystem is growing fast. But the core idea is stable:

> **Servers expose capabilities. Clients consume them. The protocol handles everything in between.**

That's it. Whether you're building a knowledge base for your team, a DevOps agent for your infrastructure, or a research tool for your domain—the pattern is the same:

1. **Define tools** with clear descriptions and annotations
2. **Return structured data** (JSON strings)
3. **Log to stderr**, never stdout
4. **Test locally** with stdio, then **deploy remotely** with Streamable HTTP
5. **Let any client connect** — your server doesn't need to change

### What's Next

- **Explore community servers:** [github.com/modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)
- **Take the HuggingFace MCP course:** [huggingface.co/learn/mcp-course](https://huggingface.co/learn/mcp-course)
- **Read the specification:** [modelcontextprotocol.io/specification/2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25)
- **Build your own:** Apply these patterns to your domain
- **Contribute:** Open-source your servers for the community

---

## Resources

- [MCP Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Community Servers](https://github.com/modelcontextprotocol/servers)
- [MCP Clients Directory](https://modelcontextprotocol.io/clients)
- [Claude Desktop](https://claude.ai/download)
- [Cursor IDE](https://cursor.sh)
- [VS Code MCP Docs](https://code.visualstudio.com/docs/copilot/chat/mcp-servers)
- [Continue Extension](https://continue.dev)
- [Gradio MCP](https://www.gradio.app/docs/mcp)
- [HuggingFace MCP Course](https://huggingface.co/learn/mcp-course)

---

| [← Blog 12: Production Deployment](../blog-12/blog.md) | [Back to Series Overview →](../README.md) |
|:---|---:|
