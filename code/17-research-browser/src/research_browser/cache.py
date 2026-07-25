"""A TTL cache for extracted pages.

Post 17. Fetching and extracting the same URL twice in one research session is
pure waste: a second Chromium navigation, a second extraction pass, and the same
bytes back. Two bounds keep the cache from becoming its own problem:

- **TTL.** A cached page is a stale page eventually. Ten minutes is long enough
  to cover a research session and short enough that nobody is served yesterday's
  news.
- **Max entries, oldest first.** Without a ceiling, a long-running server that
  browses widely holds every page it ever saw. Eviction is by insertion time,
  not by access time: least-recently-used needs bookkeeping on every read, and
  for a cache this size the simpler rule behaves the same.

The clock is injectable so the tests can expire entries without sleeping.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from .extract import Extraction

log = logging.getLogger("research-browser.cache")

DEFAULT_TTL_SECONDS = 600.0
DEFAULT_MAX_ENTRIES = 100


@dataclass
class CacheStats:
    """A snapshot of the cache, safe to publish as a tool result."""

    entries: int
    active: int
    expired: int
    max_entries: int
    ttl_seconds: float
    hits: int
    misses: int
    evictions: int


@dataclass
class _Entry:
    extraction: Extraction
    stored_at: float


class PageCache:
    """In-memory, per-process, bounded by both age and count."""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max(1, max_entries)
        self._clock = clock
        self._store: dict[str, _Entry] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _expired(self, entry: _Entry) -> bool:
        return (self._clock() - entry.stored_at) > self.ttl_seconds

    def get(self, url: str) -> Extraction | None:
        """Return the cached extraction, or None if it is missing or stale."""
        entry = self._store.get(url)
        if entry is None:
            self._misses += 1
            return None
        if self._expired(entry):
            del self._store[url]
            self._misses += 1
            log.info("cache expired: %s", url)
            return None
        self._hits += 1
        return entry.extraction

    def put(self, url: str, extraction: Extraction) -> None:
        """Store an extraction, evicting the oldest entry if we are full."""
        if url not in self._store and len(self._store) >= self.max_entries:
            self._evict_oldest()
        self._store[url] = _Entry(extraction=extraction, stored_at=self._clock())

    def clear(self) -> int:
        """Drop every cached page. Returns how many entries were removed.

        Counters survive on purpose: hits and misses describe how the cache has
        performed over the process's life, and a manual flush is not a reason to
        forget that.
        """
        removed = len(self._store)
        self._store.clear()
        if removed:
            log.info("cache cleared (%d entries)", removed)
        return removed

    def reset_stats(self) -> None:
        """Zero the counters without touching the entries."""
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def stats(self) -> CacheStats:
        expired = sum(1 for entry in self._store.values() if self._expired(entry))
        return CacheStats(
            entries=len(self._store),
            active=len(self._store) - expired,
            expired=expired,
            max_entries=self.max_entries,
            ttl_seconds=self.ttl_seconds,
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
        )

    def _evict_oldest(self) -> None:
        # Expired entries are dead weight; clear those before evicting a live
        # one. Only if everything is still fresh do we drop the oldest.
        stale = [url for url, entry in self._store.items() if self._expired(entry)]
        if stale:
            for url in stale:
                del self._store[url]
            return

        oldest = min(self._store, key=lambda url: self._store[url].stored_at)
        del self._store[oldest]
        self._evictions += 1
        log.info("cache evicted oldest entry: %s", oldest)


# Module-level singleton, cleared by the server's lifespan on shutdown.
cache = PageCache()
