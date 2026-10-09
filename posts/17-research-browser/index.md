# 17 - Project 3: A deep research browser

> **TL;DR.** The valuable part of a browsing Model Context Protocol (MCP) server is not the fetch but the discard: on the two article pages committed to this project, extraction throws away 94.6 percent and 99.6 percent of the bytes and hands the model 707 and 134 words instead of 76,201 and 243,128 bytes of markup. This post builds that server, drives a headless browser so client-rendered pages do not come back empty, and makes every page it returns carry the byte counts and the method it was derived from. It also fixes two defects the previous edition shipped: a screenshot returned as text, and JavaScript returned as prose. Every number, result and transcript below is printed by a script in `snippets/` over the project's committed pages, or by the project's own test suite, and nothing in either starts a browser.
>
> **Prerequisites:** [Post 06](../06-tools-in-depth/index.md).
> **Safe to skip?** Skip it if the reader can already keep a browser out of a server's test suite, tell an extraction that found nothing from one that found an article by its byte counts and method, and return an image content block while still publishing an output schema.
>
> **After reading, you will be able to:**
>
> - Drive a headless browser from an MCP server behind a small fetcher protocol, so the test suite runs the real tools without a browser.
> - Extract article text from a page and report the bytes in, the bytes out, the reduction, and the method alongside it.
> - Return a screenshot as a real image content block while still publishing an output schema.
> - Cache extractions with bounds on both age and count, and explain why a `tools/call` result carries no protocol caching hints.

![Two pairs of nested squares drawn to one area scale. On the left, a plain article template: a large outlined square of 76,201 bytes of fetched HTML, annotated as 49.2 percent inline style sheets and 32.1 percent scripts, with a small solid square of 4,126 bytes of kept article text in its corner. On the right, a portal page: a much larger outlined square of 243,128 bytes, annotated as 74.7 percent scripts, with a far smaller solid square of 860 bytes inside it. The right-hand page is three times heavier and yields one fifth as much text.](diagrams/01-html-to-article.svg)

*Both pairs are drawn on one scale, from the byte counts `snippets/extraction.py` prints for the committed fixtures.*

---

## 1. The question: why is the discard worth more than the fetch?

A user asks the host to research something. The host has a model, the model has a context window, and the web has pages. The obvious server is a thin wrapper over a Hypertext Transfer Protocol (HTTP) GET that returns whatever came back.

That server is close to useless, and the reason is arithmetic. One of the pages committed to this project is an ordinary news story: a headline and three paragraphs. As a document it is 243,128 bytes. As prose it is 860 bytes and 134 words. Handed the document, the model reads about 283 bytes for every byte of story.

So the brief for this server is not "fetch pages". It is:

- Render the page the way a reader would see it, including whatever its JavaScript builds.
- Throw away navigation, advertising, consent banners, recirculation, styling, and scripts.
- Report what was thrown away, in bytes, so the caller can check the claim.
- Never pay twice for the same URL inside one research session.
- Return a picture as a picture.

Vocabulary first, because readers arrive mid-series. The **host** is the application the user talks to and the thing that owns the model. The **client** is the protocol-speaking object inside the host, one per connected server. The **server** is the process this post builds. The question this post answers is why the valuable part of such a server is what it throws away, and how to measure that.

The whole project lives in [code/17-research-browser/](../../code/17-research-browser/), and every path below that starts with `src/` or `tests/` is relative to it. [Post 18](../18-server-side-models/index.md) adds the second half of it, server-side model calls and multi-page research.

**A note on the previous edition.** The earlier version of this project claimed "500 KB to 5 MB of raw HTML" and "maybe 10-50 KB" of article, with no measurement behind either figure. This edition commits three real HTML pages to [tests/fixtures/](../../code/17-research-browser/tests/fixtures/), asserts their raw sizes exactly and the extracted sizes as bands in `tests/test_extract.py`, and `snippets/extraction.py` prints the exact numbers this post quotes, so a dependency bump that moves the ratio shows up rather than quietly making the post wrong.

## 2. Why a plain fetch is not enough

Start with the failure, because it is the one that wastes an afternoon: the page loads, the extractor runs, and six words come back.

![Two columns for one client-rendered URL. On the left, labeled as a plain HTTP GET, the shell the server sent: an empty root element and the article as an inline JSON payload nothing has read, 48,522 bytes from which the extractor keeps 46 bytes and six words, by markdownify, all of them the title. On the right, labeled as a headless browser, the same document after the page script has built an article element inside the root: 50,063 bytes, about three percent heavier, from which the extractor keeps 1,558 bytes and 265 words, by trafilatura.](diagrams/03-fetch-vs-render.svg)

