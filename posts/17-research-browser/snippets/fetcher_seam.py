"""Post 17, section 3: the seam that keeps the browser out of the tests.

Run from the series root:
    uv run python posts/17-research-browser/snippets/fetcher_seam.py

Imports the package under code/17-research-browser/src and never starts
Chromium: it checks that importing the server did not import Playwright, lists
what the `PageFetcher` protocol asks for, shows the refusal a session with no
fetcher gives, and then runs the server's real lifespan over an in-memory client
with the suite's `FixtureFetcher` (from tests/conftest.py) installed, printing
the log lines the lifespan writes and whether it closed a fetcher it did not
start.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

import research_browser  # noqa: E402

PLAYWRIGHT_AT_IMPORT = "playwright" in sys.modules

from conftest import ARTICLE_URL, FixtureFetcher  # noqa: E402
from mcp import Client  # noqa: E402

from research_browser.browser import (  # noqa: E402
    BrowserSession,
    BrowserUnavailable,
    PageFetcher,
    browser,
)


class Records(logging.Handler):
    """Collects the project's log lines instead of printing them to stderr."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("research-browser"):
            self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


records = Records()
root_logger = logging.getLogger()
for handler in list(root_logger.handlers):
    root_logger.removeHandler(handler)
root_logger.addHandler(records)
root_logger.setLevel(logging.INFO)
logging.getLogger("trafilatura").setLevel(logging.ERROR)


async def main() -> None:
    print(f"1. 'playwright' in sys.modules after importing the server: {PLAYWRIGHT_AT_IMPORT}")
    members = sorted(n for n in vars(PageFetcher) if not n.startswith("_"))
    members += sorted(PageFetcher.__annotations__)
    print(f"   PageFetcher asks for: {', '.join(members)}")
    print()

    print("2. a session with no fetcher, asked for one")
    try:
        BrowserSession().require()
    except BrowserUnavailable as exc:
        print(f"   BrowserUnavailable: {exc}")
    print()

    print("3. the real lifespan, with the suite's fixture fetcher installed")
    fixture = FixtureFetcher()
    browser.use(fixture)
    async with Client(research_browser.mcp) as client:
        result = await client.call_tool("browse_url", {"url": ARTICLE_URL})
        print(f"   browse_url: method={result.structured_content['method']}, "
              f"fetcher saw {fixture.fetched}")
    for line in records.lines:
        if "research browser" in line:
            print(f"   logged: {line}")
    print(f"   the injected fetcher was closed by the lifespan: {fixture.closed}")
    print(f"   playwright imported at any point: {'playwright' in sys.modules}")


if __name__ == "__main__":
    asyncio.run(main())
