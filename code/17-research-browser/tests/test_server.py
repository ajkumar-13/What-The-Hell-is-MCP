"""In-memory protocol tests.

`Client(mcp)` connects straight to the server object with no subprocess and no
socket, so these run in milliseconds and still go through the real protocol
machinery: schemas, validation, result shapes, and content-block types.

Note the shape of every test: the client is opened with `async with` inside the
test body, not handed over by a yield fixture. The client owns an anyio task
group, and a task group has to be exited by the same task that entered it. A
yield fixture tears down in a different task and you get "Attempted to exit
cancel scope in a different task" on every test.
"""

from __future__ import annotations

import base64
import pathlib

import pytest
from mcp import Client

from conftest import ARTICLE_URL, MISSING_URL, PORTAL_URL, SPA_URL, FixtureFetcher
from research_browser import mcp
from research_browser.browser import browser
from research_browser.research import (
    Budget,
    CitationLog,
    MissingSourceError,
    run_research,
)
from research_browser.summarize import ExtractiveSummarizer, split_sentences

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

# Emoji, dingbats, pictographs, and arrows. The first edition of this server
# returned strings full of them; a model gains nothing from them and a terminal
# host renders half of them as boxes. Codepoint ranges rather than literals, so
# that this file stays readable in an editor that does not have the fonts.
EMOJI_RANGES = (
    (0x2190, 0x21FF),  # arrows
    (0x2300, 0x23FF),  # miscellaneous technical
    (0x2460, 0x24FF),  # enclosed alphanumerics
    (0x25A0, 0x27BF),  # geometric shapes, miscellaneous symbols, dingbats
    (0x2B00, 0x2BFF),  # miscellaneous symbols and arrows
    (0xFE0F, 0xFE0F),  # variation selector 16
    (0x1F000, 0x1FAFF),  # the emoji planes
)


def find_emoji(text: str) -> str | None:
    """Return the first emoji-ish character in `text`, or None."""
    for character in text:
        code = ord(character)
        if any(low <= code <= high for low, high in EMOJI_RANGES):
            return character
    return None


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


async def test_every_tool_publishes_an_output_schema() -> None:
    """A missing output schema is a silent failure, so assert on it.

    `screenshot_page` is the interesting case: it returns a hand-built
    CallToolResult so it can emit an image block, and still publishes a schema
    because its return is annotated `Annotated[CallToolResult, ScreenshotMeta]`.
    """
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def test_every_tool_declares_annotations() -> None:
    async with Client(mcp) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.read_only_hint is not None, tool.name


async def test_the_only_non_read_only_tool_is_the_one_that_clears_the_cache() -> None:
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}

    writers = [n for n, t in tools.items() if t.annotations.read_only_hint is False]
    assert writers == ["clear_cache"]
    assert tools["clear_cache"].annotations.destructive_hint is True


async def test_the_expected_surface_is_registered() -> None:
    async with Client(mcp) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        resources = {str(r.uri) for r in (await c.list_resources()).resources}
        prompts = {p.name for p in (await c.list_prompts()).prompts}

    assert tools == {
        "browse_url",
        "screenshot_page",
        "search_in_page",
        "get_cache_stats",
        "clear_cache",
        "summarize_page",
        "research_urls",
    }
    assert "browser://cache-stats" in resources
    assert "research_topic" in prompts


# --------------------------------------------------------------------------
# Post 17: browsing and the size reduction
# --------------------------------------------------------------------------


async def test_browse_url_returns_the_article_and_the_receipt() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool("browse_url", {"url": ARTICLE_URL})

    assert result.result_type == "complete"
    data = result.structured_content
    assert data["method"] == "trafilatura"
    assert data["raw_bytes"] > 70_000
    assert data["clean_bytes"] < 6_000
    assert data["reduction_percent"] > 90
    assert "Where the bytes actually go" in data["text"]
    assert data["from_cache"] is False


async def test_browse_url_caps_what_it_returns_and_says_so() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "browse_url", {"url": ARTICLE_URL, "max_words": 100}
        )

    data = result.structured_content
    assert data["truncated"] is True
    assert data["returned_words"] <= 100
    assert data["word_count"] > 100