*The same URL and the same extractor; the only difference is whether the page's own script ran. In this post's runs the right-hand document comes from the suite's stand-in for that script, not from Chromium.*

The third fixture, [spa-shell.html](../../code/17-research-browser/tests/fixtures/spa-shell.html), is a client-rendered page: the server sends a shell with an empty `<div id="root">` and the article as an inline JavaScript Object Notation (JSON) payload, and the page's own script builds the document on load. Read as it was sent, it is 48,522 bytes of HTML containing no article at all.

Nothing in this post starts a browser, so the two paths are the two modes of the suite's `FixtureFetcher` in `tests/conftest.py`. With `render=False` it returns the saved file, which is what a plain fetch gets. With `render=True` it passes the file through `hydrate()`, which reads the inline payload and writes the article markup into the root element, a deterministic stand-in for what the page's script does in a browser. `snippets/browse_tools.py` sends both through the real `browse_url` tool and prints the fields the comparison turns on:

```
   {"url": "https://example.com/app/rendering-budgets", "method": "markdownify", "raw_bytes": 48522, "clean_bytes": 46, "reduction_percent": 99.9, "word_count": 6}
   {"url": "https://example.com/app/rendering-budgets", "method": "trafilatura", "raw_bytes": 50063, "clean_bytes": 1558, "reduction_percent": 96.9, "word_count": 265}
```

Note the trap in the first line. A 99.9 percent reduction looks like a triumph and is a total failure: `snippets/extraction.py` prints the whole extracted text, `'Rendering budgets, measured | Northwind Review'`, the contents of the `<title>` element. Any metric that only reports how much was discarded can be maximized by discarding everything. That is why the server reports both byte counts, the word count, and the method that produced them.

Rendering the same page adds 1,541 bytes to the document, 3.2 percent, and turns six words into 265.

## 3. Driving a headless browser

Two decisions in [src/research_browser/browser.py](../../code/17-research-browser/src/research_browser/browser.py) carry the rest of the project, and neither is about Playwright.

**The fetcher is a `Protocol`, not a class the tools import.** Everything that needs a page asks `browser.require()` for whatever fetcher is installed, and this is everything it asks of one:

```python
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
```

That is the entire seam: a name and three methods. The real implementation, `PlaywrightFetcher`, drives Chromium; the test suite installs `FixtureFetcher`, which reads saved HTML off disk. Nothing in `tools.py` or `research.py` knows which is running, so the suite exercises the real server code with no network, no browser binary, and no site that might change under it. The idea worth taking into any server: the expensive external dependency sits behind a small protocol, and the tests install the cheap implementation.

**Playwright is imported lazily, inside `start()`.** Importing the server must not require the package, so that the suite and any tooling can import it on a machine without Playwright, and so that a missing installation is reported by `start()` with a message naming the two steps, the package and the browser binaries, instead of by a bare `ModuleNotFoundError` at import. Inside `PlaywrightFetcher.start()`:

```python
        try:
            # Lazy on purpose -- see the module docstring.
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "playwright is not installed. Run `uv sync` and then "
                "`uv run playwright install chromium`."
            ) from exc
```

`snippets/fetcher_seam.py` checks both halves. It prints `'playwright' in sys.modules after importing the server: False`, and `PageFetcher asks for: close, fetch, screenshot, name`.

The browser itself is owned by the server's lifespan, one Chromium per process. The body of `lifespan()` in [src/research_browser/app.py](../../code/17-research-browser/src/research_browser/app.py):

```python
    await browser.start()
    log.info("research browser ready (fetcher=%s)", browser.require().name)
    try:
        yield browser
    finally:
        await browser.stop()
        cache.clear()
        log.info("research browser stopped")
```

Launching Chromium once per process rather than once per tool call is the reason the browser is a singleton. The `finally` is load-bearing: a Chromium that outlives its server is a leaked process the host has no way to reap. And `browser.start()` is a no-op when a fetcher has already been injected, which is what lets the suite run this same lifespan. The script runs it over an in-memory client with the fixture fetcher installed and prints what it logged:

```
   logged: INFO research-browser: research browser ready (fetcher=fixture)
   logged: INFO research-browser: research browser stopped
   the injected fetcher was closed by the lifespan: False
   playwright imported at any point: False
```

`stop()` closes only a fetcher the session started itself, so the suite's fetcher survives and can be inspected afterward.

Three smaller choices in `PlaywrightFetcher`, each a bug first:

