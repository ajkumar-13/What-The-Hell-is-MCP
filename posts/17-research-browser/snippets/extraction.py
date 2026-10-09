"""Post 17, sections 2, 4 and 5: what the extractor keeps from each committed page.

Run from the series root:
    uv run python posts/17-research-browser/snippets/extraction.py

Imports `extract()` from code/17-research-browser/src and the fixtures and the
`hydrate()` helper from code/17-research-browser/tests/conftest.py, as the
project's tests do. No browser and no network: the rendered row is `hydrate()`,
the suite's deterministic stand-in for the page's own script. Prints the
measured table, the ratios the post derives from it, where the bytes of the two
article pages go, what the previous edition's `markdownify(strip=...)` fallback
returned for the client-rendered shell, and what the fixed fallback returns.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

from importlib.metadata import version  # noqa: E402

from bs4 import BeautifulSoup  # noqa: E402
from conftest import hydrate, read_fixture  # noqa: E402
from markdownify import markdownify  # noqa: E402

from research_browser.extract import extract  # noqa: E402

logging.disable(logging.CRITICAL)  # trafilatura logs a WARNING for the empty shell

LEAKS = ("function", "dataLayer", "addEventListener", "querySelector", "buildId", "__NEXT_DATA__")


def words_in(html: str, tag: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    return sum(len(element.get_text().split()) for element in soup(tag))


def bytes_in(html: str, tag: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    return sum(len(str(element).encode("utf-8")) for element in soup(tag))


def main() -> None:
    print(f"trafilatura {version('trafilatura')}, markdownify {version('markdownify')}, "
          f"beautifulsoup4 {version('beautifulsoup4')}, Python {sys.version.split()[0]}")
    print()

    article = read_fixture("article.html")
    portal = read_fixture("nav-heavy.html")
    shell = read_fixture("spa-shell.html")
    rows = [
        ("article.html", article),
        ("nav-heavy.html", portal),
        ("spa-shell.html, fetched", shell),
        ("spa-shell.html, rendered", hydrate(shell)),
    ]

    print("1. extract() over the committed fixtures")
    print(f"   {'fixture':<26}{'raw B':>8}{'kept B':>8}{'discarded':>11}  {'method':<13}{'words':>5}")
    results = {}
    for name, html in rows:
        r = extract(html, url="https://example.com/x")
        results[name] = r
        print(f"   {name:<26}{r.raw_bytes:>8}{r.clean_bytes:>8}{r.reduction_percent:>9.1f} %  "
              f"{r.method:<13}{r.word_count:>5}")
    a, p = results["article.html"], results["nav-heavy.html"]
    f, g = results["spa-shell.html, fetched"], results["spa-shell.html, rendered"]
    print(f"   the fetched shell's whole text: {f.text!r}")
    print()

    print("2. the ratios")
    print(f"   portal raw / article raw:          {p.raw_bytes / a.raw_bytes:.2f}")
    print(f"   portal kept / article kept:        {p.clean_bytes / a.clean_bytes:.2f} (bytes), "
          f"{p.word_count / a.word_count:.2f} (words)")
    print(f"   raw / kept, article:               {a.raw_bytes / a.clean_bytes:.1f}")
    print(f"   raw / kept, portal:                {p.raw_bytes / p.clean_bytes:.1f}")
    print(f"   rendered shell minus fetched shell: {g.raw_bytes - f.raw_bytes} bytes "
          f"({100 * (g.raw_bytes - f.raw_bytes) / f.raw_bytes:.1f} percent)")
    print()

    print("3. where the bytes go (whole <style> and <script> elements, share of the file)")
    for name, html in (("article.html", article), ("nav-heavy.html", portal)):
        total = len(html.encode("utf-8"))
        style, script = bytes_in(html, "style"), bytes_in(html, "script")
        print(f"   {name:<16}style {100 * style / total:4.1f} %   script {100 * script / total:4.1f} %")
    print()

    print("4. the previous edition's fallback on the fetched shell")
    old = markdownify(shell, strip=["script", "style", "nav", "footer", "header"], heading_style="ATX")
    soup = BeautifulSoup(shell, "html.parser")
    store = soup.find("script", id="__NEXT_DATA__")
    print("   markdownify(html, strip=['script', 'style', 'nav', 'footer', 'header'])")
    print(f"   words returned:                 {len(old.split())}")
    print(f"   words inside <script> elements: {words_in(shell, 'script')} "
          f"(the __NEXT_DATA__ store alone: {len(store.get_text().split())})")
    print(f"   words inside <style> elements:  {words_in(shell, 'style')}")
    print(f"   leaked strings: {[s for s in LEAKS if s in old]}")
    print()

    print("5. the fixed fallback (elements decomposed first) on the same shell")
    print(f"   method={f.method}  words={f.word_count}  leaked strings: {[s for s in LEAKS if s in f.text]}")
    empty = extract("", url="https://example.com/nothing")
    print(f"   extract(''): method={empty.method}  raw_bytes={empty.raw_bytes}  "
          f"reduction_percent={empty.reduction_percent}  word_count={empty.word_count}")


if __name__ == "__main__":
    main()
