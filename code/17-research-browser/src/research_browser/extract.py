"""Content extraction, and the number that justifies the whole server.

Post 17. The thesis: fetching the page is the easy part. The value is throwing
away the ninety-odd percent of it that would otherwise burn the model's
context on navigation, cookie banners, and inline CSS.

So every extraction reports what it cost and what it saved: bytes in, bytes
out, and the ratio between them. Those are the numbers post 17 quotes, and the
test suite asserts them against the committed fixtures so they stay honest.

Two extractors, in order:

- `trafilatura` is built for articles. It finds the main text node and drops
  everything around it. On a normal article it is very good and very fast.
- `markdownify` is the fallback for pages trafilatura declines: documentation,
  forums, search results, anything that is not shaped like an article. It keeps
  more boilerplate, which is exactly why it is second.

When both come back empty the method is reported as `empty` rather than dressed
up as a successful extraction. A page that rendered nothing is a real answer,
and it is the answer you get for a JavaScript shell fetched without a browser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify

log = logging.getLogger("research-browser.extract")

# Below this, an "extraction" is a nav bar and a copyright line. Treat it as a
# miss and let the next extractor try.
MIN_USEFUL_CHARS = 200

# Elements the fallback removes outright, contents included.
#
# This has to happen before markdownify sees the document. markdownify's own
# `strip=` argument removes the *tag* and keeps the *text*, which on a `<script>`
# means the page's JavaScript comes back as prose. On tests/fixtures/spa-shell.html
# that turned a JavaScript shell into 2,539 words, 1,777 of them the minified
# bundle, extracted with every appearance of success.
_DROP_ELEMENTS = (
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "iframe",
    "form",
    "nav",
    "header",
    "footer",
    "aside",
)

_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)


@dataclass
class Extraction:
    """Clean text, plus the receipt showing what was discarded.

    The class-body annotations below are load-bearing. A class that only sets
    attributes inside __init__ has no type hints for the SDK to read, and you
    get `outputSchema: null` with no warning at all.
    """

    url: str
    title: str
    text: str
    method: str
    raw_bytes: int
    clean_bytes: int
    reduction_percent: float
    word_count: int


def _tidy(text: str) -> str:
    """Collapse the whitespace an HTML-to-text pass leaves behind."""
    text = _TRAILING_SPACE.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


def _try_trafilatura(html: str, url: str) -> str | None:
    """Article extraction. Options are fixed so the numbers are reproducible."""
    try:
        text = trafilatura.extract(
            html,
            url=url or None,
            output_format="txt",
            include_comments=False,
            include_links=False,
            include_images=False,
            include_tables=True,
            favor_precision=True,
        )
    except Exception as exc:  # pragma: no cover - trafilatura is well behaved
        log.warning("trafilatura raised on %s: %s", url or "<no url>", exc)
        return None
    return text


def _try_markdownify(html: str) -> str | None:
    """Whole-document fallback. Keeps more than we want, but keeps something."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(list(_DROP_ELEMENTS)):
            element.decompose()
        return markdownify(str(soup), heading_style="ATX")
    except Exception as exc:  # pragma: no cover - markdownify is well behaved
        log.warning("markdownify raised: %s", exc)
        return None


def extract(html: str, *, url: str = "", title: str = "") -> Extraction:
    """Turn a page of HTML into the text worth spending context on.

    Args:
        html: The raw document, as the browser saw it after rendering.
        url: Used by trafilatura to resolve relative links, and reported back.
        title: The page title, if the fetcher already knows it.

    Returns:
        An `Extraction` carrying the clean text and the measured reduction.
    """
    raw_bytes = len(html.encode("utf-8"))

    text = _try_trafilatura(html, url)
    method = "trafilatura"

    if text is None or len(text.strip()) < MIN_USEFUL_CHARS:
        fallback = _try_markdownify(html)
        # Only accept the fallback if it actually did better. On a JavaScript
        # shell neither one finds anything, and saying so is the honest answer.
        if fallback is not None and len(fallback.strip()) > len((text or "").strip()):
            text = fallback
            method = "markdownify"

    text = _tidy(text or "")
    if not text:
        method = "empty"

    clean_bytes = len(text.encode("utf-8"))
    reduction = 0.0 if raw_bytes == 0 else (1 - clean_bytes / raw_bytes) * 100

    log.info(
        "extracted %s: %d -> %d bytes (%.1f percent smaller, method=%s)",
        url or "<no url>",
        raw_bytes,
        clean_bytes,
        reduction,
        method,
    )

    return Extraction(
        url=url,
        title=title,
        text=text,
        method=method,
        raw_bytes=raw_bytes,
        clean_bytes=clean_bytes,
        reduction_percent=round(reduction, 1),
        word_count=len(text.split()),
    )