- **`wait_until="domcontentloaded"` plus a fixed 0.5 second settle**, not `"networkidle"`. Waiting for the network to go quiet means waiting for analytics beacons and long-polling sockets that may never stop. The settle gives a client-rendered page's script time to build its document; whether half a second is enough depends on the page, and nothing in the suite measures it, because the suite never starts a browser. `screenshot()` waits for `"load"` instead.
- **A new page per call, closed in a `finally`.** A page that is not closed is a tab that never goes away, and over a long-lived server Chromium eventually refuses to open new ones.
- **Logging to standard error, configured at the top of `app.py`.** Under the standard input and output (stdio) transport, standard output is the protocol channel. [Post 04](../04-transports/index.md) has the full anatomy of that failure.

## 4. Extracting the article

![A vertical pipeline inside one tools/call. The call crosses the tool boundary into a cache lookup; a hit short-circuits down the right-hand side straight to the result without touching the browser. A miss continues through a Chromium navigation and settle, a read of the rendered document, an extract step that tries trafilatura, then markdownify, then reports empty, and a cache write, before rejoining the same result box.](diagrams/02-extraction-pipeline.svg)

*The cache decides how much of the pipeline runs, and a hit returns at the tool boundary.*

[src/research_browser/extract.py](../../code/17-research-browser/src/research_browser/extract.py) tries two extractors in order and reports which one won.

`trafilatura` is built for articles: it looks for the main text and drops everything around it. Its options are fixed in `_try_trafilatura()` so the numbers stay reproducible:

```python
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
```

`markdownify` is the fallback for pages `trafilatura` declines: documentation, forums, search results, directories, anything not shaped like an article. It keeps more boilerplate, which is why it is second and not first. The decision between them, in `extract()`:

```python
    text = _try_trafilatura(html, url)
    method = "trafilatura"

    if text is None or len(text.strip()) < MIN_USEFUL_CHARS:
        fallback = _try_markdownify(html)
        # Only accept the fallback if it actually did better. On a JavaScript
        # shell the fallback may find only the title; the word count, not the
        # method, is what shows that.
        if fallback is not None and len(fallback.strip()) > len((text or "").strip()):
            text = fallback
            method = "markdownify"

    text = _tidy(text or "")
    if not text:
        method = "empty"
```

`MIN_USEFUL_CHARS` is 200: below that, an extraction is a nav bar and a copyright line. Read the comment in the middle against section 2's measurement, because the two disagree. On the committed shell `trafilatura` keeps nothing, the fallback keeps the 46 bytes of the title, 46 is more than nothing, and the method comes back `markdownify` with six words, not `empty`. The label `empty` is reserved for a document in which both extractors find nothing at all; the script prints `extract('')` as `method=empty  raw_bytes=0  reduction_percent=0.0  word_count=0`, with no division by zero. So the method alone does not flag a shell. The word count and the byte counts beside it do, which is why all of them travel together.

**Here is the defect that shipped in the previous edition, and it looked like a success.** The old code passed `strip=["script", "style", "nav", "footer", "header"]` to `markdownify`. That argument removes the *tag* and keeps the *text*. On a `<script>` element the text is the page's JavaScript, or its serialized state; on a `<style>` element it is the style sheet. `snippets/extraction.py` runs the old call against the client-rendered fixture:

```
   words returned:                 2539
   words inside <script> elements: 1777 (the __NEXT_DATA__ store alone: 1433)
   words inside <style> elements:  673
   leaked strings: ['function', 'dataLayer', 'addEventListener', 'querySelector', 'buildId']
```

That is a plausible word count with every appearance of a clean extraction, and a model reading it has been handed `addEventListener`, a serialized application store and a style sheet and told it is an article. The elements have to be removed from the tree before the converter sees the document, as `_try_markdownify()` now does:

```python
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(list(_DROP_ELEMENTS)):
            element.decompose()
        return markdownify(str(soup), heading_style="ATX")
```

with `_DROP_ELEMENTS` covering `script`, `style`, `noscript`, `template`, `svg`, `iframe`, `form`, `nav`, `header`, `footer`, and `aside`. The test `test_the_fallback_never_returns_javascript_as_prose` in `tests/test_extract.py` exists only to keep this fixed, and it asserts on the strings that used to leak:

```python
    result = extract(spa_shell_html, url="https://example.com/app/x")

    for javascript in ("function", "dataLayer", "addEventListener", "querySelector"):
        assert javascript not in result.text, javascript

    # Nor the serialized store, which is the other large inline script.
    assert "buildId" not in result.text
    assert "__NEXT_DATA__" not in result.text
```

