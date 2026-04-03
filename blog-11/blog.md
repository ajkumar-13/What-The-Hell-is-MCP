# Blog 11: Deep Research Browser – Part 3
## Complete Research Assistant

*Reading Time: 25 minutes*

---

> *"Let's bring it all together: web search, multi-page research, PDF extraction, and citation tracking. A real research assistant."*

---

## Introduction

Over Blogs 9-10, we built a browser that fetches pages, extracts content, and uses Sampling to summarize large documents. But a real researcher doesn't just read pages one at a time—they:

1. **Search** for relevant sources
2. **Browse** multiple pages
3. **Read** PDFs and papers
4. **Track** sources and citations
5. **Export** findings as a report

Today we're adding all of that.

### Complete Tool Set After This Blog

| Tool | Blog | Purpose |
|------|------|---------|
| `browse_url` | 9 | Fetch and extract page content |
| `screenshot_page` | 9 | Visual page capture |
| `search_in_page` | 9 | Search within a page |
| `research_url` | 10 | Browse + auto-summarize (Sampling) |
| `web_search` | **11** | Search the web for pages |
| `research_pdf` | **11** | Extract text from PDF documents |
| `start_session` | **11** | Begin a tracked research session |
| `add_note` | **11** | Add findings to session |
| `export_research` | **11** | Export session as Markdown report |

---

## 1. Web Search Integration

We need a search API to find relevant pages. We'll use SerpAPI (free tier: 100 searches/month) with a fallback mock for development.

```python
# src/search.py
"""Web search integration."""
import json
import logging
import os
import sys
from dataclasses import dataclass

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    position: int


async def web_search(query: str, num_results: int = 5) -> list[SearchResult]:
    """
    Search the web and return results with titles, URLs, and snippets.
    
    Uses SerpAPI if SERPAPI_KEY is set, otherwise uses DuckDuckGo.
    """
    serpapi_key = os.environ.get("SERPAPI_KEY")

    if serpapi_key:
        return await _search_serpapi(query, num_results, serpapi_key)
    else:
        return await _search_duckduckgo(query, num_results)


async def _search_serpapi(
    query: str, num_results: int, api_key: str
) -> list[SearchResult]:
    """Search using SerpAPI (Google results)."""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://serpapi.com/search",
                params={
                    "q": query,
                    "api_key": api_key,
                    "num": num_results,
                    "engine": "google",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.error("SerpAPI search failed: %s", e)
        raise RuntimeError(f"Search failed: {e}")

    results = []
    for i, item in enumerate(data.get("organic_results", [])[:num_results]):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                position=i + 1,
            )
        )

    logger.info("SerpAPI returned %d results for '%s'", len(results), query)
    return results


async def _search_duckduckgo(query: str, num_results: int) -> list[SearchResult]:
    """Search using DuckDuckGo (no API key required)."""
    from duckduckgo_search import DDGS

    try:
        with DDGS() as ddgs:
            raw_results = list(ddgs.text(query, max_results=num_results))
    except Exception as e:
        logger.error("DuckDuckGo search failed: %s", e)
        raise RuntimeError(f"Search failed: {e}")

    results = []
    for i, item in enumerate(raw_results):
        results.append(
            SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                snippet=item.get("body", ""),
                position=i + 1,
            )
        )

    logger.info("DuckDuckGo returned %d results for '%s'", len(results), query)
    return results
```

---

## 2. PDF Extraction

Research often involves PDFs—papers, reports, documentation. We use PyMuPDF for extraction:

```python
# src/pdf.py
"""PDF text extraction."""
import logging
import sys
import tempfile
from dataclasses import dataclass

import httpx
import pymupdf

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class PDFContent:
    """Extracted PDF content."""
    url: str
    title: str
    text: str
    page_count: int
    word_count: int


async def extract_pdf(url: str, max_pages: int = 50) -> PDFContent:
    """
    Download a PDF and extract its text content.
    
    Args:
        url: URL of the PDF file.
        max_pages: Maximum pages to extract (default: 50).
    """
    # Download the PDF
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            pdf_bytes = response.content
    except Exception as e:
        raise RuntimeError(f"Failed to download PDF: {e}")

    logger.info("Downloaded PDF from %s (%d bytes)", url, len(pdf_bytes))

    # Extract text
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        title = doc.metadata.get("title", "") or url.split("/")[-1]
        
        pages_to_read = min(len(doc), max_pages)
        text_parts = []
        
        for page_num in range(pages_to_read):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(f"--- Page {page_num + 1} ---\n{text}")
        
        doc.close()
    except Exception as e:
        raise RuntimeError(f"Failed to extract PDF text: {e}")

    full_text = "\n\n".join(text_parts)
    word_count = len(full_text.split())

    logger.info(
        "Extracted %d words from %d pages of %s",
        word_count,
        pages_to_read,
        url,
    )

    return PDFContent(
        url=url,
        title=title,
        text=full_text,
        page_count=pages_to_read,
        word_count=word_count,
    )
```

