"""Extraction, and the size-reduction numbers post 17 quotes.

These assertions are the reason the fixtures are committed. The post claims a
particular reduction on a particular shape of page; if a dependency bump moves
those numbers, this file fails and the post gets corrected rather than quietly
becoming wrong.

Measured against the committed fixtures:

    fixture                        raw       clean   reduction  method
    article.html                76,201 B    4,126 B     94.6%   trafilatura
    nav-heavy.html             243,128 B      860 B     99.6%   trafilatura
    spa-shell.html (fetched)    48,522 B       46 B     99.9%   markdownify
    spa-shell.html (rendered)   50,063 B    1,558 B     96.9%   trafilatura

Raw byte counts are asserted exactly, because they are a property of the file.
Everything downstream of the extractor is asserted as a band, because it is a
property of a pinned dependency rather than of this repository.
"""

from __future__ import annotations

from research_browser.extract import extract

# Exact sizes of the committed fixtures, in UTF-8 bytes.
ARTICLE_RAW_BYTES = 76_201
NAV_HEAVY_RAW_BYTES = 243_128
SPA_SHELL_RAW_BYTES = 48_522


def test_article_fixture_is_the_size_the_post_says_it_is(article_html: str) -> None:
    assert len(article_html.encode("utf-8")) == ARTICLE_RAW_BYTES


def test_nav_heavy_fixture_is_the_size_the_post_says_it_is(nav_heavy_html: str) -> None:
    assert len(nav_heavy_html.encode("utf-8")) == NAV_HEAVY_RAW_BYTES


def test_spa_fixture_is_the_size_the_post_says_it_is(spa_shell_html: str) -> None:
    assert len(spa_shell_html.encode("utf-8")) == SPA_SHELL_RAW_BYTES


# --------------------------------------------------------------------------
# The thesis: bytes in, bytes out, ratio
# --------------------------------------------------------------------------


def test_article_reduces_by_more_than_ninety_percent(article_html: str) -> None:
    result = extract(article_html, url="https://example.com/blog/x", title="Article")

    assert result.method == "trafilatura"
    assert result.raw_bytes == ARTICLE_RAW_BYTES
    assert 3_500 <= result.clean_bytes <= 5_000
    assert 93.0 <= result.reduction_percent <= 96.5
    assert 650 <= result.word_count <= 780


def test_nav_heavy_page_reduces_by_more_than_ninety_nine_percent(
    nav_heavy_html: str,
) -> None:
    """The worse the page, the better the number.

    A short story wrapped in a mega menu, four ad slots, thirty recirculation
    cards, and a serialised store is the case that makes the server worth
    building.
    """
    result = extract(nav_heavy_html, url="https://example.com/news/x", title="News")

    assert result.method == "trafilatura"
    assert result.raw_bytes == NAV_HEAVY_RAW_BYTES
    assert result.clean_bytes <= 2_000
    assert result.reduction_percent >= 99.0
    assert 100 <= result.word_count <= 220


def test_the_ratio_is_internally_consistent(article_html: str) -> None:
    """`reduction_percent` must be derived from the two byte counts, not asserted."""
    result = extract(article_html, url="https://example.com/blog/x")

    expected = (1 - result.clean_bytes / result.raw_bytes) * 100
    assert abs(result.reduction_percent - expected) < 0.05
    assert result.clean_bytes == len(result.text.encode("utf-8"))


def test_empty_input_does_not_divide_by_zero() -> None:
    result = extract("", url="https://example.com/nothing")

    assert result.method == "empty"
    assert result.raw_bytes == 0
    assert result.reduction_percent == 0.0
    assert result.word_count == 0


# --------------------------------------------------------------------------
# What actually survives
# --------------------------------------------------------------------------


def test_the_article_survives_and_the_furniture_does_not(nav_heavy_html: str) -> None:
    result = extract(nav_heavy_html, url="https://example.com/news/x")

    assert "twelve-week consultation" in result.text
    assert "machine readable without vendor-specific tooling" in result.text

    # Navigation, advertising, consent, recirculation, and the comment thread.
    for boilerplate in (
        "Acceptable use",
        "Advertisement",
        "Accept all",
        "Comments",
        "Published 1 days ago",
    ):
        assert boilerplate not in result.text, boilerplate


def test_headings_and_quotes_survive_extraction(article_html: str) -> None:
    result = extract(article_html, url="https://example.com/blog/x")

    assert "Where the bytes actually go" in result.text
    assert "The article is not the page." in result.text


# --------------------------------------------------------------------------
# Fetched against rendered
# --------------------------------------------------------------------------


def test_a_javascript_shell_yields_nothing_without_a_browser(
    spa_shell_html: str,
) -> None:
    """This is the whole argument for driving a browser instead of fetching.

    The document is 48 KB. Fetched without rendering, the only text on it is the
    contents of the title tag.
    """
    result = extract(spa_shell_html, url="https://example.com/app/x")

    assert result.raw_bytes == SPA_SHELL_RAW_BYTES
    assert result.word_count < 15
    assert result.reduction_percent > 99.0
    assert "Every page in this study" not in result.text


def test_rendering_the_same_page_yields_the_article(spa_rendered_html: str) -> None:
    result = extract(spa_rendered_html, url="https://example.com/app/x")

    assert result.method == "trafilatura"
    assert result.word_count > 200
    assert "Every page in this study was loaded twice" in result.text
    assert 95.0 <= result.reduction_percent <= 98.5


def test_the_fallback_never_returns_javascript_as_prose(spa_shell_html: str) -> None:
    """The bug this guards against shipped, and looked like a success.

    markdownify's `strip=` removes the tag and keeps the text, so passing it
    `["script"]` hands back the page's minified bundle as extracted content:
    hundreds of words, a plausible-looking word count, and nothing a reader
    would call text. Elements have to be removed from the tree first.
    """
    result = extract(spa_shell_html, url="https://example.com/app/x")

    for javascript in ("function", "dataLayer", "addEventListener", "querySelector"):
        assert javascript not in result.text, javascript

    # Nor the serialised store, which is the other large inline script.
    assert "buildId" not in result.text
    assert "__NEXT_DATA__" not in result.text


def test_the_fallback_runs_when_trafilatura_declines() -> None:
    """A page with no article body still gets a best effort, and says which."""
    html = (
        "<html><head><title>Directory</title><style>body{color:red}</style></head>"
        "<body><script>var x=1;</script>"
        "<main><h1>Service directory</h1>"
        "<ul><li>Registry lookup</li><li>Schema validation</li>"
        "<li>Bulk export</li><li>Webhook replay</li></ul></main>"
        "</body></html>"
    )
    result = extract(html, url="https://example.com/directory")

    assert result.method == "markdownify"
    assert "Service directory" in result.text
    assert "var x=1" not in result.text
    assert "color:red" not in result.text