The fixed fallback on the same shell prints `method=markdownify  words=6  leaked strings: []`.

## 5. Measuring what was saved

Every extraction carries its own receipt. `Extraction` is a dataclass with class-body annotations, which is not a style preference: the software development kit (SDK) builds a tool's output schema from the return type through `get_type_hints()`, and a class that only assigns attributes inside `__init__` publishes `outputSchema: null` with no exception and no warning. [Post 06](../06-tools-in-depth/index.md) prints that failure in full.

```python
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
```

The ratio is derived rather than asserted, so it cannot drift away from the two counts it describes:

```python
    clean_bytes = len(text.encode("utf-8"))
    reduction = 0.0 if raw_bytes == 0 else (1 - clean_bytes / raw_bytes) * 100
```

`reduction_percent` is that value rounded to one decimal, and `test_the_ratio_is_internally_consistent` recomputes it from the two counts independently, because a hand-maintained percentage is a number that eventually lies.

**These are the measured numbers**, as `snippets/extraction.py` prints them under `trafilatura` 2.1.0 and `markdownify` 1.2.3, the versions the project's lock pins. The suite asserts the raw byte counts exactly, because they are a property of the files in this repository, and everything downstream of the extractor as a band, because that is a property of a pinned dependency:

| Fixture | Raw | Kept | Discarded | Method | Words |
|---|---:|---:|---:|---|---:|
| `article.html` | 76,201 B | 4,126 B | 94.6 % | `trafilatura` | 707 |
| `nav-heavy.html` | 243,128 B | 860 B | 99.6 % | `trafilatura` | 134 |
| `spa-shell.html`, fetched | 48,522 B | 46 B | 99.9 % | `markdownify` | 6 |
| `spa-shell.html`, rendered | 50,063 B | 1,558 B | 96.9 % | `trafilatura` | 265 |

The hero figure is drawn from the first two rows, on one area scale. The relationship worth internalizing is in the comparison, not in either row alone. The script prints the ratios: the portal page is 3.19 times heavier than the plain article and keeps 0.21 times as many bytes and 0.19 times as many words, so its raw document is 282.7 times its kept text against the article's 18.5. **The worse the page, the more the extractor is worth**, which is the opposite of the intuition that a heavy page is a hard case.

Where do those bytes go? The same script measures the whole `<style>` and `<script>` elements of each file. On `article.html`, inline style sheets are 49.2 percent of the document and scripts are 32.1 percent. On `nav-heavy.html`, scripts alone are 74.7 percent, most of it a serialized store. Almost none of the weight of these pages is the words on them.

Here is the receipt as it reaches the client, from `snippets/browse_tools.py` calling `browse_url` on the article page, with `text` shortened by the script:

```json
{
  "url": "https://example.com/blog/what-a-page-costs",
  "title": "What a web page actually costs a language model",
  "text": "What a web page actually costs a language model\nA reader opening a news article ...",
  "method": "trafilatura",
  "raw_bytes": 76201,
  "clean_bytes": 4126,
  "reduction_percent": 94.6,
  "word_count": 707,
  "returned_words": 707,
  "truncated": false,
  "from_cache": false
}
```

That object is the result's `structuredContent`; on the wire the field is `structuredContent`, and the Python SDK exposes it as `structured_content`. The same result carries one text block whose JSON parses to the same object, which the script checks and prints as `True`, and a `_meta` of `{"io.modelcontextprotocol/serverInfo": {"name": "research-browser", "version": ""}}`, with an empty version because `app.py` sets none. The shape is published as a JSON Schema on `tools/list`, whose `browse_url` entry lists all eleven fields from `url` to `from_cache`, so a client knows `raw_bytes` and `clean_bytes` exist before it calls the tool, and a host can show them in its own interface without parsing prose.

## 6. Caching what was already paid for

Browsing the same URL twice in one research session costs a second Chromium navigation and a second extraction pass, and returns identical bytes. [src/research_browser/cache.py](../../code/17-research-browser/src/research_browser/cache.py) is bounded by two things, and both bounds matter.

**Time to live (TTL), ten minutes.** Long enough to cover a research session, short enough that nobody is served yesterday's news.

**A maximum of a hundred entries, oldest evicted first.** Without a ceiling, a long-running server that browses widely holds every page it ever saw. Eviction is by insertion time and not by last access: least-recently-used needs bookkeeping on every read, and at this size the simpler rule is enough. One refinement is worth copying, because it is the difference between a cache that helps and one that thrashes:

```python
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
```

