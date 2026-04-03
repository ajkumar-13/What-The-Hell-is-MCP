# Blog 10: Deep Research Browser – Part 2 (Server-Side Summarization)

Introduces MCP Sampling — where the server asks the host's LLM for help — to automatically summarize large web pages.

## What This Blog Adds

- **research_url** — Browse + auto-summarize via Sampling
- **summarizer.py** — Chunk → Summarize → Aggregate pipeline
- Graceful degradation when Sampling is unavailable
- Focused summaries with optional topic guidance

## Key Concept: MCP Sampling

```
Server has 15,000 words → Splits into 5 chunks
  → Asks LLM to summarize each (5 Sampling requests)
  → Asks LLM to combine summaries (1 more request)
  → Returns ~400 words to the conversation
```

## API Usage

```python
from mcp.types import TextContent, SamplingMessage

result = await ctx.session.create_message(
    messages=[
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text="Summarize: ..."),
        )
    ],
    max_tokens=800,
)
summary = result.content.text
```

## Navigation

| Previous | Next |
|----------|------|
| [Blog 9: Research Browser Part 1](../blog-9/blog.md) | [Blog 11: Research Browser Part 3](../blog-11/blog.md) |
