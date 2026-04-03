# mcp-system-info

System information MCP server from Blog 3, Your First MCP Server.

This sample project exposes:
- `get_system_info` for CPU, memory, and disk usage
- `find_process` for basic process lookup
- `system://top-processes` as a read-only resource
- `diagnose_performance` as a reusable MCP prompt

## Requirements

- Python 3.10+
- `uv`
- An MCP host that can launch local stdio servers, such as Claude Desktop

## Install

```bash
uv sync
```

## Run

```bash
uv run python src/server.py
```

This project uses stdio transport by default, so it is intended to be launched by an MCP host rather than browsed directly in a browser.

## Notes

- Logging is sent to `stderr` so the stdio transport stays clean
- Tool output should be treated as model-visible data; avoid returning secrets
- The main tutorial walkthrough lives in `../blog.md`