Only the removal of a live entry counts as an eviction; clearing stale ones does not. The clock is injected, which is what makes the TTL testable without sleeping: `tests/test_cache.py` drives a `FakeClock` by hand, and `snippets/cache_bounds.py` uses the same clock to print each bound:

```
2. the age bound (ttl_seconds=600)
   599 s after the put: hit
   601 s after the put: miss; entries left: 0

3. the count bound (max_entries=3, one put per second)
   after four puts, still cached: [1, 2, 3]; evictions=1

4. full, with one entry already stale (ttl_seconds=10, max_entries=2)
   still cached: ['fresh', 'newest']; evictions=0
```

A suite that sleeps to test a TTL is a suite nobody runs. Through the tool, the same script browses one URL twice and then with `use_cache` set to false: `from_cache` comes back `False`, `True`, `False`, for two navigations.

**One note about the protocol.** This is a server-side cache and nothing more. Revision 2026-07-28 does add caching hints to results, `ttlMs` and `cacheScope` from `CacheableResult`, and the specification's Caching page lists the six methods whose complete results must carry them: `server/discover`, `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and `resources/read`. `CallToolResult` does not extend `CacheableResult`, so a `tools/call` result carries neither field and there is no protocol field in which to tell the client about this cache. The SDK's own types agree, as the script prints them:

```
   ListToolsResult ['_meta', 'ttlMs', 'cacheScope', 'nextCursor', 'tools', 'resultType']
   CallToolResult  ['_meta', 'content', 'structuredContent', 'isError', 'resultType']
```

What the tool can do, and does, is report `from_cache` in its own structured result, so the model can tell a fresh read from a reused one.

The counters survive `clear()` on purpose: hits and misses describe how the cache has performed over the process's life, and a manual flush is not a reason to forget that. The script prints `hits=1 misses=1` both before and after a `clear()` that removed one entry.

## 7. Screenshots come back as images

The previous edition of this server returned a screenshot as a list of dictionaries. `snippets/screenshots.py` rebuilds that return on a throwaway server under `mcp==2.3.0`:

```python
    @old.tool()
    async def screenshot_page(url: str) -> list:
        return [
            {"type": "text", "text": f"Screenshot of {url} ..."},
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
        ]
```

The declared return type is `list`, so the SDK treats each dictionary as data, not as a content block, and serializes it to JSON inside a **text** block of its own:

```
   outputSchema: null
   content block types: ['text', 'text']
   second block's text: "{\n  \"type\": \"image\",\n  \"data\": \"iVBORw0KGgo=\",\n  \"mimeType\": \"image/png\"\n}"
```

The host receives the base64 as prose. No picture appears anywhere, a real screenshot becomes kilobytes of characters the model cannot look at, and the tool publishes no schema either.

The specification's `ContentBlock` union is `TextContent | ImageContent | AudioContent | ResourceLink | EmbeddedResource`, and an image block, with `type`, `data` (base64) and `mimeType` required, is a first-class member of it.

Getting a real image block *and* a published output schema takes one trick, and it is worth knowing beyond this project: annotate the return as `Annotated[CallToolResult, ScreenshotMeta]`. The SDK reads the `CallToolResult` arm as "the handler owns the content blocks" and the second arm as "derive the output schema from this". The signature in [src/research_browser/tools.py](../../code/17-research-browser/src/research_browser/tools.py):

```python
async def screenshot_page(
    url: str,
    full_page: bool = False,
) -> Annotated[CallToolResult, ScreenshotMeta]:
```

and the end of its body:

```python
    meta = ScreenshotMeta(
        url=shot.url,
        width=shot.width,
        height=shot.height,
        png_bytes=len(shot.png),
        full_page=full_page,
    )

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=f"Screenshot of {shot.url} at {shot.width}x{shot.height}.",
            ),
            ImageContent(
                type="image",
                data=base64.b64encode(shot.png).decode("ascii"),
                mime_type="image/png",
            ),
        ],
        structured_content=asdict(meta),
    )
```

The suite's fixture fetcher returns a real one-pixel Portable Network Graphics (PNG) file, so the script can call the real tool without a browser:

```
1. screenshot_page, as tools/list publishes it
   outputSchema: title=ScreenshotMeta  properties=['url', 'width', 'height', 'png_bytes', 'full_page']

2. one call
   content block types: ['text', 'image']
   text block:  {"type": "text", "text": "Screenshot of https://example.com/blog/what-a-page-costs at 1280x720."}
   image block: {"type": "image", "data": "iVBORw0KGgoAAAANSUhEUgAA...", "mimeType": "image/png"}
   data decodes to a PNG: True; base64 in the text block: False
   structuredContent: {"url": "https://example.com/blog/what-a-page-costs", "width": 1280, "height": 720, "png_bytes": 70, "full_page": false}
   pixels in structuredContent: False
```

