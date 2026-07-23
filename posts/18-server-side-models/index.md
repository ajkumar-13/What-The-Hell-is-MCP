# Blog 10: Deep Research Browser – Part 2
## Server-Side Summarization with MCP Sampling


> *"Here's the mind-bending part: What if the server could ask the LLM for help? That's MCP Sampling—and it solves the large content problem."*

---

## Introduction

In Blog 9, we built a browser that extracts clean content from web pages. But some pages are still 15,000+ words after extraction—far too much for effective LLM consumption.

The naive solution: truncate. But truncation throws away content blindly.

The elegant solution: **MCP Sampling**—where the *server* asks the *LLM* to summarize content before returning it to the conversation.

### Normal MCP Flow

```
User → Host/LLM → Client → Server → Data → Client → LLM → User
```

### With Sampling

```
User → Host/LLM → Client → Server
                              ↓
                        Server asks LLM:
                        "Summarize this chunk"
                              ↓
                        Client routes to LLM
                              ↓
                        LLM returns summary
                              ↓
                        Server uses summary
                              ↓
                   Processed result → Client → LLM → User
```

The server becomes an *intelligent data processor*, not just a data pipe.

---

## 1. How Sampling Works

Sampling is initiated by the **server** via the MCP session:

```python
from mcp.types import TextContent, SamplingMessage

# Inside a tool function, the server asks the LLM for help:
result = await ctx.session.create_message(
    messages=[
        SamplingMessage(
            role="user",
            content=TextContent(
                type="text",
                text="Summarize this content:\n\n" + large_content,
            ),
        )
    ],
    max_tokens=500,
)

# result.content contains the LLM's summary
summary = result.content.text
```

**Key points:**

| Aspect | Detail |
|--------|--------|
| Who initiates? | The server |
| Who controls the model? | The client / host |
| Human approval? | Recommended—hosts should confirm sampling requests |
| Model selection | Client decides which model to use |
| Capability check | Server must check if client supports sampling |

> ⚠️ **The host is in control.** The MCP specification states that hosts SHOULD prompt the user before allowing sampling. Not all hosts/clients support sampling—your server should handle the case where `create_message` is unavailable or declined.

---

## 2. Adding the Summarizer Module