---

## 3. Research Session Tracking

A research session tracks everything: sources visited, notes taken, and citations collected.

```python
# src/session.py
"""Research session management — track sources, notes, and citations."""
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class Source:
    """A research source (web page or PDF)."""
    url: str
    title: str
    source_type: str  # "web" or "pdf"
    summary: str
    word_count: int
    timestamp: str


@dataclass
class Note:
    """A research note."""
    text: str
    source_url: str | None
    timestamp: str


@dataclass
class ResearchSession:
    """A tracked research session."""
    topic: str
    created: str
    sources: list[Source] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def add_source(
        self,
        url: str,
        title: str,
        source_type: str,
        summary: str,
        word_count: int,
    ) -> None:
        """Record a source that was consulted."""
        self.sources.append(
            Source(
                url=url,
                title=title,
                source_type=source_type,
                summary=summary,
                word_count=word_count,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        logger.info("Added source: %s (%s)", title, url)

    def add_note(self, text: str, source_url: str | None = None) -> None:
        """Add a research note."""
        self.notes.append(
            Note(
                text=text,
                source_url=source_url,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        logger.info("Added note (%d chars)", len(text))

    def export_markdown(self) -> str:
        """Export the session as a Markdown research report."""
        lines = [
            f"# Research Report: {self.topic}",
            f"*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*\n",
            f"---\n",
        ]

        # Summary stats
        lines.append(f"## Overview\n")
        lines.append(f"- **Topic:** {self.topic}")
        lines.append(f"- **Sources consulted:** {len(self.sources)}")
        lines.append(f"- **Notes recorded:** {len(self.notes)}")
        total_words = sum(s.word_count for s in self.sources)
        lines.append(f"- **Total content processed:** {total_words:,} words")
        lines.append(f"- **Session started:** {self.created}\n")

        # Notes / Findings
        if self.notes:
            lines.append(f"## Findings\n")
            for i, note in enumerate(self.notes, 1):
                lines.append(f"### Finding {i}\n")
                lines.append(note.text)
                if note.source_url:
                    lines.append(f"\n*Source: {note.source_url}*")
                lines.append("")

        # Sources
        if self.sources:
            lines.append(f"## Sources\n")
            for i, source in enumerate(self.sources, 1):
                icon = "🌐" if source.source_type == "web" else "📄"
                lines.append(
                    f"{i}. {icon} [{source.title}]({source.url}) "
                    f"— {source.word_count:,} words"
                )
                if source.summary:
                    # First 200 chars of summary as preview
                    preview = source.summary[:200]
                    if len(source.summary) > 200:
                        preview += "..."
                    lines.append(f"   > {preview}")
                lines.append("")

        lines.append("---")
        lines.append("*Report generated by MCP Research Browser*")

        return "\n".join(lines)

    def status(self) -> str:
        """Return a brief status string."""
        return (
            f"📋 Session: '{self.topic}' | "
            f"{len(self.sources)} sources | "
            f"{len(self.notes)} notes"
        )


# Module-level active session
_active_session: ResearchSession | None = None


def get_session() -> ResearchSession | None:
    return _active_session


def start_session(topic: str) -> ResearchSession:
    global _active_session
    _active_session = ResearchSession(
        topic=topic,
        created=datetime.now(timezone.utc).isoformat(),
    )
    logger.info("Started research session: %s", topic)
    return _active_session


def end_session() -> ResearchSession | None:
    global _active_session
    session = _active_session
    _active_session = None
    return session
```

---

## 4. Updated Server with All Tools

Here's the final `src/server.py` with every tool:

