"""Post 18, sections 6, 7, 8 and 11: the two new tools, the research loop, its budget and its citations.

Run from the series root:
    uv run python posts/18-server-side-models/snippets/research_runs.py

Imports the research browser (code/17-research-browser/) in memory and installs the
FixtureFetcher of its tests/conftest.py, so every page is one of the three committed
HTML fixtures and nothing launches a browser or touches the network. Then, over an
in-memory Client, it prints the published surface of summarize_page and research_urls,
one summarize_page result, a research pass over the two article fixtures, the same
pass stopped by each budget, a dead link, and a pass with a provider summarizer whose
client is a local stub, so the label in the result changes without any key or provider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

from conftest import ARTICLE_URL, MISSING_URL, PORTAL_URL, SPA_URL, FixtureFetcher  # noqa: E402
from mcp import Client  # noqa: E402

from research_browser import mcp  # noqa: E402
from research_browser.browser import browser  # noqa: E402
from research_browser.cache import cache  # noqa: E402
from research_browser.summarize import AnthropicSummarizer, ExtractiveSummarizer, use_summarizer  # noqa: E402

logging.disable(logging.WARNING)  # the dead link logs a warning; the report below says the same


class StubBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class StubMessages:
    """Stands in for a provider client's messages API. Nothing leaves the process."""

    async def create(self, **kwargs: object) -> object:
        reply = "Most of a page's bytes are markup the reader never sees."
        return type("Message", (), {"content": [StubBlock(reply)]})()


class StubClient:
    messages = StubMessages()


def fresh() -> None:
    browser.use(FixtureFetcher())
    cache.clear()
    cache.reset_stats()


def header(report: dict) -> dict:
    return {k: v for k, v in report.items() if k not in ("sources", "citations")}


async def research(client: Client, **arguments: object) -> dict:
    fresh()
    result = await client.call_tool("research_urls", {"topic": "page weight", **arguments})
    return result.structured_content


async def main() -> None:
    use_summarizer(ExtractiveSummarizer())
    fresh()
    async with Client(mcp) as client:
        print("== the two tools post 18 adds ==")
        for tool in (await client.list_tools()).tools:
            if tool.name in ("summarize_page", "research_urls"):
                print(f"{tool.name:15} inputs={list(tool.input_schema['properties'])}")
                print(f"{'':15} outputSchema={'yes' if tool.output_schema else 'no'}  "
                      f"readOnlyHint={tool.annotations.read_only_hint}")

        print("\n== summarize_page on the article fixture, three sentences, summary text elided ==")
        fresh()
        page = await client.call_tool("summarize_page", {"url": ARTICLE_URL, "max_sentences": 3})
        shown = {k: v for k, v in page.structured_content.items() if k not in ("summary", "title")}
        print(json.dumps(shown, indent=2))

        print("\n== research_urls over the two article fixtures ==")
        report = await research(client, urls=[ARTICLE_URL, PORTAL_URL])
        print(json.dumps(header(report), indent=2))
        print(f"citations: {len(report['citations'])}, per page: "
              f"{[sum(c['url'] == s['url'] for c in report['citations']) for s in report['sources']]}, "
              f"all verbatim: {all(c['verbatim'] for c in report['citations'])}")
        for source in report["sources"]:
            print(f"source {source['url']}: raw_bytes={source['raw_bytes']:,} "
                  f"clean_bytes={source['clean_bytes']:,} words={source['word_count']} "
                  f"summarizer={source['summarizer']}")
        print(f"raw bytes, both pages: {sum(s['raw_bytes'] for s in report['sources']):,}")
        portal = [c for c in report["citations"] if c["url"] == PORTAL_URL]
        print(f"first claim from the second page (its heading): {portal[0]['claim']!r}")
        print("second claim from the second page:")
        print(json.dumps(portal[1], indent=2))

        print("\n== the same three URLs, three budgets, then the defaults ==")
        three = [ARTICLE_URL, PORTAL_URL, SPA_URL]
        for budget in ({"max_pages": 2}, {"max_words": 500}, {"max_words": 100}):
            report = await research(client, urls=three, **budget)
            print(f"{json.dumps(budget):18} pages_visited={report['pages_visited']} words_read={report['words_read']}  "
                  f"stopped_because: {report['stopped_because']}")
        report = await research(client, urls=three)
        print(f"{'(defaults)':18} pages_visited={report['pages_visited']} words_read={report['words_read']}  "
              f"stopped_because: {report['stopped_because']}")

        print("\n== a dead link first, then a page that loads, then the same page again ==")
        report = await research(client, urls=[MISSING_URL, ARTICLE_URL, ARTICLE_URL])
        print(f"pages_visited={report['pages_visited']} pages_failed={report['pages_failed']}")
        print(f"failures: {report['failures']}")
        print(f"stopped_because: {report['stopped_because']}")

        print("\n== the article again, with a provider summarizer on a stub client ==")
        use_summarizer(AnthropicSummarizer(model="claude-opus-5", client=StubClient()))
        report = await research(client, urls=[ARTICLE_URL])
        print(f"summarizer: {report['summarizer']}, source summarizer: {report['sources'][0]['summarizer']}")
        print(json.dumps(report["citations"][0], indent=2))
        use_summarizer(ExtractiveSummarizer())


asyncio.run(main())