```python
# src/summarizer.py
"""Server-side content summarization using MCP Sampling."""
import logging
import sys

from mcp.server.fastmcp import Context
from mcp.types import TextContent, SamplingMessage

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Max words per chunk for summarization
CHUNK_SIZE = 3000
# Threshold: only summarize if content exceeds this word count
SUMMARIZE_THRESHOLD = 2000


def _chunk_text(text: str, max_words: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks of approximately max_words."""
    words = text.split()
    chunks = []

    for i in range(0, len(words), max_words):
        chunk = " ".join(words[i : i + max_words])
        chunks.append(chunk)

    return chunks


async def summarize_content(
    ctx: Context,
    content: str,
    focus: str | None = None,
) -> str | None:
    """
    Use MCP Sampling to summarize content via the host's LLM.
    
    Args:
        ctx: MCP tool context (provides session access).
        content: The text content to summarize.
        focus: Optional focus area (e.g., "focus on security implications").
    
    Returns:
        Summarized text, or None if sampling is not available.
    """
    word_count = len(content.split())

    if word_count <= SUMMARIZE_THRESHOLD:
        logger.info("Content is %d words, below threshold. No summarization needed.", word_count)
        return None  # No need to summarize

    chunks = _chunk_text(content)
    logger.info(
        "Summarizing %d words in %d chunk(s)",
        word_count,
        len(chunks),
    )

    focus_instruction = ""
    if focus:
        focus_instruction = f" Focus on: {focus}."

    # Summarize each chunk
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        prompt = (
            f"Summarize the following content concisely. "
            f"Preserve key facts, data points, and conclusions.{focus_instruction}\n\n"
            f"--- CONTENT (chunk {i + 1}/{len(chunks)}) ---\n\n"
            f"{chunk}"
        )

        try:
            result = await ctx.session.create_message(
                messages=[
                    SamplingMessage(
                        role="user",
                        content=TextContent(type="text", text=prompt),
                    )
                ],
                max_tokens=800,
            )

            if hasattr(result.content, "text"):
                chunk_summaries.append(result.content.text)
                logger.info("Chunk %d/%d summarized", i + 1, len(chunks))
            else:
                logger.warning("Chunk %d returned non-text content", i + 1)
                chunk_summaries.append(f"(Chunk {i + 1}: non-text response)")

        except Exception as e:
            logger.warning("Sampling failed for chunk %d: %s", i + 1, e)
            # Fall back: include a truncated version of the chunk
            words = chunk.split()[:500]
            chunk_summaries.append(" ".join(words) + "... (truncated, sampling unavailable)")

    # If multiple chunks, aggregate the summaries
    if len(chunk_summaries) > 1:
        combined = "\n\n---\n\n".join(chunk_summaries)

        try:
            result = await ctx.session.create_message(
                messages=[
                    SamplingMessage(
                        role="user",
                        content=TextContent(
                            type="text",
                            text=(
                                f"Combine these {len(chunk_summaries)} section summaries "
                                f"into one coherent summary. Remove redundancy and "
                                f"maintain a logical flow.{focus_instruction}\n\n"
                                f"{combined}"
                            ),
                        ),
                    )
                ],
                max_tokens=1200,
            )

            if hasattr(result.content, "text"):
                logger.info("Final aggregated summary created")
                return result.content.text

        except Exception as e:
            logger.warning("Aggregation sampling failed: %s", e)
            # Fall back to concatenated summaries
            return combined

    return chunk_summaries[0] if chunk_summaries else None
```

### The Chunk → Summarize → Aggregate Pattern

```
┌──────────────────────────────────────────────────────────────┐
│              SAMPLING: CONTENT SUMMARIZATION                 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  15,000 words extracted from web page                        │
│       │                                                      │
│       ▼                                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Chunk 1  │ │ Chunk 2  │ │ Chunk 3  │ │ Chunk 4  │       │
│  │ 3000 wds │ │ 3000 wds │ │ 3000 wds │ │ 3000 wds │       │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘       │
│        │            │            │            │              │
│        ▼            ▼            ▼            ▼              │
│  ┌──────────────────────────────────────────────────┐       │
│  │            Sampling: "Summarize this"            │       │
│  │         (4 requests to the host's LLM)           │       │
│  └──────────────────────────────────────────────────┘       │
│        │            │            │            │              │
│        ▼            ▼            ▼            ▼              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │Summary 1 │ │Summary 2 │ │Summary 3 │ │Summary 4 │       │
│  │  200 wds │ │  200 wds │ │  200 wds │ │  200 wds │       │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘       │
│        │            │            │            │              │
│        └────────────┴─────┬──────┴────────────┘              │
│                           ▼                                  │
│  ┌──────────────────────────────────────────────────┐       │
│  │       Sampling: "Combine these summaries"        │       │
│  │           (1 more request to the LLM)            │       │
│  └──────────────────────────────────────────────────┘       │
│                           │                                  │
│                           ▼                                  │
│                  ┌─────────────────┐                        │
│                  │ Final Summary   │                        │
│                  │   ~400 words    │                        │
│                  └─────────────────┘                        │
│                                                              │
│  15,000 words → 400 words (97% reduction)                   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Updating the Server

We add a new `research_url` tool that combines browsing and sampling. Update `src/server.py`:

```python
# src/server.py — Updated with Sampling support
import json
import logging
import sys
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP

from .browser import browser
from .extractor import extract_content
from .cache import cache
from .summarizer import summarize_content, SUMMARIZE_THRESHOLD

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Launch browser on startup, close on shutdown."""
    await browser.start()
    logger.info("Research Browser MCP server ready (with Sampling)")
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

    Returns the full extracted content (may be very long for large pages).
    For automatic summarization of large pages, use research_url instead.

    Args:
        url: The web page URL to browse.
        use_cache: Use cached content if available (default: True).
    """
    if use_cache:
        cached = cache.get(url)
        if cached:
            return _format_content(cached, from_cache=True)

    try:
        page_result = await browser.fetch_page(url)
    except Exception as e:
        return f"❌ Failed to load page: {e}"

    extracted = extract_content(
        url=page_result.url,
        html=page_result.html,
        title=page_result.title,
    )
    cache.put(url, extracted)
    return _format_content(extracted, from_cache=False)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def research_url(
    ctx: Context,
    url: str,
    focus: str | None = None,
) -> str:
    """
    Browse a URL and return a smart summary.

    For short pages, returns the full content.
    For long pages, uses MCP Sampling to summarize via the host's LLM.

    This is the recommended tool for research workflows where you need
    to consume content from many pages efficiently.

    Args:
        url: The web page URL to research.
        focus: Optional focus for the summary (e.g., "security implications").
    """
    # Get or fetch content
    cached = cache.get(url)
    if cached:
        extracted = cached
    else:
        try:
            page_result = await browser.fetch_page(url)
        except Exception as e:
            return f"❌ Failed to load page: {e}"
        extracted = extract_content(
            url=page_result.url,
            html=page_result.html,
            title=page_result.title,
        )
        cache.put(url, extracted)

    # Short content: return as-is
    if extracted.word_count <= SUMMARIZE_THRESHOLD:
        return _format_content(extracted, from_cache=cached is not None)

    # Long content: summarize using Sampling
    logger.info(
        "Page %s has %d words, attempting Sampling summarization",
        url,
        extracted.word_count,
    )

    summary = await summarize_content(ctx, extracted.content, focus=focus)

    if summary:
        return (
            f"# {extracted.title}\n"
            f"**URL:** {url}\n"
            f"**Original:** {extracted.word_count} words → "
            f"**Summary:** {len(summary.split())} words\n"
            f"{'**Focus:** ' + focus if focus else ''}\n\n"
            f"---\n\n"
            f"{summary}\n\n"
            f"---\n*Summarized via MCP Sampling. "
            f"Use `browse_url` for full content.*"
        )

    # Sampling unavailable: fall back to truncated content
    logger.warning("Sampling unavailable, returning truncated content")
    truncated = " ".join(extracted.content.split()[:2000])
    return (
        f"# {extracted.title}\n"
        f"**URL:** {url}\n"
        f"**Words:** {extracted.word_count} (truncated to ~2000)\n\n"
        f"⚠️ *Sampling unavailable. Showing truncated content.*\n\n"
        f"---\n\n"
        f"{truncated}..."
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def screenshot_page(url: str, full_page: bool = False) -> list:
    """
    Take a screenshot of a web page.

    Args:
        url: The web page URL to screenshot.
        full_page: Capture the full scrollable page (default: viewport only).
    """
    try:
        result = await browser.take_screenshot(url, full_page=full_page)
    except Exception as e:
        return f"❌ Failed to screenshot: {e}"

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
    Search for text within a page's extracted content.

    Args:
        url: The web page URL to search.
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
        extracted = extract_content(
            url=page_result.url,
            html=page_result.html,
            title=page_result.title,
        )
        cache.put(url, extracted)

    query_lower = query.lower()
    paragraphs = extracted.content.split("\n\n")
    matches = [
        {"index": i, "text": p.strip()}
        for i, p in enumerate(paragraphs)
        if query_lower in p.lower()
    ]

    if not matches:
        return (
            f"No matches for '{query}' in {extracted.title} "
            f"({extracted.word_count} words)."
        )

    output = (
        f"**{len(matches)} match(es) for '{query}' "
        f"in [{extracted.title}]({url}):**\n\n"
    )
    for m in matches[:10]:
        output += f"---\n{m['text']}\n\n"
    return output


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
async def get_cache_stats() -> str:
    """Show cache statistics."""
    return json.dumps(cache.stats(), indent=2)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
    }
)
async def clear_cache() -> str:
    """Clear all cached page content."""
    cache.clear()
    return "✅ Cache cleared."


