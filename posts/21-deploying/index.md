# Blog 12: Production Deployment
## Docker, Streamable HTTP & Authentication

*Reading Time: 20 minutes*

---

> *"Your MCP servers run great locally. But what about accessing them from anywhere? Time to go to production."*

---

## Introduction

Throughout this series, all our MCP servers have used one transport: **stdio** (standard input/output). It's simple, secure, and perfect for local development—the host launches the server as a subprocess, and they communicate over pipes.

But stdio has a limitation: the client and server must run on the **same machine**.

For production, we need:
- Servers accessible from anywhere over the network
- Authentication to control access
- Docker containers for portability
- Cloud hosting for availability

This blog covers all of that using **Streamable HTTP**—the recommended transport for remote MCP servers since the 2025-03-26 specification update.

---

## 1. Transports: stdio vs. Streamable HTTP

| Feature | stdio | Streamable HTTP |
|---------|-------|-----------------|
| Communication | Process pipes | HTTP + Server-Sent Events |
| Network | Same machine only | Any network |
| Setup | Host launches server | Server runs independently |
| Security | Process isolation | TLS + auth needed |
| Use case | Local dev, Claude Desktop | Remote, shared, cloud |

### Why Streamable HTTP (Not SSE)?

The MCP specification **deprecated** the old HTTP+SSE transport on 2025-03-26 and replaced it with **Streamable HTTP**. Key differences:

- **Single endpoint** for both requests and streaming (POST + optional GET)
- **Session management** via `Mcp-Session-Id` header
- **Resumability** with SSE event IDs
- **Backwards compatible** — clients expecting the old SSE transport still work

> If you see code examples using `SseServerTransport` or `/sse` endpoints, those are outdated. Use `streamable_http_app()` instead.

---

## 2. Streamable HTTP in the Python SDK

The MCP Python SDK makes Streamable HTTP straightforward:

### Option 1: Direct Run

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Server")

@mcp.tool()
async def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    # Run with Streamable HTTP transport on port 8080
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
```

### Option 2: ASGI App (for production with uvicorn)

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Server")

@mcp.tool()
async def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

# Export as an ASGI app for uvicorn/gunicorn
app = mcp.streamable_http_app()
```

Run with:
```bash
uvicorn server:app --host 0.0.0.0 --port 8080
```

The endpoint will be at `http://localhost:8080/mcp/` by default.

---

## 3. Adding Authentication

A remote MCP server needs authentication. Here's how to add API key middleware:

```python
# server.py — Production MCP Server with Auth
import logging
import os
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load API key from environment
API_KEY = os.environ.get("MCP_API_KEY")


# ============ AUTH MIDDLEWARE ============

class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate API key in X-API-Key header."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health check
        if request.url.path == "/health":
            return await call_next(request)

        # Skip auth if no API key is configured (dev mode)
        if not API_KEY:
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key")
        if provided_key != API_KEY:
            logger.warning(
                "Unauthorized request from %s", request.client.host
            )
            return JSONResponse(
                {"error": "Invalid or missing API key"},
                status_code=401,
            )

        return await call_next(request)


# ============ MCP SERVER ============

mcp = FastMCP("Production Server")


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"


# Add your actual tools here...


# ============ ASGI APP ============

def create_app() -> Starlette:
    """Create the ASGI app with auth middleware and health check."""
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def health_check(request: Request):
        return PlainTextResponse("OK")

    # Get the MCP ASGI app
    mcp_app = mcp.streamable_http_app()

    # Combine into a larger Starlette app with middleware
    app = Starlette(
        routes=[
            Route("/health", health_check),
            Mount("/", app=mcp_app),
        ],
        middleware=[
            Middleware(APIKeyMiddleware),
        ],
    )

    return app


app = create_app()
```

Run:
```bash
MCP_API_KEY="your-secret-key" uvicorn server:app --host 0.0.0.0 --port 8080
```

---

## 4. Docker Containerization

### Dockerfile