async def test_the_second_browse_of_a_url_is_served_from_the_cache() -> None:
    async with Client(mcp) as c:
        first = await c.call_tool("browse_url", {"url": ARTICLE_URL})
        second = await c.call_tool("browse_url", {"url": ARTICLE_URL})

        assert first.structured_content["from_cache"] is False
        assert second.structured_content["from_cache"] is True
        # One navigation, two calls.
        assert browser.require().fetched == [ARTICLE_URL]


async def test_use_cache_false_forces_a_fresh_fetch() -> None:
    async with Client(mcp) as c:
        await c.call_tool("browse_url", {"url": ARTICLE_URL})
        result = await c.call_tool(
            "browse_url", {"url": ARTICLE_URL, "use_cache": False}
        )

        assert result.structured_content["from_cache"] is False
        assert browser.require().fetched == [ARTICLE_URL, ARTICLE_URL]


async def test_a_page_that_will_not_load_is_an_error_not_a_success() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool("browse_url", {"url": MISSING_URL})

    assert result.is_error is True
    assert "could not load" in result.content[0].text


async def test_a_javascript_page_is_worth_a_browser() -> None:
    """The same URL, fetched two ways, through the real tool.

    With rendering off the fetcher behaves like `requests.get`: it returns the
    shell the server sent. With rendering on it returns what a reader sees. The
    gap between the two word counts is the argument for Playwright.
    """
    browser.use(FixtureFetcher(render=False))
    async with Client(mcp) as c:
        fetched = (await c.call_tool("browse_url", {"url": SPA_URL})).structured_content

    browser.use(FixtureFetcher(render=True))
    async with Client(mcp) as c:
        rendered = (
            await c.call_tool("browse_url", {"url": SPA_URL})
        ).structured_content

    assert fetched["raw_bytes"] > 40_000
    assert fetched["word_count"] < 15
    assert rendered["word_count"] > 200
    assert "Every page in this study" in rendered["text"]


async def test_search_in_page_returns_only_matching_paragraphs() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "search_in_page", {"url": ARTICLE_URL, "query": "extractor"}
        )

    data = result.structured_content
    assert data["total_matches"] >= 1
    for match in data["matches"]:
        assert "extractor" in match["text"].lower()


async def test_search_in_page_reports_a_clean_miss() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "search_in_page", {"url": ARTICLE_URL, "query": "kubernetes"}
        )

    data = result.structured_content
    assert data["total_matches"] == 0
    assert data["matches"] == []
    assert data["word_count"] > 0


# --------------------------------------------------------------------------
# Post 17: screenshots come back as images
# --------------------------------------------------------------------------


async def test_screenshot_returns_a_real_image_content_block() -> None:
    """The defect this replaces returned a list of dicts.

    The SDK serialized that to JSON inside a text block, so the host had a few
    kilobytes of base64 prose and no picture. An image needs an image block.
    """
    async with Client(mcp) as c:
        result = await c.call_tool("screenshot_page", {"url": ARTICLE_URL})

    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image"]

    image = result.content[1]
    assert image.mime_type == "image/png"
    # Decodes, and decodes to an actual PNG.
    assert base64.b64decode(image.data).startswith(b"\x89PNG\r\n\x1a\n")

    # No base64 leaked into the text block.
    assert "base64" not in result.content[0].text
    assert image.data not in result.content[0].text


async def test_screenshot_also_publishes_structured_metadata() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "screenshot_page", {"url": ARTICLE_URL, "full_page": True}
        )

    data = result.structured_content
    assert data["url"] == ARTICLE_URL
    assert data["width"] == 1280
    assert data["full_page"] is True
    assert data["png_bytes"] > 0
    # The pixels are not duplicated into the structured result.
    assert "data" not in data


async def test_a_screenshot_that_fails_is_an_error() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool("screenshot_page", {"url": MISSING_URL})

    assert result.is_error is True
    assert "could not screenshot" in result.content[0].text


# --------------------------------------------------------------------------
# Post 17: the cache tools, resource, and prompt
# --------------------------------------------------------------------------