The published schema comes from `ScreenshotMeta`, and it deliberately does **not** carry the pixels: duplicating a real screenshot's base64 into `structuredContent` beside the image block is a cost with no benefit. The suite asserts the blocks and the metadata in `test_screenshot_returns_a_real_image_content_block` and `test_screenshot_also_publishes_structured_metadata`, including the absence of the pixels, and `test_every_tool_publishes_an_output_schema` asserts that the schema is published. Decoding the data and checking the PNG signature is the part that catches a nasty class of bug: a string that is valid base64 and decodes to something that is not an image at all.

## 8. Bounding the result, and the resource-link alternative

Extraction is not a bound. A long documentation page can survive it and still be fifteen thousand words, so `browse_url` caps what it returns and says so in the result:

```python
    max_words = max(100, min(max_words, MAX_MAX_WORDS))
    extraction, from_cache = await load_page(url, use_cache=use_cache)

    words = extraction.text.split()
    truncated = len(words) > max_words
    text = " ".join(words[:max_words]) if truncated else extraction.text
```

The default is 4,000 words and the ceiling is 20,000, and `truncated`, `word_count`, and `returned_words` all travel back so nobody has to guess whether the whole page arrived. The clamp on the caller's own argument matters too, because `max_words` is supplied by the model. `snippets/browse_tools.py` asks for both extremes on the 707-word article:

```
   max_words=5        truncated=True   word_count=707  returned_words=100
   max_words=1000000  truncated=False  word_count=707  returned_words=707
```

A request for five words is raised to the floor of 100, and a request for a million is held to the ceiling, which this page never reaches.

For anything larger there are two better tools than a bigger cap. `search_in_page` returns only the paragraphs that contain a phrase, which is cheaper than reading a page whose shape is already known. And `summarize_page`, which [post 18](../18-server-side-models/index.md) builds, returns a summary of the page instead of the page.

There is a third option the protocol offers and this project does not use. A tool may return a **resource link** instead of content: a `ContentBlock` of type `resource_link` carrying a Uniform Resource Identifier (URI) the client can read later through `resources/read`. It carries the full resource field set, so `uri` and `name` are required. `snippets/screenshots.py` builds one with the SDK's own `ResourceLink` type and prints it as it would travel:

```json
{"name": "what-a-page-costs", "uri": "browser://page/what-a-page-costs", "mimeType": "text/plain", "type": "resource_link"}
```

That turns a giant tool result into a pointer, and it moves the decision about how much to read from the server to the application. Two things make it the wrong default here. The Tools page notes that resource links returned by tools are not guaranteed to appear in the results of `resources/list`, so a client cannot count on finding the URI in a listing it already has, and has to be willing to read one it was never shown. And a research pass needs the text now, in the same turn, to work with it. Resource links suit an artifact that is large, durable, and optional to read: a generated report, a captured log, a downloaded dataset. They do not suit a tool whose whole purpose is to put words in front of the model this turn.

## 9. When not to build this

Three cases where a browsing server is the wrong answer.

**The site has an application programming interface (API).** A JSON endpoint gives structured data with no extraction, no rendering, and no heuristics deciding what the article is. Driving a browser against a site that publishes an API is choosing the fragile path on purpose.

**Many pages are needed at once.** Every page costs a navigation plus the settle, and the research loop [post 18](../18-server-side-models/index.md) builds visits its pages one after another. A workload of hundreds of pages wants a queue, a worker pool, and probably the Tasks extension of [post 09](../09-tasks/index.md) rather than a tool call that blocks.

**The server sits inside a security boundary that matters.** This server will fetch any Uniform Resource Locator (URL) it is given, and the URL usually comes from a model, which may be acting on text it read on a previous page. That is server-side request forgery with extra steps. Binding the HTTP transport to loopback, the default in `src/research_browser/__main__.py`, limits who can call the server; it does nothing about which URLs the browser opens, and the project has no URL allowlist. [Post 19](../19-security/index.md) is the one to read before deploying this anywhere interesting.

## 10. Make it run: the project and the scripts behind this post

The project is the post's code, and its suite needs no network, no API key, and no browser binary. From inside `code/17-research-browser/`:

```bash
uv sync --extra dev                       # once: the environment, with the test tools
uv run --extra dev pytest                 # 75 passed, 1 skipped; no browser anywhere
```

