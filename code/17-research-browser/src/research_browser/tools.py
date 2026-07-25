"""Browsing tools, the cache resource, and the research prompt.

Post 17. Five tools, one resource, one prompt. The interesting one is
`screenshot_page`: a screenshot is an image, so it comes back as an **image
content block**, not as a dict of base64 serialized into a text block. A client
that receives the latter cannot show the user a picture, and the model gets a
few thousand tokens of base64 instead of something it can look at.

Getting both an image block and a structured result takes one trick, and it is
worth knowing: annotate the return as `Annotated[CallToolResult, Meta]`. The
SDK reads the `CallToolResult` arm as "the handler owns the content blocks" and
the second arm as "derive the output schema from this". You hand back the
blocks you want and the structured metadata is still published and validated.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.prompts.base import Message, UserMessage
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations

from .app import mcp
from .browser import browser
from .cache import CacheStats, cache
from .extract import Extraction, extract

# The default ceiling on how much text one `browse_url` call may return. Post 17
# spends its whole length on why the page had to shrink; handing back a 15,000
# word article would undo that.
DEFAULT_MAX_WORDS = 4_000
MAX_MAX_WORDS = 20_000

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

# Clearing the cache is not read-only and not reversible, so it says so. The
# annotation is a hint for the host, not enforcement -- nothing stops a badly
# written tool from lying. The enforcement is that these functions genuinely do
# what they claim.
CACHE_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass
class PageContent:
    """One browsed page: the clean text plus what it cost to get there."""

    url: str
    title: str
    text: str
    method: str
    raw_bytes: int
    clean_bytes: int
    reduction_percent: float
    word_count: int
    returned_words: int
    truncated: bool
    from_cache: bool


@dataclass
class ScreenshotMeta:
    """Structured metadata for a screenshot. The pixels ride in an image block."""

    url: str
    width: int
    height: int
    png_bytes: int
    full_page: bool


@dataclass
class SearchMatch:
    """One paragraph that matched the query."""

    paragraph_index: int
    text: str


@dataclass
class SearchResult:
    """The result of searching within one page."""

    url: str
    title: str
    query: str
    total_matches: int
    returned: int
    matches: list[SearchMatch]
    word_count: int
    from_cache: bool


@dataclass
class CacheCleared:
    """What `clear_cache` removed."""

    entries_removed: int


async def load_page(url: str, *, use_cache: bool = True) -> tuple[Extraction, bool]:
    """Fetch, extract, cache. The pipeline every browsing tool runs.

    Returns the extraction and whether it came from the cache.
    """
    if use_cache:
        cached = cache.get(url)
        if cached is not None:
            return cached, True

    try:
        page = await browser.require().fetch(url)
    except Exception as exc:
        # The SDK turns this into an error result the model can read and react
        # to, which beats a stack trace or a success-shaped result with an
        # error message hidden in a text field.
        raise ToolError(f"could not load {url}: {exc}") from exc

    extraction = extract(page.html, url=page.url, title=page.title)
    cache.put(url, extraction)
    return extraction, False


@mcp.tool(
    title="Browse a URL",
    annotations=READ_ONLY,
)
async def browse_url(
    url: str,
    use_cache: bool = True,
    max_words: int = DEFAULT_MAX_WORDS,
) -> PageContent:
    """Load a page in a headless browser and return the article text.

    Renders JavaScript, then strips navigation, ads, scripts, and styling. The
    result reports how many bytes went in and how many came out, so you can see
    what was discarded.

    Args:
        url: The page to browse.
        use_cache: Reuse a recently extracted copy if one is cached.
        max_words: Cap on returned words. Clamped to a sane range.
    """
    max_words = max(100, min(max_words, MAX_MAX_WORDS))
    extraction, from_cache = await load_page(url, use_cache=use_cache)

    words = extraction.text.split()
    truncated = len(words) > max_words
    text = " ".join(words[:max_words]) if truncated else extraction.text

    return PageContent(
        url=extraction.url or url,
        title=extraction.title,
        text=text,
        method=extraction.method,
        raw_bytes=extraction.raw_bytes,
        clean_bytes=extraction.clean_bytes,
        reduction_percent=extraction.reduction_percent,
        word_count=extraction.word_count,
        returned_words=len(text.split()),
        truncated=truncated,
        from_cache=from_cache,
    )


@mcp.tool(
    title="Screenshot a page",
    annotations=READ_ONLY,
)
async def screenshot_page(
    url: str,
    full_page: bool = False,
) -> Annotated[CallToolResult, ScreenshotMeta]:
    """Capture a page as a PNG image.

    Use this to see layout, verify that a page rendered, or read something that
    only exists as pixels. The image comes back as an image content block, so a
    host can display it inline.

    Args:
        url: The page to capture.
        full_page: Capture the whole scrollable page instead of the viewport.
    """
    try:
        shot = await browser.require().screenshot(url, full_page=full_page)
    except Exception as exc:
        raise ToolError(f"could not screenshot {url}: {exc}") from exc

    meta = ScreenshotMeta(
        url=shot.url,
        width=shot.width,
        height=shot.height,
        png_bytes=len(shot.png),
        full_page=full_page,
    )

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=f"Screenshot of {shot.url} at {shot.width}x{shot.height}.",
            ),
            ImageContent(
                type="image",
                data=base64.b64encode(shot.png).decode("ascii"),
                mime_type="image/png",
            ),
        ],
        structured_content=asdict(meta),
    )


@mcp.tool(
    title="Search within a page",
    annotations=READ_ONLY,
)
async def search_in_page(
    url: str,
    query: str,
    max_matches: int = 10,
) -> SearchResult:
    """Find paragraphs on a page that mention a phrase.

    Cheaper than reading the whole page when you already know what you are
    looking for. Uses the cached extraction when one is available.

    Args:
        url: The page to search.
        query: Text to look for, case-insensitively.
        max_matches: How many matching paragraphs to return, at most.
    """
    max_matches = max(1, min(max_matches, 50))
    extraction, from_cache = await load_page(url)

    needle = query.strip().lower()
    paragraphs = [p.strip() for p in extraction.text.split("\n\n") if p.strip()]
    matches = [
        SearchMatch(paragraph_index=index, text=paragraph)
        for index, paragraph in enumerate(paragraphs)
        if needle and needle in paragraph.lower()
    ]

    return SearchResult(
        url=extraction.url or url,
        title=extraction.title,
        query=query,
        total_matches=len(matches),
        returned=min(len(matches), max_matches),
        matches=matches[:max_matches],
        word_count=extraction.word_count,
        from_cache=from_cache,
    )


@mcp.tool(
    title="Get cache statistics",
    annotations=READ_ONLY,
)
def get_cache_stats() -> CacheStats:
    """Report how many pages are cached, how many are stale, and the hit rate."""
    return cache.stats()


@mcp.tool(
    title="Clear the page cache",
    annotations=CACHE_WRITE,
)
def clear_cache() -> CacheCleared:
    """Drop every cached page so the next browse fetches fresh content."""
    return CacheCleared(entries_removed=cache.clear())


@mcp.resource(
    "browser://cache-stats",
    title="Page cache statistics",
    mime_type="application/json",
)
def cache_stats_resource() -> str:
    """The same numbers as `get_cache_stats`, for a user to pin into context.

    A tool is called by the model when it decides it needs something. A resource
    is read by the application, usually because a user attached it. The same
    data can legitimately be both.
    """
    return json.dumps(asdict(cache.stats()), indent=2)


@mcp.prompt(
    title="Research a topic",
    description="Start a multi-source research pass with citations.",
)
def research_topic(topic: str, sources: int = 3) -> list[Message]:
    """Build an opening message that sets the rules of the research pass."""
    sources = max(1, min(sources, 10))
    return [
        UserMessage(
            f"Research this topic: {topic}\n\n"
            f"Work through at least {sources} independent sources.\n\n"
            "How to use this server:\n"
            "- `research_urls(topic, urls)` visits several pages, summarizes "
            "each, and returns claims with the URL each one came from. Start "
            "here when you already have candidate links.\n"
            "- `browse_url(url)` returns one page's article text.\n"
            "- `search_in_page(url, query)` is cheaper than reading a whole "
            "page when you know the phrase you want.\n"
            "- `screenshot_page(url)` when the answer is visual.\n\n"
            "Rules for the write-up: every claim carries the URL it came from. "
            "If you cannot attribute a claim to a page you actually visited, "
            "leave it out and say what is missing."
        )
    ]