```dockerfile
FROM python:3.12-slim

# Don't write .pyc files, unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN groupadd -r mcp && useradd -r -g mcp -s /sbin/nologin mcp

WORKDIR /app

# Install uv for dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
RUN uv sync --no-dev --frozen

# Switch to non-root user
USER mcp

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

# Run with Streamable HTTP transport
CMD ["uv", "run", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8080"]
```

### .dockerignore

```
__pycache__/
*.pyc
.git/
.env
.venv/
*.md
tests/
```

### Build & Run

```bash
# Build
docker build -t mcp-server .

# Run (with API key)
docker run -p 8080:8080 -e MCP_API_KEY="your-secret-key" mcp-server

# Test health
curl http://localhost:8080/health
# → OK
```

---

## 5. Connecting Clients to Remote Servers

### Claude Desktop (Streamable HTTP)

```json
{
  "mcpServers": {
    "remote-server": {
      "url": "https://your-server.fly.dev/mcp/",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

> **Note:** When specifying a `url`, Claude Desktop connects directly over HTTP instead of launching a subprocess. No `command` or `args` needed.

### Cursor IDE

```json
{
  "mcpServers": {
    "remote-server": {
      "url": "https://your-server.fly.dev/mcp/",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

### Programmatic Python Client

```python
import asyncio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
  async with httpx.AsyncClient(
    headers={"X-API-Key": "your-api-key"},
  ) as http_client:
    async with streamable_http_client(
      "https://your-server.fly.dev/mcp/",
      http_client=http_client,
    ) as (read_stream, write_stream):
      async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()

        # List tools
        tools = await session.list_tools()
        print(f"Available: {[t.name for t in tools.tools]}")

        # Call a tool
        result = await session.call_tool("hello", {"name": "World"})
        print(result.content[0].text)

asyncio.run(main())
```

> **SDK note:** Current Python SDK releases use `streamable_http_client()` and expect headers/auth to be configured on an `httpx.AsyncClient`. The older `streamablehttp_client()` helper has been removed.

---

## 6. Cloud Deployment

### Option 1: fly.io

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login and launch
fly auth login
fly launch --name my-mcp-server
```

**fly.toml:**
```toml
app = "my-mcp-server"
primary_region = "iad"

[build]

[http_service]
  internal_port = 8080
  force_https = true

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"
```

```bash
# Set secrets
fly secrets set MCP_API_KEY="your-secret-key"

# Deploy
fly deploy
```

Your server will be at: `https://my-mcp-server.fly.dev/mcp/`

### Option 2: Railway

1. Push your project to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Set environment variables: `MCP_API_KEY`
4. Railway auto-detects the Dockerfile and deploys

### Option 3: Any Docker Host

```bash
# Push to Docker Hub
docker tag mcp-server your-user/mcp-server:latest
docker push your-user/mcp-server:latest

# Run on any server with Docker
docker run -d -p 8080:8080 \
  -e MCP_API_KEY="your-key" \
  --restart unless-stopped \
  your-user/mcp-server:latest
```

---

## 7. Quick Alternative: Gradio MCP Servers

For quick prototypes or sharing tools publicly, **Gradio** can auto-wrap functions as MCP tools:

```python
import gradio as gr

def analyze_text(text: str) -> str:
    """Analyze text and return word count and character stats."""
    words = text.split()
    return (
        f"Words: {len(words)}\n"
        f"Characters: {len(text)}\n"
        f"Sentences: {text.count('.') + text.count('!') + text.count('?')}"
    )

demo = gr.Interface(
    fn=analyze_text,
    inputs="text",
    outputs="text",
)
demo.launch(mcp_server=True)  # MCP endpoint enabled automatically
```

- No MCP decorators needed—Gradio converts your function to an MCP tool
- Deploy free on [Hugging Face Spaces](https://huggingface.co/spaces)
- MCP endpoint available at `/gradio_api/mcp/sse`
- Great for demos, sharing, and quick iteration

> Gradio MCP servers use the SSE transport for compatibility. For production MCP servers, use the Streamable HTTP approach shown above.

---

## 8. Monitoring & Logging

### Structured JSON Logging

```python
import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Format logs as JSON for cloud log ingestion."""

    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


# Use in production
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(JSONFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)
```

### Request Logging Middleware

```python
import time
from starlette.middleware.base import BaseHTTPMiddleware

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration = time.monotonic() - start

        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": str(request.url.path),
                "status": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "client": request.client.host if request.client else "unknown",
            },
        )
        return response
```

---

## 9. Security Checklist

Before deploying any MCP server to production:

| # | Check | Why |
|---|-------|-----|
| 1 | **API key or OAuth authentication** | Prevent unauthorized access |
| 2 | **HTTPS only (TLS)** | Encrypt data in transit |
| 3 | **Non-root container user** | Limit container escape impact |
| 4 | **Input validation in every tool** | Prevent injection attacks |
| 5 | **Rate limiting** | Prevent abuse and cost overrun |
| 6 | **Secrets in env vars, not code** | Never commit API keys |
| 7 | **Minimal Docker image** | Reduce attack surface |
| 8 | **Audit logging** | Track who did what |
| 9 | **Health check endpoint** | Monitor availability |
| 10 | **Origin header validation** | Prevent cross-origin attacks (per MCP spec) |

---

## 10. Putting It All Together

Here's our production-ready Database Analyst from Blogs 5-6, adapted for remote deployment:

### pyproject.toml

```toml
[project]
name = "mcp-db-analyst-prod"
version = "1.0.0"
description = "Production MCP Database Analyst"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.9",
    "asyncpg>=0.30",
    "starlette>=0.41",
    "uvicorn>=0.32",
]

[project.scripts]
mcp-db-analyst = "src.server:main"
```

### Claude Desktop Config (Remote)

```json
{
  "mcpServers": {
    "db-analyst": {
      "url": "https://db-analyst.fly.dev/mcp/",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

### Docker Compose (with PostgreSQL)

```yaml
version: "3.8"

services:
  mcp-server:
    build: .
    ports:
      - "8080:8080"
    environment:
      - MCP_API_KEY=${MCP_API_KEY}
      - DATABASE_URL=postgresql://analyst:password@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: analyst
      POSTGRES_PASSWORD: password
      POSTGRES_DB: mydb
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U analyst"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
```

```bash
MCP_API_KEY="your-key" docker compose up -d
```

---

## Key Takeaways

```
✅ Streamable HTTP replaces SSE for remote MCP servers
✅ mcp.streamable_http_app() for ASGI integration with uvicorn
✅ API key middleware for authentication
✅ Docker with non-root user for secure containers
✅ Cloud deployment on fly.io, Railway, or any Docker host
✅ Gradio for quick MCP prototypes (free hosting on HF Spaces)
✅ JSON structured logging for cloud log aggregation
✅ Health check endpoints for monitoring
```

---

## What's Next?

Our servers are now production-ready. But we've been using only **one client**: Claude Desktop.

MCP's real promise is interoperability—one server, every client. In **Blog 13: Multi-Client MCP**, we'll build a Team Knowledge Base Server and connect it to four different clients: Claude Desktop, Cursor IDE, VS Code (GitHub Copilot), and programmatic Python clients.

Same server. Zero code changes. That's the "USB-C for AI" promise delivered.

---

## Quick Reference

### Streamable HTTP Setup
```python
# Minimal remote server
app = mcp.streamable_http_app()
# Run with: uvicorn server:app --host 0.0.0.0 --port 8080
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir mcp[cli]
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Cloud Deploy
```bash
# fly.io
fly launch --name my-mcp-server --internal-port 8080
fly secrets set MCP_API_KEY="your-key"
fly deploy
```

### Client Config (Remote)
```json
{
  "mcpServers": {
    "my-server": {
      "url": "https://my-server.fly.dev/mcp/"
    }
  }
}
```

---

## Resources

- [MCP Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Community Servers](https://github.com/modelcontextprotocol/servers)
- [Claude Desktop](https://claude.ai/download)
- [Cursor IDE](https://cursor.sh)
- [HuggingFace MCP Course](https://huggingface.co/learn/mcp-course)

---

| [← Blog 11: Deep Research Browser Part 3](../blog-11/blog.md) | [Blog 13: Multi-Client MCP →](../blog-13/blog.md) |
|:---|---:|