```
tests\test_cache.py ..........                                           [ 13%]
tests\test_extract.py .............                                      [ 30%]
tests\test_server.py ...................................                 [ 76%]
tests\test_summarize.py ................s.                               [100%]

======================== 75 passed, 1 skipped in 2.77s ========================
```

The count is the result; the seconds vary by machine. The skip is `test_a_key_selects_the_provider_when_the_sdk_is_installed`, which belongs to [post 18](../18-server-side-models/index.md) and is skipped because the optional provider extra is not installed. That is the intended state: the server and its entire suite work without it.

Under a real host the server is spawned over stdio and needs Chromium present, which is the one step this post's runs never take:

```bash
uv run playwright install chromium        # the browser binary, a separate download
uv run mcp-research-browser               # stdio, what a desktop host spawns
uv run mcp-research-browser --http        # Streamable HTTP on 127.0.0.1:8000
```

Six scripts under `snippets/` produce every number, result and transcript quoted in this post. Each runs from the series root with no arguments, in a few seconds. The root environment pins `mcp[cli]==2.3.0` and the libraries the project imports, and each script imports the package and the suite's fixtures from the project's `src/` and `tests/` directories. None starts a browser, needs a host, or touches the network, and each stops whatever it started before it exits.

```bash
uv run python posts/17-research-browser/snippets/extraction.py     # sections 2, 4 and 5: the table, the ratios, the old fallback
uv run python posts/17-research-browser/snippets/fetcher_seam.py   # section 3: the lazy import, the protocol, the lifespan
uv run python posts/17-research-browser/snippets/browse_tools.py   # sections 2, 5, 8, 10 and 11: tools/list, receipts, the cap, a failure
uv run python posts/17-research-browser/snippets/cache_bounds.py   # section 6: both bounds, the counters, the result types
uv run python posts/17-research-browser/snippets/screenshots.py    # sections 7 and 8: the image block, the old list, a resource link
uv run python posts/17-research-browser/snippets/launch_failure.py # section 11: a stdio server whose browser cannot launch
```

`launch_failure.py` is both halves of its experiment: with no argument it is the client, and it starts itself again with `--serve` as the stdio server, a flag that is not meant to be run by hand.

`browse_tools.py` prints the registered surface, five tools from this post plus two from [post 18](../18-server-side-models/index.md), one resource, and one prompt:

```
   browse_url      inputs=['url', 'use_cache', 'max_words']                         outputSchema=yes  readOnly=True  destructive=False
   screenshot_page inputs=['url', 'full_page']                                      outputSchema=yes  readOnly=True  destructive=False
   search_in_page  inputs=['url', 'query', 'max_matches']                           outputSchema=yes  readOnly=True  destructive=False
   get_cache_stats inputs=[]                                                        outputSchema=yes  readOnly=True  destructive=False
   clear_cache     inputs=[]                                                        outputSchema=yes  readOnly=False  destructive=True
   summarize_page  inputs=['url', 'focus', 'max_sentences']                         outputSchema=yes  readOnly=True  destructive=False
   research_urls   inputs=['topic', 'urls', 'focus', 'max_pages', 'max_words']      outputSchema=yes  readOnly=True  destructive=False
   resources: ['browser://cache-stats']   prompts: ['research_topic']
```

`clear_cache` is the only tool that is not read-only, and it says so through `ToolAnnotations(read_only_hint=False, destructive_hint=True, ...)`. Annotations are hints: the Tools page says clients must consider them untrusted unless they come from trusted servers, and nothing stops a badly written tool from lying about itself. What backs them here is that the functions do what they claim, and `test_the_only_non_read_only_tool_is_the_one_that_clears_the_cache` asserts that the list of non-read-only tools is exactly `["clear_cache"]` rather than merely "does not include something".

## 11. What can go wrong?

Four failures, each with the symptom a reader will actually see.

**A page that will not load.** It must come back as an error result, not as a success-shaped result with an apology in a text field. `load_page()` in `src/research_browser/tools.py` wraps every fetch:

```python
    try:
        page = await browser.require().fetch(url)
    except Exception as exc:
        # The SDK turns this into an error result the model can read and react
        # to, which beats a stack trace or a success-shaped result with an
        # error message hidden in a text field.
        raise ToolError(f"could not load {url}: {exc}") from exc
```

`ToolError` is the SDK's exception for an anticipated failure, so its message reaches the model; any other exception escaping the tool would reach it only as `Error executing tool browse_url`, as [post 06](../06-tools-in-depth/index.md) shows. The fixture fetcher raises the same `net::ERR_NAME_NOT_RESOLVED` a dead link produces, and `snippets/browse_tools.py` prints what the client receives:

```
   resultType=complete  isError=True  structuredContent=None
   text: Error executing tool browse_url: could not load https://example.com/gone: net::ERR_NAME_NOT_RESOLVED
```

Read `isError` as well as `resultType`. A `resultType` of `"complete"` means the call finished, and says nothing about whether the tool succeeded. [Post 12](../12-testing-and-debugging/index.md) covers the two places a failure hides.

**A client-rendered page read without a browser.** The symptom is the first line of section 2: a 99.9 percent reduction, six words, and a method of `markdownify` rather than `empty`, because the fallback found the title. A shell that rendered nothing is diagnosable only from the counts, so a caller that reads `reduction_percent` alone reads a total failure as a triumph.

**A browser that is installed as a package and not as a binary.** `uv sync` installs Playwright's Python bindings, so the server imports and the suite passes, because neither starts a browser. The lifespan does: under a real host it calls `browser.start()` before answering anything, and when Chromium cannot launch, `PlaywrightFetcher.start()` raises `BrowserUnavailable` with a message that, as the code writes it, begins "could not launch Chromium. The Python package installs the bindings, not the browser: run `playwright install chromium` (and `playwright install-deps` on Linux)". `snippets/launch_failure.py` reproduces the shape of that failure over stdio with a stand-in fetcher whose `start()` raises the same exception, so nothing is launched:

```
   exit status:     1
   standard output: ''
   standard error ends with the traceback of: research_browser.browser.BrowserUnavailable: could not launch Chromium (stand-in for PlaywrightFetcher.start(); the real message names `playwright install chromium`)
```

The host sees a server that exited without a reply. The fix is named on the last line of the traceback the server writes to standard error, which the host collects in its server log, and it is `uv run playwright install chromium`.

**A request outside the word cap.** A model that asks `browse_url` for five words gets 100 and a model that asks for a million gets at most 20,000, both without an error; `truncated` and `returned_words` say what actually came back, and a caller that ignores them miscounts what the model has read.

## Common pitfalls

1. **Passing `strip=["script"]` to `markdownify` and thinking the scripts are gone.** It removes the tag and keeps the text, so serialized state, JavaScript and style sheets come back as prose with a healthy word count; remove the elements from the tree with `decompose()` first, and assert on the strings that used to leak.
2. **Reporting a reduction percentage as if it were a quality score.** A shell with no article reduces by 99.9 percent; report the bytes in, the bytes out, the word count, and the method, and read them together.
3. **Returning image content as a list of dictionaries.** The SDK serializes each one into a text block and publishes no schema; return a `CallToolResult` with a real `ImageContent`, annotated as `Annotated[CallToolResult, YourMeta]`.
4. **Forgetting `await page.close()`.** Every leaked page is a tab that never goes away; close it in a `finally`, and stop the browser in the lifespan's `finally` too.
5. **Making Playwright a hard import at module load.** Then the server and its suite cannot even be imported where the package is missing, and the operator sees a bare `ModuleNotFoundError` instead of a message naming `uv sync` and `uv run playwright install chromium`.
6. **Caching without a ceiling on entries.** A TTL bounds staleness, not memory, so a long-running server that browses widely holds every page it ever saw until something restarts it.

## Further reading

- Specification, *"Tools"*, revision 2026-07-28, the content block types. The `ContentBlock` union, the image block's fields, the resource link's field set, the note that tool-returned resource links need not appear in `resources/list`, and the rule that clients must treat annotations from untrusted servers as untrusted. <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>
- Specification, *"Caching"*, revision 2026-07-28. The six methods that carry `ttlMs` and `cacheScope`, and the absence of `tools/call` among them. <https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/caching>
- Playwright for Python. Navigation lifecycle events, and why `install` and `install-deps` are separate commands. <https://playwright.dev/python/>
- `trafilatura` documentation. Extraction options, and what `favor_precision` changes. <https://trafilatura.readthedocs.io/>
- MCP Python SDK, release 2.3.0. `MCPServer`, `ToolError`, the `Annotated[CallToolResult, ...]` return, and the in-memory `Client` every script and test here uses. <https://github.com/modelcontextprotocol/python-sdk>
- [code/17-research-browser/](../../code/17-research-browser/). The complete project, with the suite run in section 10.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 18 - Project 3: Server-side model calls and multi-page research](../18-server-side-models/index.md):** the other half of this server, where a page still does not fit after extraction and the server needs a model of its own, which is not the host's model.
- **[Post 06 - Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md):** the class-body annotation rule behind every dataclass in this project, the content block types, and the `Annotated[CallToolResult, ...]` return the screenshot tool depends on.