# ============ RESOURCE ============

@mcp.resource("browser://cache-stats")
async def cache_stats_resource() -> str:
    """Current cache statistics."""
    return json.dumps(cache.stats(), indent=2)


# ============ PROMPT ============

@mcp.prompt(title="Research Topic")
async def research_topic(topic: str, num_urls: int = 3) -> str:
    """Research a topic by browsing and summarizing multiple URLs."""
    return (
        f"# Research Task: {topic}\n\n"
        f"Research '{topic}' thoroughly.\n\n"
        f"## Instructions\n"
        f"1. Browse {num_urls} relevant URLs about this topic using `research_url`\n"
        f"2. Each page will be automatically summarized if large\n"
        f"3. Synthesize findings into a comprehensive summary\n"
        f"4. Include citations (URLs) for each source\n\n"
        f"## Available Tools\n"
        f"- `research_url(url, focus)` — Browse + auto-summarize (recommended)\n"
        f"- `browse_url(url)` — Full content (no summarization)\n"
        f"- `search_in_page(url, query)` — Search within a page\n"
        f"- `screenshot_page(url)` — Visual capture\n\n"
        f"Begin by finding relevant pages about: {topic}"
    )


# ============ HELPERS ============

def _format_content(content, from_cache: bool = False) -> str:
    """Format extracted content for the LLM."""
    cache_note = " (from cache)" if from_cache else ""
    size_warning = ""
    if content.word_count > 5000:
        size_warning = (
            f"\n\n⚠️ **Large page** ({content.word_count} words). "
            f"Consider `research_url` for automatic summarization or "
            f"`search_in_page` for targeted queries."
        )

    return (
        f"# {content.title}\n"
        f"**URL:** {content.url}\n"
        f"**Words:** {content.word_count} | "
        f"**Extraction:** {content.method}{cache_note}\n"
        f"{size_warning}\n\n---\n\n"
        f"{content.content}"
    )


# ============ ENTRY POINT ============

def main():
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
```

---

## 4. The `ctx` Parameter — How Sampling Reaches the Tool

Notice the `research_url` tool takes a `ctx: Context` parameter. FastMCP automatically injects this when the parameter is type-hinted as `Context`. This object gives tools access to:

- `ctx.session` — The MCP session (for Sampling calls)
- `ctx.request_context` — Request metadata

```python
@mcp.tool()
async def my_tool(ctx: Context, url: str) -> str:
    # ctx is injected by FastMCP, not passed by the caller
    # The LLM only sees the 'url' parameter
    result = await ctx.session.create_message(...)
```

The `ctx` parameter doesn't appear in the tool's input schema—it's invisible to the LLM.

---

## 5. Graceful Degradation

Not all hosts support Sampling. Our code handles this:

```python
# In summarizer.py — the try/except around create_message
try:
    result = await ctx.session.create_message(...)
except Exception as e:
    logger.warning("Sampling failed: %s", e)
    # Fall back to truncation
```

In `research_url`:

```python
summary = await summarize_content(ctx, content, focus=focus)

if summary:
    # Use the summary
    ...
else:
    # Sampling unavailable — truncate instead
    truncated = " ".join(content.split()[:2000])
    ...