async def test_cache_stats_reflect_real_activity() -> None:
    async with Client(mcp) as c:
        await c.call_tool("browse_url", {"url": ARTICLE_URL})
        await c.call_tool("browse_url", {"url": ARTICLE_URL})
        stats = (await c.call_tool("get_cache_stats", {})).structured_content

    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["max_entries"] > 0


async def test_clear_cache_reports_what_it_removed() -> None:
    async with Client(mcp) as c:
        await c.call_tool("browse_url", {"url": ARTICLE_URL})
        cleared = (await c.call_tool("clear_cache", {})).structured_content
        stats = (await c.call_tool("get_cache_stats", {})).structured_content

    assert cleared["entries_removed"] == 1
    assert stats["entries"] == 0


async def test_the_cache_resource_serves_the_same_numbers() -> None:
    async with Client(mcp) as c:
        await c.call_tool("browse_url", {"url": ARTICLE_URL})
        result = await c.read_resource("browser://cache-stats")

    assert result.contents[0].mime_type == "application/json"
    assert '"entries": 1' in result.contents[0].text


async def test_the_research_prompt_names_the_topic_and_demands_citations() -> None:
    async with Client(mcp) as c:
        # Prompt arguments are strings on the wire; the SDK coerces them to the
        # handler's annotated types.
        result = await c.get_prompt(
            "research_topic", {"topic": "data portability", "sources": "4"}
        )

    text = result.messages[0].content.text
    assert "data portability" in text
    assert "at least 4" in text
    assert "every claim carries the URL it came from" in text


# --------------------------------------------------------------------------
# Post 18: summarizing one page
# --------------------------------------------------------------------------


async def test_summarize_page_names_the_summarizer_that_actually_ran() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "summarize_page", {"url": ARTICLE_URL, "max_sentences": 3}
        )

    data = result.structured_content
    assert data["summarizer"] == "extractive"
    assert data["requested_summarizer"] == "extractive"
    assert data["fell_back"] is False
    assert data["note"] == ""
    assert data["summary_words"] < data["page_words"]
    assert data["reduction_percent"] > 50


async def test_summarize_page_returns_sentences_that_are_on_the_page() -> None:
    async with Client(mcp) as c:
        page = await c.call_tool("browse_url", {"url": ARTICLE_URL})
        summary = await c.call_tool("summarize_page", {"url": ARTICLE_URL})

    body = page.structured_content["text"]
    for sentence in split_sentences(summary.structured_content["summary"]):
        assert sentence in body


# --------------------------------------------------------------------------
# Post 18: multi-page research, citations, and budgets
# --------------------------------------------------------------------------


async def test_research_returns_a_citation_for_every_claim() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {"topic": "page weight", "urls": [ARTICLE_URL, PORTAL_URL]},
        )

    report = result.structured_content
    assert report["pages_visited"] == 2
    assert report["citations"], "a research pass with no citations is not a report"

    visited = {source["url"] for source in report["sources"]}
    for citation in report["citations"]:
        assert citation["url"] in visited
        assert citation["claim"].strip()


async def test_extractive_claims_are_marked_verbatim() -> None:
    """An extractive claim is quotable; a paraphrase would not be."""
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls", {"topic": "page weight", "urls": [ARTICLE_URL]}
        )

    citations = result.structured_content["citations"]
    assert citations
    assert all(c["verbatim"] for c in citations)


async def test_research_reports_the_reduction_across_all_sources() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {"topic": "page weight", "urls": [ARTICLE_URL, PORTAL_URL, SPA_URL]},
        )

    report = result.structured_content
    assert report["words_read"] > report["words_returned"]
    assert report["reduction_percent"] > 50
    for source in report["sources"]:
        assert source["raw_bytes"] > source["clean_bytes"]
        assert source["summarizer"] == "extractive"
        assert source["fell_back"] is False


async def test_the_page_budget_stops_the_loop_and_says_so() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {
                "topic": "page weight",
                "urls": [ARTICLE_URL, PORTAL_URL, SPA_URL],
                "max_pages": 2,
            },
        )

    report = result.structured_content
    assert report["pages_visited"] == 2
    assert "page limit reached" in report["stopped_because"]


