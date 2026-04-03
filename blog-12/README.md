# Blog 12: Production Deployment

Take MCP servers from local development to production with Docker, Streamable HTTP transport, and authentication.

## What This Blog Covers

- **Streamable HTTP** transport (replaces deprecated HTTP+SSE)
- API key authentication middleware
- Docker containerization with non-root user
- Cloud deployment (fly.io, Railway)
- Gradio as a quick alternative for MCP servers
- Monitoring, structured logging, and health checks
- Security checklist for production

## Key Commands

```bash
# Run locally with Streamable HTTP
uvicorn server:app --host 0.0.0.0 --port 8080

# Docker build and run
docker build -t mcp-server .
docker run -p 8080:8080 -e MCP_API_KEY="key" mcp-server

# Deploy to fly.io
fly launch --name my-mcp-server
fly secrets set MCP_API_KEY="key"
fly deploy
```

## Client Configuration (Remote)

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

## Navigation

| Previous | Next |
|----------|------|
| [Blog 11: Research Browser Part 3](../blog-11/blog.md) | [Blog 13: Multi-Client MCP](../blog-13/blog.md) |
