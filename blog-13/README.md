# Blog 13: Multi-Client MCP

One server, every client—prove MCP's interoperability promise by connecting a single server to Claude Desktop, Cursor, VS Code, and programmatic Python clients.

## What This Blog Covers

- Building a **Team Knowledge Base Server** with `search_docs`, `get_snippet`, `ask_faq`
- **Claude Desktop** configuration (`claude_desktop_config.json`)
- **Cursor IDE** configuration (`.cursor/mcp.json`)
- **VS Code** native MCP support (`.vscode/mcp.json` with GitHub Copilot)
- **Programmatic Python** clients (`mcp.ClientSession`)
- Local (stdio) and remote (Streamable HTTP) configs for each client
- Config quick reference and comparison table
- Series wrap-up and next steps

## Key Concepts

- **One server, zero code changes** — the same server works with every MCP client
- **Config format differences** — Claude/Cursor use `"mcpServers"`, VS Code uses `"servers"` with explicit `"type"`
- **Transport flexibility** — stdio for local, Streamable HTTP for remote (same server binary)
- **Input variables** — VS Code supports secure credential prompts via `"inputs"`

## Client Config Locations

| Client | Config Path |
|--------|-------------|
| Claude Desktop (Win) | `%APPDATA%\Claude\claude_desktop_config.json` |
| Claude Desktop (Mac) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Cursor (project) | `.cursor/mcp.json` |
| Cursor (global) | `~/.cursor/mcp.json` |
| VS Code (workspace) | `.vscode/mcp.json` |
| VS Code (user) | User profile `mcp.json` (open via `MCP: Open User Configuration`) |

## Navigation

| Previous | Series |
|----------|--------|
| [Blog 12: Production Deployment](../blog-12/blog.md) | [Series Overview](../README.md) |
