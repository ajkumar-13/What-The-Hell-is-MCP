"""Headless browsing.

Post 17. A single Chromium process, a fresh page per call, started and stopped
by the server's lifespan.

Two design decisions carry the rest of the file:

1. **The fetcher is a Protocol, not a class the tools import directly.** Every
   tool asks `browser.require()` for whatever fetcher is installed. The real
   one drives Playwright; the test suite installs one that reads saved HTML off
   disk. Nothing in `tools.py` or `research.py` knows the difference, and no
   test needs a network, a browser binary, or a running site.

2. **Playwright is imported lazily, inside `start()`.** Importing this module
   must not require the package, because `pip install playwright` is only half
   the job -- the browser binaries are a separate several-hundred-megabyte
   download. A server that cannot import its own modules cannot even tell you
   what is missing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Protocol

log = logging.getLogger("research-browser.browser")

DEFAULT_TIMEOUT_MS = 30_000
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720

# Waiting for "networkidle" means waiting for analytics beacons and long-polling
# sockets that may never go quiet. "domcontentloaded" plus a short settle is
# what actually works on real pages.
DEFAULT_WAIT_UNTIL = "domcontentloaded"
SETTLE_SECONDS = 0.5


class BrowserUnavailable(RuntimeError):
    """Raised when no fetcher is installed, or Playwright cannot be started."""


@dataclass
class PageResult:
    """One fetched page, before any extraction has happened."""

    url: str
    title: str
    html: str
    status: int | None


@dataclass
class ScreenshotResult:
    """A PNG capture. Raw bytes, not base64 -- encoding is the caller's problem."""

    url: str
    png: bytes
    width: int
    height: int


class PageFetcher(Protocol):
    """What the rest of the server needs from a browser.

    Deliberately small. Anything that can produce HTML for a URL and a PNG for a
    URL can stand in here, which is what makes the server testable offline.
    """

    name: str

    async def fetch(
        self,
        url: str,
        *,
        wait_until: str = DEFAULT_WAIT_UNTIL,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> PageResult: ...

    async def screenshot(
        self,
        url: str,
        *,
        full_page: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> ScreenshotResult: ...

    async def close(self) -> None: ...


class PlaywrightFetcher:
    """The real thing: one Chromium, one page per call."""

    name = "playwright-chromium"

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        try:
            # Lazy on purpose -- see the module docstring.
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "playwright is not installed. Run `uv sync` and then "
                "`uv run playwright install chromium`."
            ) from exc

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless
            )
        except Exception as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "could not launch Chromium. The Python package installs the "
                "bindings, not the browser: run `playwright install chromium` "
                f"(and `playwright install-deps` on Linux). Original error: {exc}"
            ) from exc

        log.info("chromium launched (headless=%s)", self._headless)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        log.info("chromium stopped")

    def _require_browser(self) -> Any:
        if self._browser is None:
            raise BrowserUnavailable("browser not started")
        return self._browser

    async def fetch(
        self,
        url: str,
        *,
        wait_until: str = DEFAULT_WAIT_UNTIL,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> PageResult:
        page = await self._require_browser().new_page(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
        )
        try:
            response = await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            # Client-rendered pages finish their first paint after
            # domcontentloaded. Half a second is the difference between an
            # article and an empty <div id="root">.
            await asyncio.sleep(SETTLE_SECONDS)
            html = await page.content()
            title = await page.title()
            log.info("fetched %s (%d bytes of HTML)", url, len(html))
            return PageResult(
                url=url,
                title=title,
                html=html,
                status=response.status if response is not None else None,
            )
        finally:
            # A page that is not closed is a tab that never goes away. Over a
            # long-lived server that is a memory leak with a user-visible
            # symptom: Chromium eventually refuses to open new pages.
            await page.close()

    async def screenshot(
        self,
        url: str,
        *,
        full_page: bool = False,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> ScreenshotResult:
        page = await self._require_browser().new_page(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
        )
        try:
            await page.goto(url, wait_until="load", timeout=timeout_ms)
            await asyncio.sleep(SETTLE_SECONDS)
            png = await page.screenshot(full_page=full_page)
            viewport = page.viewport_size or {}
            log.info("screenshot of %s (%d bytes of PNG)", url, len(png))
            return ScreenshotResult(
                url=url,
                png=png,
                width=int(viewport.get("width", VIEWPORT_WIDTH)),
                height=int(viewport.get("height", VIEWPORT_HEIGHT)),
            )
        finally:
            await page.close()


class BrowserSession:
    """Holds whichever fetcher this process is using.

    The injection point. `use()` installs a fetcher and makes `start()` a no-op,
    which is how the tests drive the real protocol path with saved HTML.
    """

    def __init__(self, factory: Callable[[], PageFetcher] | None = None) -> None:
        self._factory = factory or (lambda: PlaywrightFetcher())
        self._fetcher: PageFetcher | None = None
        self._owns_fetcher = False

    def use(self, fetcher: PageFetcher) -> None:
        """Install a fetcher. The session will not start or stop this one."""
        self._fetcher = fetcher
        self._owns_fetcher = False

    def require(self) -> PageFetcher:
        if self._fetcher is None:
            raise BrowserUnavailable(
                "no browser is running. The server's lifespan starts one; if "
                "you are calling this outside a session, install a fetcher "
                "with browser.use(...)."
            )
        return self._fetcher

    async def start(self) -> None:
        """Launch the browser, unless a fetcher was already injected."""
        if self._fetcher is not None:
            return
        fetcher = self._factory()
        start = getattr(fetcher, "start", None)
        if start is not None:
            await start()
        self._fetcher = fetcher
        self._owns_fetcher = True

    async def stop(self) -> None:
        """Close the browser, but only the one this session created."""
        if self._fetcher is not None and self._owns_fetcher:
            await self._fetcher.close()
        if self._owns_fetcher:
            self._fetcher = None
            self._owns_fetcher = False


# Module-level singleton. One Chromium per server process.
browser = BrowserSession()
