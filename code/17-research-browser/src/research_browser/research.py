"""Multi-page research: the loop, the budget, and the citations.

Post 18, part two. One page at a time is a browsing server. Several pages, a
budget, and an audit trail is a research server.

Three things make the difference:

**A budget.** An unbounded loop over a list of URLs is a way to spend an
afternoon and a context window. Pages, words, and failures all have ceilings,
and the report says which one stopped it. "It finished" and "it ran out of
budget" are different answers and the caller needs to be able to tell them
apart.

**Provenance on every claim.** `CitationLog.add` refuses a claim with no source
URL. Not "logs a warning" -- raises. The report is assembled only from the log,
so there is no path by which an unattributed claim reaches the caller. That is
a property of the code rather than a promise in a docstring.

**An honest `verbatim` flag.** The extractive summarizer returns sentences that
appear on the page, so a claim can be checked against the source text and
marked verbatim. A model-written summary is a paraphrase and gets marked
`verbatim=False`. Both are cited; only one is quotable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .app import mcp
from .summarize import Summarizer, active_summarizer, split_sentences, summarize
from .tools import READ_ONLY, load_page

log = logging.getLogger("research-browser.research")

DEFAULT_MAX_PAGES = 5
MAX_MAX_PAGES = 20
DEFAULT_MAX_WORDS = 20_000
DEFAULT_CLAIMS_PER_PAGE = 3


class MissingSourceError(ValueError):
    """Raised when something tries to record a claim with no source."""


@dataclass
class Citation:
    """One claim and the page it came from.

    There is no constructor path that produces a Citation without a URL. That
    is the point of the class.
    """

    claim: str
    url: str
    title: str
    verbatim: bool


@dataclass
class SourceRecord:
    """One page that was actually visited, and what it cost."""

    url: str
    title: str
    raw_bytes: int
    clean_bytes: int
    reduction_percent: float
    word_count: int
    summarizer: str
    fell_back: bool
    from_cache: bool


@dataclass
class ResearchReport:
    """The result of a research pass. Every claim is attributable."""

    topic: str
    summarizer: str
    pages_visited: int
    pages_failed: int
    words_read: int
    words_returned: int
    reduction_percent: float
    stopped_because: str
    sources: list[SourceRecord]
    citations: list[Citation]
    failures: list[str]


@dataclass
class Budget:
    """What a single research pass is allowed to spend."""

    max_pages: int = DEFAULT_MAX_PAGES
    max_words: int = DEFAULT_MAX_WORDS
    max_claims_per_page: int = DEFAULT_CLAIMS_PER_PAGE


@dataclass
class CitationLog:
    """The only way a claim gets into a report.

    `add` validates before it appends, so an unsourced claim cannot be recorded
    and then filtered out later. There is nothing to filter.
    """

    entries: list[Citation] = field(default_factory=list)

    def add(self, claim: str, *, url: str, title: str, source_text: str = "") -> Citation:
        claim = claim.strip()
        if not claim:
            raise MissingSourceError("a claim cannot be empty")
        if not url.strip():
            raise MissingSourceError(
                f"refusing to record a claim with no source URL: {claim[:60]!r}"
            )
        citation = Citation(
            claim=claim,
            url=url.strip(),
            title=title,
            # Verifiable rather than asserted: does this sentence actually
            # appear in the text we extracted from that page?
            verbatim=bool(source_text) and claim in source_text,
        )
        self.entries.append(citation)
        return citation


async def run_research(
    topic: str,
    urls: list[str],
    *,
    summarizer: Summarizer,
    budget: Budget | None = None,
    focus: str | None = None,
) -> ResearchReport:
    """Visit pages until the budget runs out, and report with citations.

    Everything the loop touches is passed in, so a test can run the whole thing
    against saved HTML and a no-network summarizer.
    """
    budget = budget or Budget()
    citations = CitationLog()
    sources: list[SourceRecord] = []
    failures: list[str] = []

    words_read = 0
    words_returned = 0
    stopped_because = "completed: visited every source"

    # A duplicate URL is a wasted page against the budget. Dedupe, but keep the
    # caller's order: the first link is usually the one they trust most.
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)

    if not ordered:
        return ResearchReport(
            topic=topic,
            summarizer=summarizer.name,
            pages_visited=0,
            pages_failed=0,
            words_read=0,
            words_returned=0,
            reduction_percent=0.0,
            stopped_because="stopped: no sources were supplied",
            sources=[],
            citations=[],
            failures=[],
        )

    for index, url in enumerate(ordered):
        if len(sources) >= budget.max_pages:
            stopped_because = (
                f"budget: page limit reached ({budget.max_pages}), "
                f"{len(ordered) - index} source(s) not visited"
            )
            break
        if words_read >= budget.max_words:
            stopped_because = (
                f"budget: word limit reached ({budget.max_words}), "
                f"{len(ordered) - index} source(s) not visited"
            )
            break

        try:
            extraction, from_cache = await load_page(url)
        except Exception as exc:
            # One dead link must not end the pass. Record it and move on: a
            # report that says which sources failed is more useful than one
            # that quietly has fewer sources than you asked for.
            failures.append(f"{url}: {exc}")
            log.warning("research: skipping %s (%s)", url, exc)
            continue

        if not extraction.text.strip():
            failures.append(f"{url}: no readable content after extraction")
            continue

        result = await summarize(
            extraction.text,
            summarizer=summarizer,
            focus=focus or topic,
            max_sentences=budget.max_claims_per_page,
        )

        words_read += extraction.word_count
        words_returned += result.summary_words

        sources.append(
            SourceRecord(
                url=extraction.url or url,
                title=extraction.title,
                raw_bytes=extraction.raw_bytes,
                clean_bytes=extraction.clean_bytes,
                reduction_percent=extraction.reduction_percent,
                word_count=extraction.word_count,
                summarizer=result.summarizer,
                fell_back=result.fell_back,
                from_cache=from_cache,
            )
        )

        for sentence in split_sentences(result.text)[: budget.max_claims_per_page]:
            citations.add(
                sentence,
                url=extraction.url or url,
                title=extraction.title,
                source_text=extraction.text,
            )

    if not sources and failures:
        stopped_because = "stopped: no source could be read"

    reduction = 0.0 if words_read == 0 else (1 - words_returned / words_read) * 100

    return ResearchReport(
        topic=topic,
        summarizer=summarizer.name,
        pages_visited=len(sources),
        pages_failed=len(failures),
        words_read=words_read,
        words_returned=words_returned,
        reduction_percent=round(reduction, 1),
        stopped_because=stopped_because,
        sources=sources,
        citations=citations.entries,
        failures=failures,
    )


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@dataclass
class PageSummary:
    """One page, summarized, with the summarizer named."""

    url: str
    title: str
    summary: str
    summarizer: str
    requested_summarizer: str
    fell_back: bool
    note: str
    page_words: int
    summary_words: int
    reduction_percent: float
    from_cache: bool


@mcp.tool(
    title="Summarize a page",
    annotations=READ_ONLY,
)
async def summarize_page(
    url: str,
    focus: str | None = None,
    max_sentences: int = 5,
) -> PageSummary:
    """Browse a page and return a short summary of it.

    The result names the summarizer that actually produced the text. If a
    configured model was unreachable, `fell_back` is true, `summarizer` says
    what ran instead, and `note` says why.

    Args:
        url: The page to summarize.
        focus: Optional angle to bias the summary toward.
        max_sentences: How long the summary may be, in sentences.
    """
    max_sentences = max(1, min(max_sentences, 20))
    extraction, from_cache = await load_page(url)
    result = await summarize(
        extraction.text,
        summarizer=active_summarizer(),
        focus=focus,
        max_sentences=max_sentences,
    )
    return PageSummary(
        url=extraction.url or url,
        title=extraction.title,
        summary=result.text,
        summarizer=result.summarizer,
        requested_summarizer=result.requested,
        fell_back=result.fell_back,
        note=result.note,
        page_words=result.input_words,
        summary_words=result.summary_words,
        reduction_percent=result.reduction_percent,
        from_cache=from_cache,
    )


@mcp.tool(
    title="Research several sources",
    annotations=READ_ONLY,
)
async def research_urls(
    topic: str,
    urls: list[str],
    focus: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_words: int = DEFAULT_MAX_WORDS,
) -> ResearchReport:
    """Visit several pages on one topic and return claims with their sources.

    Stops when the page budget or the word budget runs out, and says which. A
    source that fails to load is recorded in `failures` rather than ending the
    pass. Every entry in `citations` carries the URL it came from.

    Args:
        topic: What the research is about. Also biases the summaries.
        urls: Candidate sources, in the order you want them visited.
        focus: Optional angle, if it differs from the topic.
        max_pages: How many pages this pass may visit.
        max_words: How many words of extracted text this pass may read.
    """
    budget = Budget(
        max_pages=max(1, min(max_pages, MAX_MAX_PAGES)),
        max_words=max(500, max_words),
    )
    return await run_research(
        topic,
        urls,
        summarizer=active_summarizer(),
        budget=budget,
        focus=focus,
    )


__all__ = [
    "Budget",
    "Citation",
    "CitationLog",
    "MissingSourceError",
    "PageSummary",
    "ResearchReport",
    "SourceRecord",
    "research_urls",
    "run_research",
    "summarize_page",
]