async def test_the_word_budget_stops_the_loop_and_says_so() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {
                "topic": "page weight",
                "urls": [ARTICLE_URL, PORTAL_URL, SPA_URL],
                "max_words": 500,
            },
        )

    report = result.structured_content
    assert report["pages_visited"] < 3
    assert "word limit reached" in report["stopped_because"]


async def test_a_dead_link_is_recorded_rather_than_ending_the_pass() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {"topic": "page weight", "urls": [MISSING_URL, ARTICLE_URL]},
        )

    report = result.structured_content
    assert report["pages_visited"] == 1
    assert report["pages_failed"] == 1
    assert any(MISSING_URL in failure for failure in report["failures"])


async def test_duplicate_urls_do_not_spend_the_budget_twice() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls",
            {"topic": "page weight", "urls": [ARTICLE_URL, ARTICLE_URL, ARTICLE_URL]},
        )

    assert result.structured_content["pages_visited"] == 1


async def test_research_with_no_sources_says_so_instead_of_inventing_any() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool(
            "research_urls", {"topic": "page weight", "urls": []}
        )

    report = result.structured_content
    assert report["citations"] == []
    assert report["sources"] == []
    assert "no sources" in report["stopped_because"]


# --------------------------------------------------------------------------
# Post 18: provenance is enforced, not requested
# --------------------------------------------------------------------------


def test_a_claim_with_no_source_cannot_be_recorded() -> None:
    log = CitationLog()

    with pytest.raises(MissingSourceError):
        log.add("Page weight is mostly boilerplate.", url="", title="Somewhere")
    with pytest.raises(MissingSourceError):
        log.add("Page weight is mostly boilerplate.", url="   ", title="Somewhere")

    assert log.entries == []


def test_an_empty_claim_cannot_be_recorded() -> None:
    log = CitationLog()

    with pytest.raises(MissingSourceError):
        log.add("   ", url="https://example.com/a", title="A")

    assert log.entries == []


def test_a_claim_that_is_not_in_the_source_is_not_marked_verbatim() -> None:
    log = CitationLog()

    citation = log.add(
        "A paraphrase a model might produce.",
        url="https://example.com/a",
        title="A",
        source_text="The page said something else entirely.",
    )

    assert citation.verbatim is False


async def test_the_report_carries_no_claim_without_a_url() -> None:
    report = await run_research(
        "page weight",
        [ARTICLE_URL, PORTAL_URL],
        summarizer=ExtractiveSummarizer(),
        budget=Budget(max_pages=2),
    )

    assert report.citations
    assert all(c.url for c in report.citations)


# --------------------------------------------------------------------------
# Post 17 defect: no emoji anywhere
# --------------------------------------------------------------------------


def test_no_emoji_in_any_source_file() -> None:
    """Cheapest possible regression test for the tidiest possible defect."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            found = find_emoji(line)
            if found is not None:
                offenders.append(f"{path.name}:{number}: U+{ord(found):04X}")
    assert offenders == []


async def test_no_emoji_in_anything_a_tool_returns() -> None:
    async with Client(mcp) as c:
        results = [
            await c.call_tool("browse_url", {"url": ARTICLE_URL}),
            await c.call_tool("screenshot_page", {"url": ARTICLE_URL}),
            await c.call_tool(
                "search_in_page", {"url": ARTICLE_URL, "query": "bytes"}
            ),
            await c.call_tool("summarize_page", {"url": PORTAL_URL}),
            await c.call_tool(
                "research_urls",
                {"topic": "page weight", "urls": [ARTICLE_URL, MISSING_URL]},
            ),
            await c.call_tool("get_cache_stats", {}),
            await c.call_tool("clear_cache", {}),
        ]
        prompt = await c.get_prompt("research_topic", {"topic": "page weight"})
        resource = await c.read_resource("browser://cache-stats")

    texts: list[str] = [prompt.messages[0].content.text, resource.contents[0].text]
    for result in results:
        for block in result.content:
            if block.type == "text":
                texts.append(block.text)

    for text in texts:
        found = find_emoji(text)
        assert found is None, f"U+{ord(found):04X} in {text[:80]!r}"