```python
# src/server.py — Complete Research Browser MCP Server
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP

from .browser import browser
from .cache import cache
from .extractor import extract_content
from .pdf import extract_pdf
from .search import web_search as do_web_search
from .session import get_session, start_session, end_session
from .summarizer import summarize_content, SUMMARIZE_THRESHOLD

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP):
    await browser.start()
    logger.info("Research Browser MCP server ready (full)")
    try:
        yield
    finally:
        await browser.stop()
        cache.clear()
        logger.info("Research Browser MCP server stopped")


mcp = FastMCP("Research Browser", lifespan=lifespan)


# ============ SEARCH ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def search_web(query: str, num_results: int = 5) -> str:
    """
    Search the web for pages related to a query.

    Returns a list of results with titles, URLs, and snippets.
    Use the returned URLs with browse_url or research_url to read content.

    Requires SERPAPI_KEY env var for Google, or falls back to DuckDuckGo.

    Args:
        query: Search query.
        num_results: Number of results (default: 5, max: 10).
    """
    num_results = max(1, min(num_results, 10))
    try:
        results = await do_web_search(query, num_results)
    except RuntimeError as e:
        return f"❌ Search error: {e}"

    if not results:
        return f"No results found for '{query}'."

    output = f"**Search results for '{query}':**\n\n"
    for r in results:
        output += f"**{r.position}. [{r.title}]({r.url})**\n"
        output += f"   {r.snippet}\n\n"

    output += (
        f"---\n*{len(results)} results. "
        f"Use `research_url(url)` to read any of these pages.*"
    )
    return output


# ============ BROWSING ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def browse_url(url: str, use_cache: bool = True) -> str:
    """
    Browse a URL and extract the full main content.

    For automatic summarization of large pages, use research_url instead.

    Args:
        url: Web page URL.
        use_cache: Use cache if available (default: True).
    """
    if use_cache:
        cached = cache.get(url)
        if cached:
            return _format_content(cached, from_cache=True)

    try:
        page_result = await browser.fetch_page(url)
    except Exception as e:
        return f"❌ Failed to load page: {e}"

    extracted = extract_content(page_result.url, page_result.html, page_result.title)
    cache.put(url, extracted)

    # Track in session if active
    session = get_session()
    if session:
        session.add_source(
            url=url,
            title=extracted.title,
            source_type="web",
            summary=extracted.content[:300],
            word_count=extracted.word_count,
        )

    return _format_content(extracted)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def research_url(
    ctx: Context,
    url: str,
    focus: str | None = None,
) -> str:
    """
    Browse a URL and return a smart summary (recommended for research).

    Short pages return full content. Long pages are summarized via Sampling.

    Args:
        url: Web page URL.
        focus: Optional focus for the summary (e.g., "security risks").
    """
    cached = cache.get(url)
    if cached:
        extracted = cached
    else:
        try:
            page_result = await browser.fetch_page(url)
        except Exception as e:
            return f"❌ Failed to load page: {e}"
        extracted = extract_content(page_result.url, page_result.html, page_result.title)
        cache.put(url, extracted)

    # Track in session
    session = get_session()

    if extracted.word_count <= SUMMARIZE_THRESHOLD:
        if session:
            session.add_source(
                url=url,
                title=extracted.title,
                source_type="web",
                summary=extracted.content[:300],
                word_count=extracted.word_count,
            )
        return _format_content(extracted, from_cache=cached is not None)

    # Summarize long content
    summary = await summarize_content(ctx, extracted.content, focus=focus)

    if summary:
        if session:
            session.add_source(
                url=url,
                title=extracted.title,
                source_type="web",
                summary=summary[:300],
                word_count=extracted.word_count,
            )
        return (
            f"# {extracted.title}\n"
            f"**URL:** {url}\n"
            f"**Original:** {extracted.word_count} words → "
            f"**Summary:** {len(summary.split())} words\n"
            f"{'**Focus:** ' + focus if focus else ''}\n\n"
            f"---\n\n{summary}\n\n"
            f"---\n*Summarized via MCP Sampling.*"
        )

    # Fallback
    truncated = " ".join(extracted.content.split()[:2000])
    if session:
        session.add_source(
            url=url,
            title=extracted.title,
            source_type="web",
            summary=truncated[:300],
            word_count=extracted.word_count,
        )
    return (
        f"# {extracted.title}\n**URL:** {url}\n"
        f"**Words:** {extracted.word_count} (truncated to ~2000)\n\n"
        f"⚠️ *Sampling unavailable.*\n\n---\n\n{truncated}..."
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def screenshot_page(url: str, full_page: bool = False) -> list:
    """
    Take a screenshot of a web page.

    Args:
        url: Web page URL.
        full_page: Capture full scrollable page (default: viewport only).
    """
    try:
        result = await browser.take_screenshot(url, full_page=full_page)
    except Exception as e:
        return f"❌ Failed to screenshot: {e}"

    return [
        {"type": "text", "text": f"Screenshot of {result.url} ({result.width}x{result.height})"},
        {"type": "image", "data": result.data_base64, "mimeType": "image/png"},
    ]


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def search_in_page(url: str, query: str) -> str:
    """
    Search for text within a page's content.

    Args:
        url: Web page URL.
        query: Text to search for (case-insensitive).
    """
    cached = cache.get(url)
    if cached:
        extracted = cached
    else:
        try:
            page_result = await browser.fetch_page(url)
        except Exception as e:
            return f"❌ Failed to load page: {e}"
        extracted = extract_content(page_result.url, page_result.html, page_result.title)
        cache.put(url, extracted)

    query_lower = query.lower()
    paragraphs = extracted.content.split("\n\n")
    matches = [p.strip() for p in paragraphs if query_lower in p.lower()]

    if not matches:
        return f"No matches for '{query}' in {extracted.title}."

    output = f"**{len(matches)} match(es) for '{query}' in [{extracted.title}]({url}):**\n\n"
    for m in matches[:10]:
        output += f"---\n{m}\n\n"
    return output


# ============ PDF ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def research_pdf(
    ctx: Context,
    url: str,
    focus: str | None = None,
    max_pages: int = 50,
) -> str:
    """
    Download and extract text from a PDF document.

    Automatically summarizes long PDFs using Sampling.

    Args:
        url: URL of the PDF file.
        focus: Optional focus for summarization.
        max_pages: Max pages to extract (default: 50).
    """
    try:
        pdf = await extract_pdf(url, max_pages=max_pages)
    except RuntimeError as e:
        return f"❌ PDF error: {e}"

    session = get_session()

    # Short PDF: return full text
    if pdf.word_count <= SUMMARIZE_THRESHOLD:
        if session:
            session.add_source(
                url=url,
                title=pdf.title,
                source_type="pdf",
                summary=pdf.text[:300],
                word_count=pdf.word_count,
            )
        return (
            f"# 📄 {pdf.title}\n"
            f"**URL:** {url}\n"
            f"**Pages:** {pdf.page_count} | **Words:** {pdf.word_count}\n\n"
            f"---\n\n{pdf.text}"
        )

    # Long PDF: summarize
    summary = await summarize_content(ctx, pdf.text, focus=focus)

    if summary:
        if session:
            session.add_source(
                url=url,
                title=pdf.title,
                source_type="pdf",
                summary=summary[:300],
                word_count=pdf.word_count,
            )
        return (
            f"# 📄 {pdf.title}\n"
            f"**URL:** {url}\n"
            f"**Pages:** {pdf.page_count} | "
            f"**Original:** {pdf.word_count} words → "
            f"**Summary:** {len(summary.split())} words\n\n"
            f"---\n\n{summary}\n\n"
            f"---\n*Summarized via MCP Sampling.*"
        )

    # Fallback
    truncated = " ".join(pdf.text.split()[:2000])
    if session:
        session.add_source(
            url=url,
            title=pdf.title,
            source_type="pdf",
            summary=truncated[:300],
            word_count=pdf.word_count,
        )
    return (
        f"# 📄 {pdf.title}\n**URL:** {url}\n"
        f"**Pages:** {pdf.page_count} | **Words:** {pdf.word_count} (truncated)\n\n"
        f"---\n\n{truncated}..."
    )


# ============ RESEARCH SESSION ============

@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": False}
)
async def start_research_session(topic: str) -> str:
    """
    Start a tracked research session.

    Sources browsed and notes added will be tracked for export.
    Only one session can be active at a time.

    Args:
        topic: The research topic or question.
    """
    existing = get_session()
    if existing:
        return (
            f"⚠️ Session already active: '{existing.topic}' "
            f"({len(existing.sources)} sources). "
            f"Export or end it first."
        )

    session = start_session(topic)
    return (
        f"✅ Research session started: **{topic}**\n\n"
        f"All `research_url` and `research_pdf` calls will be tracked.\n"
        f"Use `add_note` to record findings.\n"
        f"Use `export_research` when done."
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False})
async def add_note(text: str, source_url: str | None = None) -> str:
    """
    Add a research note to the active session.

    Args:
        text: The note or finding to record.
        source_url: Optional URL this note is about.
    """
    session = get_session()
    if not session:
        return "❌ No active research session. Use start_research_session first."

    session.add_note(text, source_url)
    return f"✅ Note added. {session.status()}"


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def export_research() -> str:
    """
    Export the current research session as a Markdown report.

    Includes all sources consulted, notes recorded, and citations.
    The session remains active after export.
    """
    session = get_session()
    if not session:
        return "❌ No active research session."

    report = session.export_markdown()
    return report


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
    }
)
async def end_research_session() -> str:
    """
    End the current research session.

    Export first if you want to keep the data.
    """
    session = end_session()
    if not session:
        return "❌ No active session to end."
    return (
        f"✅ Session '{session.topic}' ended. "
        f"Sources: {len(session.sources)}, Notes: {len(session.notes)}."
    )


# ============ CACHE ============

@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def get_cache_stats() -> str:
    """Show cache statistics."""
    return json.dumps(cache.stats(), indent=2)


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True}
)
async def clear_cache() -> str:
    """Clear all cached page content."""
    cache.clear()
    return "✅ Cache cleared."


# ============ RESOURCE ============

@mcp.resource("browser://cache-stats")
async def cache_stats_resource() -> str:
    """Cache statistics."""
    return json.dumps(cache.stats(), indent=2)


@mcp.resource("research://session")
async def session_resource() -> str:
    """Current research session status."""
    session = get_session()
    if not session:
        return "No active research session."
    return json.dumps(
        {
            "topic": session.topic,
            "sources": len(session.sources),
            "notes": len(session.notes),
            "created": session.created,
        },
        indent=2,
    )


# ============ PROMPT ============

@mcp.prompt(title="Deep Research")
async def deep_research(topic: str, depth: str = "standard") -> str:
    """Full research workflow prompt."""
    if depth == "deep":
        num_sources = "8-10"
        instructions = (
            "Be thorough. Check multiple perspectives and sources. "
            "Include data points, statistics, and expert opinions."
        )
    else:
        num_sources = "3-5"
        instructions = (
            "Cover the key aspects of the topic with reliable sources."
        )

    return (
        f"# Deep Research: {topic}\n\n"
        f"## Instructions\n\n"
        f"1. Start a research session: `start_research_session('{topic}')`\n"
        f"2. Search for relevant pages: `search_web('{topic}')`\n"
        f"3. Research {num_sources} of the most relevant URLs using `research_url`\n"
        f"4. If you find PDF papers, use `research_pdf` to extract them\n"
        f"5. Add key findings as notes with `add_note`\n"
        f"6. Export the final report with `export_research`\n\n"
        f"{instructions}\n\n"
        f"Begin now."
    )


# ============ HELPERS ============

def _format_content(content, from_cache: bool = False) -> str:
    cache_note = " (from cache)" if from_cache else ""
    size_warning = ""
    if content.word_count > 5000:
        size_warning = (
            f"\n\n⚠️ **Large page** ({content.word_count} words). "
            f"Consider `research_url` for summarization."
        )
    return (
        f"# {content.title}\n"
        f"**URL:** {content.url}\n"
        f"**Words:** {content.word_count} | "
        f"**Extraction:** {content.method}{cache_note}"
        f"{size_warning}\n\n---\n\n{content.content}"
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

---

## 5. Updated Dependencies

```toml
[project]
name = "mcp-research-browser"
version = "0.3.0"
description = "MCP server for deep web research with browsing, PDFs, and Sampling"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.9",
    "playwright>=1.49",
    "trafilatura>=2.0",
    "markdownify>=0.14",
    "httpx>=0.27",
    "pymupdf>=1.25",
    "duckduckgo-search>=7.0",
]

