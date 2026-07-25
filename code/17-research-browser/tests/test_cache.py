"""The page cache: TTL, capacity, and eviction order.

The clock is injected, so expiry is tested by moving time rather than by
sleeping. A test suite that sleeps to test a TTL is a test suite nobody runs.
"""

from __future__ import annotations

from research_browser.cache import PageCache
from research_browser.extract import Extraction


class FakeClock:
    """A clock the test drives by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_extraction(url: str, words: int = 10) -> Extraction:
    text = " ".join(["word"] * words)
    return Extraction(
        url=url,
        title="Title",
        text=text,
        method="trafilatura",
        raw_bytes=10_000,
        clean_bytes=len(text.encode("utf-8")),
        reduction_percent=99.0,
        word_count=words,
    )


def test_a_stored_page_comes_back() -> None:
    cache = PageCache(clock=FakeClock())
    cache.put("https://example.com/a", make_extraction("https://example.com/a"))

    hit = cache.get("https://example.com/a")
    assert hit is not None
    assert hit.url == "https://example.com/a"


def test_a_missing_page_is_a_miss_not_an_error() -> None:
    cache = PageCache(clock=FakeClock())
    assert cache.get("https://example.com/never-seen") is None
    assert cache.stats().misses == 1
    assert cache.stats().hits == 0


def test_hits_and_misses_are_counted_separately() -> None:
    cache = PageCache(clock=FakeClock())
    cache.put("https://example.com/a", make_extraction("https://example.com/a"))

    cache.get("https://example.com/a")
    cache.get("https://example.com/a")
    cache.get("https://example.com/b")

    stats = cache.stats()
    assert stats.hits == 2
    assert stats.misses == 1


def test_an_entry_past_its_ttl_is_a_miss() -> None:
    clock = FakeClock()
    cache = PageCache(ttl_seconds=600.0, clock=clock)
    cache.put("https://example.com/a", make_extraction("https://example.com/a"))

    clock.advance(599)
    assert cache.get("https://example.com/a") is not None

    clock.advance(2)
    assert cache.get("https://example.com/a") is None


def test_an_expired_entry_is_dropped_rather_than_kept_around() -> None:
    clock = FakeClock()
    cache = PageCache(ttl_seconds=10.0, clock=clock)
    cache.put("https://example.com/a", make_extraction("https://example.com/a"))

    clock.advance(11)
    cache.get("https://example.com/a")

    assert cache.stats().entries == 0


def test_stats_separate_active_entries_from_stale_ones() -> None:
    clock = FakeClock()
    cache = PageCache(ttl_seconds=10.0, clock=clock)
    cache.put("https://example.com/old", make_extraction("https://example.com/old"))
    clock.advance(11)
    cache.put("https://example.com/new", make_extraction("https://example.com/new"))

    stats = cache.stats()
    assert stats.entries == 2
    assert stats.active == 1
    assert stats.expired == 1


def test_capacity_evicts_the_oldest_entry_first() -> None:
    clock = FakeClock()
    cache = PageCache(max_entries=3, clock=clock)

    for index in range(3):
        cache.put(f"https://example.com/{index}", make_extraction(f"u{index}"))
        clock.advance(1)

    cache.put("https://example.com/3", make_extraction("u3"))

    assert cache.get("https://example.com/0") is None
    for index in (1, 2, 3):
        assert cache.get(f"https://example.com/{index}") is not None
    assert cache.stats().evictions == 1


def test_capacity_prefers_to_drop_stale_entries_over_live_ones() -> None:
    """Eviction should not throw away a fresh page while a dead one sits there."""
    clock = FakeClock()
    cache = PageCache(ttl_seconds=10.0, max_entries=2, clock=clock)

    cache.put("https://example.com/stale", make_extraction("stale"))
    clock.advance(11)
    cache.put("https://example.com/fresh", make_extraction("fresh"))
    cache.put("https://example.com/newest", make_extraction("newest"))

    assert cache.get("https://example.com/fresh") is not None
    assert cache.get("https://example.com/newest") is not None
    assert cache.stats().evictions == 0


def test_replacing_an_existing_url_does_not_count_against_capacity() -> None:
    clock = FakeClock()
    cache = PageCache(max_entries=2, clock=clock)

    cache.put("https://example.com/a", make_extraction("a", words=5))
    cache.put("https://example.com/b", make_extraction("b", words=5))
    cache.put("https://example.com/a", make_extraction("a", words=50))

    assert cache.stats().entries == 2
    assert cache.stats().evictions == 0
    hit = cache.get("https://example.com/a")
    assert hit is not None and hit.word_count == 50


def test_clear_reports_what_it_removed() -> None:
    cache = PageCache(clock=FakeClock())
    cache.put("https://example.com/a", make_extraction("a"))
    cache.put("https://example.com/b", make_extraction("b"))

    assert cache.clear() == 2
    assert cache.clear() == 0
    assert cache.stats().entries == 0
