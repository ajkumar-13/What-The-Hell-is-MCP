"""Post 17, section 6: the page cache's two bounds, and what the protocol caches.

Run from the series root:
    uv run python posts/17-research-browser/snippets/cache_bounds.py

Imports `PageCache` from code/17-research-browser/src and the suite's
`FakeClock` and `make_extraction` from tests/test_cache.py, so time moves by
hand and nothing sleeps. Prints the defaults, the age bound, the count bound,
the stale-first eviction, the counters across a clear, and then, over an
in-memory client with the suite's `FixtureFetcher` installed, a second browse of
one URL. Last, the result fields the SDK's own types give `tools/list` and
`tools/call`.
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

import mcp_types  # noqa: E402
from conftest import ARTICLE_URL, FixtureFetcher  # noqa: E402
from mcp import Client  # noqa: E402
from test_cache import FakeClock, make_extraction  # noqa: E402

import research_browser  # noqa: E402
from research_browser.browser import browser  # noqa: E402
from research_browser.cache import DEFAULT_MAX_ENTRIES, DEFAULT_TTL_SECONDS, PageCache  # noqa: E402

logging.disable(logging.CRITICAL)

A = "https://example.com/a"


def bounds() -> None:
    print(f"1. defaults: ttl_seconds={DEFAULT_TTL_SECONDS}  max_entries={DEFAULT_MAX_ENTRIES}")
    print()

    print("2. the age bound (ttl_seconds=600)")
    clock = FakeClock()
    cache = PageCache(ttl_seconds=600.0, clock=clock)
    cache.put(A, make_extraction(A))
    clock.advance(599)
    print(f"   599 s after the put: {'hit' if cache.get(A) is not None else 'miss'}")
    clock.advance(2)
    print(f"   601 s after the put: {'hit' if cache.get(A) is not None else 'miss'}; "
          f"entries left: {cache.stats().entries}")
    print()

    print("3. the count bound (max_entries=3, one put per second)")
    clock = FakeClock()
    cache = PageCache(max_entries=3, clock=clock)
    for index in range(4):
        cache.put(f"https://example.com/{index}", make_extraction(f"u{index}"))
        clock.advance(1)
    kept = [i for i in range(4) if cache.get(f"https://example.com/{i}") is not None]
    print(f"   after four puts, still cached: {kept}; evictions={cache.stats().evictions}")
    print()

    print("4. full, with one entry already stale (ttl_seconds=10, max_entries=2)")
    clock = FakeClock()
    cache = PageCache(ttl_seconds=10.0, max_entries=2, clock=clock)
    cache.put("https://example.com/stale", make_extraction("stale"))
    clock.advance(11)
    cache.put("https://example.com/fresh", make_extraction("fresh"))
    cache.put("https://example.com/newest", make_extraction("newest"))
    names = [u.rsplit("/", 1)[-1] for u in ("https://example.com/stale", "https://example.com/fresh",
                                            "https://example.com/newest")
             if cache.get(u) is not None]
    print(f"   still cached: {names}; evictions={cache.stats().evictions}")
    print()

    print("5. the counters across clear()")
    clock = FakeClock()
    cache = PageCache(clock=clock)
    cache.put(A, make_extraction(A))
    cache.get(A)
    cache.get("https://example.com/b")
    before = cache.stats()
    removed = cache.clear()
    after = cache.stats()
    print(f"   before: entries={before.entries} hits={before.hits} misses={before.misses}")
    print(f"   clear() removed {removed}; after: entries={after.entries} "
          f"hits={after.hits} misses={after.misses}")
    print()


async def through_the_tool() -> None:
    print("6. browse_url twice on one URL, then with use_cache=false")
    fixture = FixtureFetcher()
    browser.use(fixture)
    async with Client(research_browser.mcp) as client:
        for args in ({"url": ARTICLE_URL}, {"url": ARTICLE_URL},
                     {"url": ARTICLE_URL, "use_cache": False}):
            data = (await client.call_tool("browse_url", args)).structured_content
            print(f"   {str(args):<62} from_cache={data['from_cache']}")
    print(f"   navigations: {len(fixture.fetched)}")
    print()

    print("7. caching hints in the SDK's result types")
    for name in ("ListToolsResult", "CallToolResult"):
        fields = [f.alias or n for n, f in getattr(mcp_types, name).model_fields.items()]
        print(f"   {name:<16}{fields}")


async def main() -> None:
    bounds()
    await through_the_tool()


if __name__ == "__main__":
    asyncio.run(main())
