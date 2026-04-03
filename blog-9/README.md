# Blog 9: Deep Research Browser – Part 1 (Headless Browsing)

MCP server that browses the web with Playwright and extracts clean content for LLM consumption.

## What This Blog Builds

- **browse_url** — Fetch a page, extract main content, return clean Markdown
- **screenshot_page** — Capture a PNG screenshot of any page
- **search_in_page** — Search within a page's extracted content
- **get_cache_stats** / **clear_cache** — Manage the page cache

## Project Structure

```
mcp-research-browser/
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── server.py       # MCP tools, resource, prompt
    ├── browser.py      # Playwright wrapper (Chromium)
    ├── extractor.py    # trafilatura + markdownify content extraction
    └── cache.py        # In-memory TTL cache by URL
```

## Quick Start

```bash
cd mcp-research-browser
uv sync
uv run playwright install chromium
uv run mcp-research-browser
```

## Key Dependencies

| Package | Purpose |
|---------|---------|
| `mcp[cli]>=1.9` | MCP Python SDK |
| `playwright>=1.49` | Headless Chromium browsing |
| `trafilatura>=2.0` | Article content extraction |
| `markdownify>=0.14` | HTML-to-Markdown fallback |

## Navigation

| Previous | Next |
|----------|------|
| [Blog 8: DevOps Part 2](../blog-8/blog.md) | [Blog 10: Research Browser Part 2 (Sampling)](../blog-10/blog.md) |
