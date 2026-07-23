# Blog 9: Deep Research Browser – Part 1
## Headless Browsing & Content Extraction


> *"Web pages are huge. Sending 5 MB of HTML to an LLM doesn't work. We need the server to be smart, to browse, extract, and prepare content before sending."*

---

## Introduction

We've built MCP servers for databases (Blog 5-6) and Kubernetes (Blog 7-8). Now we're tackling a different kind of data source: **the web**.

The challenge isn't fetching a page, it's making the content *useful*. A typical web page is 500 KB to 5 MB of raw HTML. Most of that is navigation bars, ads, JavaScript, CSS, and boilerplate. The actual article content? Maybe 10-50 KB.

Our MCP server will:
1. Browse pages headlessly with **Playwright** (handles JavaScript-rendered content)
2. Extract the main content, discarding boilerplate
3. Cache results to avoid redundant fetches
4. Take screenshots for visual confirmation
5. Search within extracted content

In Blog 10, we'll add **MCP Sampling**, where the server asks the LLM to summarize content that's still too large. But first, let's build the browser.

---

## 1. Project Setup

```
mcp-research-browser/
├── pyproject.toml
└── src/
    ├── __init__.py
    ├── server.py          # MCP server with tools
    ├── browser.py          # Playwright wrapper
    ├── extractor.py        # Content extraction
    └── cache.py            # URL-based page cache
```

### pyproject.toml

```toml
[project]
name = "mcp-research-browser"
version = "0.1.0"
description = "MCP server for web research with headless browsing"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.9",
    "playwright>=1.49",
    "trafilatura>=2.0",
    "markdownify>=0.14",
]

[project.scripts]
mcp-research-browser = "src.server:main"
```

### Install Dependencies

```bash
cd mcp-research-browser
uv sync

# Install browser binaries (one-time setup)
uv run playwright install chromium
```

> **Why Playwright over requests?** Many modern pages render content via JavaScript. A simple `requests.get()` returns empty shells. Playwright runs a real browser engine that executes JavaScript, waits for dynamic content, and gives you the fully rendered page.

---

## 2. The Browser Wrapper

The browser module manages a persistent Playwright browser instance:

```python
# src/browser.py
"""Headless browser management with Playwright."""
import asyncio
import base64
import logging
import sys
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, Page

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    """Result from browsing a page."""
    url: str
    title: str
    html: str
    text: str                 # Visible text from the page
    status: int | None = None


@dataclass
class ScreenshotResult:
    """Result from taking a screenshot."""
    url: str
    data_base64: str          # PNG image as base64
    width: int
    height: int


class HeadlessBrowser:
    """Manages a single Playwright browser instance."""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._playwright = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        """Launch the browser."""
        if self._browser:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        logger.info("Chromium browser launched (headless=%s)", self._headless)

    async def stop(self) -> None:
        """Close the browser and clean up."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser stopped")

    async def fetch_page(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout_ms: int = 30_000,
    ) -> PageResult:
        """
        Navigate to a URL and return the page content.
        
        Args:
            url: The URL to fetch.
            wait_until: When to consider navigation done.
                        "domcontentloaded" | "load" | "networkidle"
            timeout_ms: Navigation timeout in milliseconds.
        """
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")

        page: Page = await self._browser.new_page()
        try:
            response = await page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout_ms,
            )
            status = response.status if response else None

            # Wait a moment for any late-loading JS content
            await asyncio.sleep(0.5)

            title = await page.title()
            html = await page.content()
            text = await page.inner_text("body")

            logger.info("Fetched %s (%d bytes HTML)", url, len(html))

            return PageResult(
                url=url,
                title=title,
                html=html,
                text=text,
                status=status,
            )
        finally:
            await page.close()

    async def take_screenshot(
        self,
        url: str,
        full_page: bool = False,
        timeout_ms: int = 30_000,
    ) -> ScreenshotResult:
        """Take a PNG screenshot of a page."""
        if not self._browser:
            raise RuntimeError("Browser not started. Call start() first.")

        page: Page = await self._browser.new_page(
            viewport={"width": 1280, "height": 720},
        )
        try:
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            await asyncio.sleep(1)  # Let animations settle

            screenshot_bytes = await page.screenshot(full_page=full_page)
            data_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            viewport = page.viewport_size
            width = viewport["width"] if viewport else 1280
            height = viewport["height"] if viewport else 720

            logger.info("Screenshot of %s (%d bytes)", url, len(screenshot_bytes))

            return ScreenshotResult(
                url=url,
                data_base64=data_b64,
                width=width,
                height=height,
            )
        finally:
            await page.close()


# Module-level singleton
browser = HeadlessBrowser()
```