[project.scripts]
mcp-research-browser = "src.server:main"
```

SerpAPI is optional—if you set `SERPAPI_KEY`, it uses Google. Otherwise DuckDuckGo works without any API key.

---

## 6. Complete Research Workflow

Here's what a full research session looks like:

> **User:** "Do deep research on the current state of nuclear fusion energy in 2025."

Claude orchestrates this sequence:

```
1. start_research_session("Nuclear Fusion Energy 2025")
   → ✅ Session started

2. search_web("nuclear fusion energy breakthroughs 2025")
   → 5 results with URLs

3. research_url("https://example.com/fusion-article-1")
   → Summary (via Sampling) + tracked in session

4. research_url("https://example.com/fusion-article-2", focus="commercial viability")
   → Focused summary + tracked

5. research_pdf("https://example.com/fusion-paper.pdf")
   → PDF extracted and summarized + tracked

6. add_note("Key finding: NIF achieved ignition in Dec 2022...", source_url="...")
   → Note recorded

7. add_note("Commercial fusion timeline: most optimistic estimates...")
   → Note recorded

8. export_research()
   → Full Markdown report with citations
```

The exported report looks like:

```markdown
# Research Report: Nuclear Fusion Energy 2025
*Generated: 2025-01-15 14:30 UTC*

