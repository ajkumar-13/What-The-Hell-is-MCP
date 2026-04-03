# Blog 11: Deep Research Browser – Part 3 (Complete Research Assistant)

The final piece: web search, PDF extraction, research session tracking, and Markdown export.

## What This Blog Adds

- **search_web** — Search via SerpAPI (Google) or DuckDuckGo (free)
- **research_pdf** — Download and extract PDF text, auto-summarize via Sampling
- **start_research_session** / **add_note** / **export_research** — Track sources and findings
- **end_research_session** — Close the active session
- **deep_research** prompt — Orchestrates the full workflow

## Complete Project Structure

```
mcp-research-browser/
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── server.py          # All MCP tools (12 total)
    ├── browser.py          # Playwright wrapper
    ├── extractor.py        # Content extraction
    ├── summarizer.py       # Sampling-based summarization
    ├── cache.py            # URL page cache
    ├── search.py           # Web search (SerpAPI / DuckDuckGo)
    ├── pdf.py              # PDF text extraction (PyMuPDF)
    └── session.py          # Research session tracking
```

## Full Research Flow

```
search_web("topic")
  → research_url(url1, focus="...")
  → research_url(url2)
  → research_pdf(pdf_url)
  → add_note("key finding", source_url=url1)
  → export_research()
```

## Navigation

| Previous | Next |
|----------|------|
| [Blog 10: Research Browser Part 2](../blog-10/blog.md) | [Blog 12: Production Deployment](../blog-12/blog.md) |