**Key design decisions:**

| Decision | Why |
|----------|-----|
| Singleton browser | One Chromium process, reused across tool calls |
| New page per request | Isolate cookies/state between navigations |
| `domcontentloaded` default | Faster than `networkidle`, good enough for most content |
| `page.close()` in finally | Prevent page/memory leaks |
| Extra `asyncio.sleep` | Let late JavaScript content render |

---

## 3. Content Extraction

Raw HTML is useless for an LLM. We need the main article content in clean Markdown:

```python
# src/extractor.py
"""Extract main content from web pages, discarding boilerplate."""
import logging
import sys
from dataclasses import dataclass

import trafilatura
from markdownify import markdownify

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class ExtractedContent:
    """Clean content extracted from a web page."""
    url: str
    title: str
    content: str        # Main content in Markdown
    word_count: int
    method: str         # "trafilatura" or "fallback"


def extract_content(url: str, html: str, title: str) -> ExtractedContent:
    """
    Extract main content from HTML, removing navigation, ads, footers.
    
    Uses trafilatura (best for articles) with a markdownify fallback.
    """
    content = None
    method = "trafilatura"

    # Try trafilatura first (optimized for article extraction)
    try:
        content = trafilatura.extract(
            html,
            include_links=True,
            include_images=False,
            include_tables=True,
            output_format="txt",
            url=url,
        )
    except Exception as e:
        logger.warning("trafilatura failed for %s: %s", url, e)

    # Fallback: convert full HTML to Markdown
    if not content or len(content.strip()) < 100:
        method = "fallback"
        try:
            content = markdownify(
                html,
                heading_style="ATX",
                strip=["script", "style", "nav", "footer", "header"],
            )
            # Clean up excessive whitespace from markdownify
            lines = [line.rstrip() for line in content.splitlines()]
            # Remove runs of 3+ blank lines
            cleaned = []
            blank_count = 0
            for line in lines:
                if line == "":
                    blank_count += 1
                    if blank_count <= 2:
                        cleaned.append(line)
                else:
                    blank_count = 0
                    cleaned.append(line)
            content = "\n".join(cleaned)
        except Exception as e:
            logger.error("Fallback extraction failed for %s: %s", url, e)
            content = "(Could not extract content from this page.)"

    word_count = len(content.split())
    logger.info(
        "Extracted %d words from %s (method: %s)", word_count, url, method
    )

    return ExtractedContent(
        url=url,
        title=title,
        content=content.strip(),
        word_count=word_count,
        method=method,
    )
```

**Why trafilatura?**

| Library | Strengths | Weaknesses |
|---------|----------|------------|
| `trafilatura` | Excellent article extraction, handles many layouts | Struggles with non-article pages |
| `readability-lxml` | Good for news articles | Heavier dependency |
| `markdownify` | Works on any HTML | Keeps too much boilerplate |
| `BeautifulSoup` | Full control | Requires manual rules per site |

We use trafilatura as the primary extractor with markdownify as a fallback for pages that aren't standard articles (documentation, forums, etc.).

---

## 4. Page Cache

Fetching and extracting the same page twice wastes time and bandwidth:

```python
# src/cache.py
"""Simple TTL cache for extracted web content."""
import logging
import sys
import time
from dataclasses import dataclass, field

from .extractor import ExtractedContent

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """A cached page with expiration."""
    content: ExtractedContent
    timestamp: float
    ttl: float

    @property
    def expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


@dataclass
class PageCache:
    """In-memory cache with TTL expiration."""
    default_ttl: float = 600.0  # 10 minutes
    max_entries: int = 100
    _store: dict[str, CacheEntry] = field(default_factory=dict)

    def get(self, url: str) -> ExtractedContent | None:
        """Get cached content, or None if missing/expired."""
        entry = self._store.get(url)
        if entry is None:
            return None
        if entry.expired:
            del self._store[url]
            logger.info("Cache expired: %s", url)
            return None
        logger.info("Cache hit: %s", url)
        return entry.content

    def put(self, url: str, content: ExtractedContent, ttl: float | None = None) -> None:
        """Cache extracted content for a URL."""
        # Evict oldest entries if at capacity
        if len(self._store) >= self.max_entries:
            self._evict_oldest()

        self._store[url] = CacheEntry(
            content=content,
            timestamp=time.time(),
            ttl=ttl or self.default_ttl,
        )
        logger.info("Cached: %s (%d entries)", url, len(self._store))

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()
        logger.info("Cache cleared")

    def stats(self) -> dict:
        """Return cache statistics."""
        now = time.time()
        active = sum(1 for e in self._store.values() if not e.expired)
        return {
            "total_entries": len(self._store),
            "active_entries": active,
            "expired_entries": len(self._store) - active,
            "max_entries": self.max_entries,
            "default_ttl_seconds": self.default_ttl,
        }

    def _evict_oldest(self) -> None:
        """Remove the oldest entry."""
        if not self._store:
            return
        oldest_url = min(self._store, key=lambda u: self._store[u].timestamp)
        del self._store[oldest_url]
        logger.info("Evicted oldest cache entry: %s", oldest_url)


# Module-level singleton
cache = PageCache()
```

---

## 5. The MCP Server

Now we assemble everything into the MCP server:

```python
# src/server.py
"""MCP Research Browser — headless browsing and content extraction."""
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .browser import browser
from .extractor import extract_content
from .cache import cache

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============ LIFECYCLE ============

@asynccontextmanager
async def lifespan(server: FastMCP):
    """Launch browser on startup, close on shutdown."""
    await browser.start()
    logger.info("Research Browser MCP server ready")
    try:
        yield
    finally:
        await browser.stop()
        cache.clear()
        logger.info("Research Browser MCP server stopped")


mcp = FastMCP("Research Browser", lifespan=lifespan)


# ============ TOOLS ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def browse_url(url: str, use_cache: bool = True) -> str:
    """
    Browse a URL and extract the main content.

    Navigates to the page with a headless browser, extracts the article
    content (removing navigation, ads, boilerplate), and returns
    clean text suitable for analysis.

    Args:
        url: The web page URL to browse.
        use_cache: Whether to use cached content if available (default: True).
    """
    # Check cache first
    if use_cache:
        cached = cache.get(url)
        if cached:
            return _format_content(cached, from_cache=True)

    # Fetch and extract
    try:
        page_result = await browser.fetch_page(url)
    except Exception as e:
        return f"Failed to load page: {e}"

    extracted = extract_content(
        url=page_result.url,
        html=page_result.html,
        title=page_result.title,
    )

    # Cache the result
    cache.put(url, extracted)

    return _format_content(extracted, from_cache=False)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def screenshot_page(url: str, full_page: bool = False) -> list:
    """
    Take a screenshot of a web page.

    Returns the screenshot as an image. Use this to visually inspect
    page layout, verify content, or capture visual information.

    Args:
        url: The web page URL to screenshot.
        full_page: If True, captures the full scrollable page.
                   If False, captures the visible viewport (1280x720).
    """
    try:
        result = await browser.take_screenshot(url, full_page=full_page)
    except Exception as e:
        return f"Failed to screenshot: {e}"

    # Return as MCP image content
    return [
        {
            "type": "text",
            "text": f"Screenshot of {result.url} ({result.width}x{result.height})",
        },
        {
            "type": "image",
            "data": result.data_base64,
            "mimeType": "image/png",
        },
    ]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def search_in_page(url: str, query: str) -> str:
    """
    Search for text within a web page's content.

    Browses the page (or uses cache), then searches the extracted
    content for the query string. Returns matching paragraphs
    with surrounding context.

    Args:
        url: The web page URL to search.
        query: Text to search for (case-insensitive).
    """
    # Get content (from cache or fresh)
    cached = cache.get(url)
    if cached:
        extracted = cached
    else:
        try:
            page_result = await browser.fetch_page(url)
        except Exception as e:
            return f"Failed to load page: {e}"
        extracted = extract_content(
            url=page_result.url,
            html=page_result.html,
            title=page_result.title,
        )
        cache.put(url, extracted)

    # Search through paragraphs
    query_lower = query.lower()
    paragraphs = extracted.content.split("\n\n")
    matches = []

    for i, para in enumerate(paragraphs):
        if query_lower in para.lower():
            matches.append(
                {
                    "paragraph_index": i,
                    "text": para.strip(),
                }
            )

    if not matches:
        return (
            f"No matches for '{query}' in {extracted.title}.\n\n"
            f"Page has {extracted.word_count} words. "
            f"Try a different search term."
        )

    output = f"**Found {len(matches)} match(es) for '{query}' in [{extracted.title}]({url}):**\n\n"
    for match in matches[:10]:  # Limit to 10 matches
        output += f"---\n{match['text']}\n\n"

    return output


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def get_cache_stats() -> str:
    """
    Show cache statistics.

    Returns the number of cached pages, active vs expired entries,
    and capacity information.
    """
    stats = cache.stats()
    return json.dumps(stats, indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
    }
)
async def clear_cache() -> str:
    """
    Clear all cached page content.

    Use when you want to force fresh fetches of previously visited pages.
    """
    cache.clear()
    return "Cache cleared. All pages will be fetched fresh on next browse."


# ============ RESOURCE ============

@mcp.resource("browser://cache-stats")
async def cache_stats_resource() -> str:
    """Current cache statistics."""
    stats = cache.stats()
    return json.dumps(stats, indent=2)


# ============ PROMPT ============

@mcp.prompt(title="Research Topic")
async def research_topic(topic: str, num_urls: int = 3) -> str:
    """Prompt to research a topic by browsing multiple URLs."""
    return (
        f"# Research Task: {topic}\n\n"
        f"Please research the topic '{topic}' thoroughly.\n\n"
        f"## Instructions\n"
        f"1. Browse {num_urls} relevant URLs about this topic\n"
        f"2. Extract key information from each page\n"
        f"3. Synthesize findings into a comprehensive summary\n"
        f"4. Include citations (URLs) for each source\n\n"
        f"## Available Tools\n"
        f"- `browse_url(url)` — Fetch and extract content from a URL\n"
        f"- `search_in_page(url, query)` — Search within a page\n"
        f"- `screenshot_page(url)` — Visual capture of a page\n\n"
        f"Begin by searching for relevant pages about: {topic}"
    )


# ============ HELPERS ============

def _format_content(content, from_cache: bool = False) -> str:
    """Format extracted content for the LLM."""
    cache_note = " (from cache)" if from_cache else ""
    size_warning = ""

    if content.word_count > 5000:
        size_warning = (
            f"\n\n**Large page** ({content.word_count} words). "
            f"Consider using `search_in_page` to find specific information "
            f"rather than reading the full content."
        )

    return (
        f"# {content.title}\n"
        f"**URL:** {content.url}\n"
        f"**Words:** {content.word_count} | "
        f"**Extraction:** {content.method}{cache_note}\n"
        f"{size_warning}\n\n"
        f"---\n\n"
        f"{content.content}"
    )


# ============ ENTRY POINT ============

def main():
    """Run the MCP Research Browser server."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

---

## 6. The `__init__.py`

```python
# src/__init__.py
"""MCP Research Browser — web research with headless browsing."""
```

---

## 7. How It All Fits Together

```
┌──────────────────────────────────────────────────────────────┐
│                    MCP Research Browser                      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  User: "Research X"                                          │
│       │                                                      │
│       ▼                                                      │
│  ┌──────────────┐     ┌──────────────┐      ┌───────────┐    │
│  │  browse_url  │────▶│   browser.py │────▶│ Chromium  │    │
│  │   (tool)     │     │   (fetch)    │      │ (render)  │    │
│  └──────┬───────┘     └──────────────┘      └───────────┘    │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────┐     ┌──────────────┐                       │
│  │ extractor.py │────▶│ trafilatura  │                      │
│  │  (extract)   │     │ (clean HTML) │                       │
│  └──────┬──────┘      └──────────────┘                       │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────┐                                            │
│  │   cache.py   │  ← saves for reuse                         │
│  │   (store)    │                                            │
│  └──────┬──────┘                                             │
│         │                                                    │
│         ▼                                                    │
│  Clean Markdown returned to LLM                              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 8. Claude Desktop Configuration

