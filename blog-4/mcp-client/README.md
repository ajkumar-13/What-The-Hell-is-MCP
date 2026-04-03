# mcp-client

Multi-provider CLI MCP host from Blog 4, Building Your Own MCP Client.

This sample project connects to a local stdio MCP server and lets one of three LLM providers decide when to call tools:
- Anthropic (`ANTHROPIC_API_KEY`)
- OpenAI (`OPENAI_API_KEY`)
- Google Gemini (`GOOGLE_API_KEY`)

## Requirements

- Python 3.10+
- `uv`
- One provider API key
- A local MCP server to connect to, such as the Blog 3 `mcp-system-info` project

## Install

```bash
uv sync
```

## Configure

Create a `.env` file with one provider and key:

```text
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key-here
```

Optional:

```text
LLM_MODEL=claude-sonnet-4-20250514
```

Update the server path in `src/client.py` so it points to your local MCP server directory before running the CLI.

## Run

```bash
uv run python src/client.py
```

## Notes

- This tutorial project covers stdio transport only; remote Streamable HTTP clients are covered later in the series
- The main walkthrough lives in `../blog.md`
- Tool execution and conversation history are managed in `src/client.py`
