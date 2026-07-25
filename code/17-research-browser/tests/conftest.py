"""Shared test setup.

The whole suite runs with no network, no Playwright, and no API key. That is a
design property of the server rather than an accident of the tests: `browser.py`
takes a `PageFetcher`, so a fetcher that reads saved HTML off disk is a
first-class stand-in for Chromium, and every tool exercises the same code path
either way.

`FixtureFetcher` also does one thing a plain file reader would not: it
optionally *renders* the JavaScript shell. Real client-rendered pages ship their
state as an inline JSON blob and build the DOM from it on load, so hydrating
that blob is a faithful, deterministic model of what a browser gives you and a
plain fetch does not.
"""

from __future__ import annotations

import base64
import json
import pathlib
import re

import pytest

from research_browser.browser import PageResult, ScreenshotResult, browser
from research_browser.cache import cache
from research_browser.summarize import ExtractiveSummarizer, use_summarizer

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

ARTICLE_URL = "https://example.com/blog/what-a-page-costs"
PORTAL_URL = "https://example.com/news/portability-consultation"
SPA_URL = "https://example.com/app/rendering-budgets"
MISSING_URL = "https://example.com/gone"

FIXTURE_FOR_URL = {
    ARTICLE_URL: "article.html",
    PORTAL_URL: "nav-heavy.html",
    SPA_URL: "spa-shell.html",
}

# A real 1x1 PNG. Small enough to inline, valid enough that a client decoding
# the image block gets an image rather than an error.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)

_DATA_BLOB = re.compile(
    r'<script type="application/json" id="__DATA__">(.*?)</script>', re.S
)
_ROOT_NODE = re.compile(r'(<div id="root">).*?(</div></main>)', re.S)


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def hydrate(html: str) -> str:
    """Build the DOM the page's own JavaScript would build.

    Deterministic stand-in for a real render: find the inline state payload,
    turn it into the article markup the client script produces, and put it where
    the client script would put it. Pages without a payload come back unchanged,
    which is the right answer for server-rendered HTML.
    """
    blob = _DATA_BLOB.search(html)
    if blob is None:
        return html
    state = json.loads(blob.group(1))
    paragraphs = "".join(f"<p>{p}</p>" for p in state.get("paragraphs", []))
    article = (
        '<article class="article">'
        f'<h1>{state.get("title", "")}</h1>'
        f'<p class="byline">By {state.get("byline", "")}</p>'
        f"{paragraphs}</article>"
    )
    return _ROOT_NODE.sub(lambda m: m.group(1) + article + m.group(2), html, count=1)


class FixtureFetcher:
    """A PageFetcher backed by files. No network, no browser binary."""

    name = "fixture"

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        *,
        render: bool = True,
    ) -> None:
        self.pages = dict(pages if pages is not None else FIXTURE_FOR_URL)
        self.render = render
        self.fetched: list[str] = []
        self.screenshotted: list[str] = []
        self.closed = False

    async def fetch(self, url: str, **_: object) -> PageResult:
        self.fetched.append(url)
        name = self.pages.get(url)
        if name is None:
            # Same shape of failure a dead link produces in production: an
            # exception out of the fetcher, which the tools turn into a
            # ToolError and the research loop records and steps over.
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
        html = read_fixture(name)
        if self.render:
            html = hydrate(html)
        title = ""
        match = re.search(r"<title>(.*?)</title>", html, re.S)
        if match:
            title = match.group(1).split("|")[0].strip()
        return PageResult(url=url, title=title, html=html, status=200)

    async def screenshot(self, url: str, *, full_page: bool = False, **_: object):
        self.screenshotted.append(url)
        if url not in self.pages:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
        return ScreenshotResult(
            url=url,
            png=PNG_1X1,
            width=1280,
            height=4800 if full_page else 720,
        )

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fetcher() -> FixtureFetcher:
    """The fetcher installed for the current test, so tests can inspect it."""
    return browser.require()


@pytest.fixture(autouse=True)
def isolated_server() -> None:
    """Give every test a clean cache, a fixture browser, and no provider.

    The summarizer is pinned to the extractive one on purpose. Without this, a
    developer who happens to have ANTHROPIC_API_KEY exported would run a
    different code path -- and possibly a billable one -- than CI does.
    """
    browser.use(FixtureFetcher())
    use_summarizer(ExtractiveSummarizer())
    cache.clear()
    cache.reset_stats()


@pytest.fixture
def article_html() -> str:
    return read_fixture("article.html")


@pytest.fixture
def nav_heavy_html() -> str:
    return read_fixture("nav-heavy.html")


@pytest.fixture
def spa_shell_html() -> str:
    return read_fixture("spa-shell.html")


@pytest.fixture
def spa_rendered_html(spa_shell_html: str) -> str:
    return hydrate(spa_shell_html)