```json
{
  "mcpServers": {
    "research-browser": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\path\\to\\mcp-research-browser",
        "mcp-research-browser"
      ]
    }
  }
}
```

---

## 9. Testing

### Test 1: Browse a Page

> "Browse https://en.wikipedia.org/wiki/Model_Context_Protocol and summarize the key points."

Claude calls `browse_url` → gets extracted article content → summarizes for you.

### Test 2: Screenshot

> "Take a screenshot of https://modelcontextprotocol.io"

Claude calls `screenshot_page` → you see the rendered page image inline.

### Test 3: Search Within a Page

> "Search for 'transport' in the MCP Wikipedia article."

Claude calls `search_in_page` → returns only the paragraphs mentioning "transport."

### Test 4: Cache Behavior

> "Browse the MCP page again."

Second call returns instantly from cache, no Chromium page load needed.

---

## 10. The Content Size Problem

Even after extraction, some pages are enormous:

| Page Type | Raw HTML | After Extraction |
|-----------|----------|------------------|
| Blog post | 200 KB | 5-15 KB |
| News article | 500 KB | 10-30 KB |
| Wikipedia article | 1 MB | 50-100 KB |
| Documentation page | 300 KB | 20-60 KB |
| API reference | 2 MB | 100-300 KB |

A 100 KB article is about 15,000 words, far too much for an LLM context window to handle efficiently.

**The solution?** Have the *server* ask the *LLM* to summarize chunks before returning.

That's **MCP Sampling**, and it's the subject of Blog 10.

---

## 11. Troubleshooting

| Problem | Solution |
|---------|----------|
| "Browser not started" | Check that `playwright install chromium` completed |
| Page timeout | Increase `timeout_ms` or check the URL |
| Empty extraction | Some JS-heavy SPAs need `wait_until="networkidle"` |
| Large response | Use `search_in_page` for targeted queries |
| Permission error on Linux | Run `playwright install-deps` for system libraries |
| Chromium crashes | Reduce concurrent page loads, check available memory |

---

## Key Takeaways

- Playwright renders JavaScript-heavy pages that requests.get() misses
- trafilatura extracts article content, discarding boilerplate
- Caching prevents redundant page fetches
- Screenshots return inline images via MCP image content
- Search within pages for targeted information retrieval
- Large pages remain a problem — solved by Sampling in Blog 10

---

## What's Next?

Our browser works, but large pages still overwhelm the LLM context. In **Blog 10: Deep Research Browser – Part 2**, we introduce the most powerful concept in MCP:

**Sampling**, where the *server* asks the *LLM* for help.

Instead of dumping 15,000 words into the conversation, the server will:
1. Split content into chunks
2. Ask the LLM to summarize each chunk (via Sampling)
3. Combine the summaries
4. Return a concise synthesis to the main conversation

The LLM helps the server, which helps the LLM. It's recursive intelligence.

---

## Quick Reference

### Tools

| Tool | Purpose | Annotations |
|------|---------|-------------|
| `browse_url` | Fetch and extract page content | `readOnlyHint: true` |
| `screenshot_page` | Capture page screenshot | `readOnlyHint: true` |
| `search_in_page` | Find text within a page | `readOnlyHint: true` |
| `get_cache_stats` | View cache statistics | `readOnlyHint: true` |
| `clear_cache` | Clear page cache | `destructiveHint: true` |

### Dependencies

```
mcp[cli]>=1.9
playwright>=1.49
trafilatura>=2.0
markdownify>=0.14
```

---

| [← Blog 8: DevOps First Responder Part 2](../blog-8/blog.md) | [Blog 10: Deep Research Browser Part 2 →](../blog-10/blog.md) |
|:---|---:|