```

**Degradation chain:**

| Scenario | Behavior |
|----------|----------|
| Host supports Sampling | Full summarization (best quality) |
| Sampling denied/unavailable | Truncated content with warning |
| Page small enough | Full content (no Sampling needed) |
| Page fetch fails | Error message |

---

## 6. Testing Sampling

### Claude Desktop Configuration

Claude Desktop supports Sampling. The server config is the same:

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

### Test 1: Short Page (No Sampling)

> "Research https://example.com"

A short page returns content directly—no Sampling triggered.

### Test 2: Long Page (Sampling Activates)

> "Research the Wikipedia article on machine learning"

Claude calls `research_url("https://en.wikipedia.org/wiki/Machine_learning")`. The article is ~15,000 words, so the server:
1. Splits into 5 chunks
2. Calls Sampling 5 times (summarize each)
3. Calls Sampling once more (aggregate)
4. Returns a ~400-word summary

### Test 3: Focused Summary

> "Research https://some-long-article.com and focus on the security implications"

The `focus` parameter guides the summarization:
```
Summarize the following content concisely. 
Preserve key facts, data points, and conclusions. 
Focus on: security implications.
```

---

## 7. When to Use Sampling

Sampling isn't just for summarization. Consider it whenever the server has data that benefits from LLM processing:

| Use Case | How |
|----------|-----|
| **Summarization** | Large content → concise summary |
| **Translation** | Server has foreign text → LLM translates |
| **Classification** | Server has data → LLM categorizes |
| **Extraction** | Server has unstructured text → LLM extracts structured data |
| **Sentiment** | Server has reviews → LLM scores sentiment |
| **Code Analysis** | Server has source code → LLM identifies patterns |

The pattern is always:
1. Server has data that needs intelligence
2. Server sends a Sampling request
3. LLM processes the data
4. Server uses the result

---

## 8. Sampling vs. Doing It Yourself

Why not just skip Sampling and let the main conversation's LLM handle summarization?

| Approach | Pros | Cons |
|----------|------|------|
| **No server processing** | Simple | Floods context window, expensive |
| **Server truncates** | Fast | Loses information |
| **Server uses Sampling** | Intelligent reduction, preserves key info | More complex, depends on host support |
| **Server calls external API** | Independent of host | Requires API key, cost, another dependency |

Sampling is the sweet spot: it uses the host's existing LLM capability without requiring the server to manage its own API keys or models.

---

## Key Takeaways

```
✅ Sampling = Server asking the host's LLM for help
✅ Chunk → Summarize → Aggregate for large content
✅ ctx.session.create_message() to initiate Sampling
✅ Always degrade gracefully when Sampling is unavailable
✅ Context parameter is invisible to the LLM caller
✅ 97% content reduction while preserving key information
✅ Host controls model selection and approval
```

---

## What's Next?

We have a browser that can fetch, extract, and summarize web pages. In **Blog 11: Deep Research Browser – Part 3**, we bring it all together into a complete research assistant:

- **Web search** integration (find pages to research)
- **PDF extraction** (research papers and documents)
- **Research sessions** (track sources, notes, citations)
- **Markdown export** (polished research reports)

---

## Quick Reference

### New Tool (Blog 10)

| Tool | Purpose | Uses Sampling? |
|------|---------|----------------|
| `research_url` | Browse + auto-summarize | Yes (for long pages) |

### Sampling API

```python
from mcp.types import TextContent, SamplingMessage

result = await ctx.session.create_message(
    messages=[
        SamplingMessage(
            role="user",
            content=TextContent(type="text", text="...prompt..."),
        )
    ],
    max_tokens=800,
)
summary = result.content.text
```

### Updated Dependencies

```toml
# Same as Blog 9 — Sampling uses the MCP SDK, no new dependencies
dependencies = [
    "mcp[cli]>=1.9",
    "playwright>=1.49",
    "trafilatura>=2.0",
    "markdownify>=0.14",
]
```

---

| [← Blog 9: Deep Research Browser Part 1](../blog-9/blog.md) | [Blog 11: Deep Research Browser Part 3 →](../blog-11/blog.md) |
|:---|---:|