---

## Overview
- **Sources consulted:** 5
- **Notes recorded:** 3
- **Total content processed:** 45,000 words

## Findings

### Finding 1
Key finding: NIF achieved ignition...
*Source: https://example.com/fusion-article-1*

### Finding 2
Commercial fusion timeline...

## Sources

1. 🌐 [Fusion Energy Breakthroughs](https://example.com/...) — 8,500 words
2. 📄 [ITER Progress Report](https://example.com/paper.pdf) — 15,000 words
...
```

---

## 7. Testing

### Test 1: Web Search + Research

> "Search for 'MCP Model Context Protocol' and summarize the top 3 results."

### Test 2: PDF Research

> "Read this PDF and tell me the key findings: https://arxiv.org/pdf/some-paper.pdf"

### Test 3: Full Research Session

> Start research on "WebAssembly adoption in 2025". Search, browse 3 pages, take notes, and export a report.

---

## Key Takeaways

```
✅ Web search via SerpAPI (Google) or DuckDuckGo (free)
✅ PDF extraction with PyMuPDF handles research papers
✅ Research sessions track sources and citations automatically
✅ Export produces professional Markdown reports
✅ Sampling summarizes both web pages and PDFs
✅ Full research workflow: Search → Browse → Note → Export
```

---

## What's Next?

We've built three complete MCP projects:
1. **Database Analyst** (Blogs 5-6) — SQL with security
2. **DevOps First Responder** (Blogs 7-8) — K8s diagnostics and remediation
3. **Research Browser** (Blogs 9-11) — Web research with Sampling

All of them run locally via stdio transport. In **Blog 12: Production Deployment**, we'll take our servers to production:

- Docker containerization
- **Streamable HTTP transport** for remote access
- Authentication with API keys and OAuth
- Cloud deployment on fly.io and Railway
- Monitoring and health checks

---

## Quick Reference

### New Tools (Blog 11)

| Tool | Purpose |
|------|---------|
| `search_web` | Search the web for pages |
| `research_pdf` | Extract and summarize PDFs |
| `start_research_session` | Begin tracking research |
| `add_note` | Record a finding |
| `export_research` | Export as Markdown report |
| `end_research_session` | End active session |

### All Tools (Blogs 9-11)

| Tool | Blog | Sampling? |
|------|------|-----------|
| `browse_url` | 9 | No |
| `screenshot_page` | 9 | No |
| `search_in_page` | 9 | No |
| `get_cache_stats` | 9 | No |
| `clear_cache` | 9 | No |
| `research_url` | 10 | Yes |
| `search_web` | 11 | No |
| `research_pdf` | 11 | Yes |
| `start_research_session` | 11 | No |
| `add_note` | 11 | No |
| `export_research` | 11 | No |
| `end_research_session` | 11 | No |

---

| [← Blog 10: Deep Research Browser Part 2](../blog-10/blog.md) | [Blog 12: Production Deployment →](../blog-12/blog.md) |
|:---|---:|
