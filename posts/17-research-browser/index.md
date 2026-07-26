# 17 · Project 3 · A deep research browser

> **TL;DR.** The valuable part of a browsing Model Context Protocol (MCP) server is not the
> fetch, it is the discard: on the two article pages committed to this project, extraction
> throws away 94.6 percent and 99.6 percent of the bytes and hands the model 707 and 134
> words instead of 76,201 and 243,128 bytes of markup. This post builds that server, drives a
> real browser so client-rendered pages are not returned empty, and makes every tool result
> carry the two byte counts it was derived from. It also fixes two defects the previous
> edition shipped: a screenshot returned as text, and JavaScript returned as prose.
>
> **After reading this you will be able to:**
> - Drive a headless browser from an MCP server without making the browser a test dependency.
> - Extract article text from a page and report the measured reduction alongside it.
> - Return a screenshot as a real image content block and still publish an output schema.
> - Cache extractions with bounds on both age and count, and know why that is not protocol caching.

![Two pairs of nested squares drawn to one area scale. On the left, a plain article template: a large outlined square of 76,201 bytes of fetched HTML with a small solid square of 4,126 bytes of kept article text in its corner. On the right, a portal page: a much larger outlined square of 243,128 bytes with a far smaller solid square of 860 bytes inside it. The right-hand page is three times heavier and yields one fifth as much text.](diagrams/01-html-to-article.svg)
*Both pairs are drawn on one scale, from byte counts the test suite asserts against committed fixtures.*

---

## 1. The brief

A user asks the host to research something. The host has a model, the model has a context
window, and the web has pages. The obvious server is a thin wrapper over an HTTP GET that
returns whatever came back.

That server is close to useless, and the reason is arithmetic. One of the pages committed to
this project is an ordinary news article: a headline, six paragraphs, a byline. As a document
it is 243,128 bytes. As prose it is 134 words. Hand the model the document and you have spent
roughly sixty thousand tokens to deliver about a hundred and eighty.

So the brief for this server is not "fetch pages". It is:

- Render the page the way a reader would see it, including whatever JavaScript builds.
- Throw away navigation, advertising, consent banners, recirculation, styling, and scripts.
- Report what was thrown away, in bytes, so the caller can check the claim.
- Never pay twice for the same URL inside one research session.
- Return a picture as a picture.

The whole project lives in [code/17-research-browser/](../../code/17-research-browser/), and
post 18 adds the second half of it. Vocabulary first, because readers arrive mid-series. The
**host** is the application the user talks to and the thing that owns the model. The
**client** is the protocol-speaking object inside the host, one per connected server. The
**server** is the process this post builds. Those three words mean exactly that throughout
the series.

**A note on the previous edition.** The earlier version of this project claimed "500 KB to
5 MB of raw HTML" and "maybe 10-50 KB" of article, with no measurement behind either figure.
This edition commits three real HTML pages to
[tests/fixtures/](../../code/17-research-browser/tests/fixtures/) and asserts the numbers
against them, so a dependency bump that moves the ratio fails a test rather than quietly
making the post wrong.

## 2. Why a plain fetch is not enough

Start with the failure, because it is the one that will waste your afternoon: the page loads,
the extractor runs, and you get six words back.

![Two columns for one client-rendered URL. On the left a plain HTTP GET returns the shell the server sent: an empty root element and an inline JSON blob, 48,522 bytes of document from which the extractor recovers 46 bytes and six words, all of them from the title element. On the right a headless browser navigates, waits half a second and reads the document back after the page script has run: 50,063 bytes, only three percent heavier, from which the extractor recovers 1,558 bytes and 265 words of article.](diagrams/03-fetch-vs-render.svg)
*The same URL and the same extractor. The only difference is whether the page's own script ran.*

The third fixture in this project,
[spa-shell.html](../../code/17-research-browser/tests/fixtures/spa-shell.html), is a
client-rendered page: the server sends a shell with an empty `<div id="root">` and the article
as an inline JSON payload, and the page's own script builds the document on load. Fetched
without a browser it is 48,522 bytes of HTML containing no article at all.

Here is what each path returns through the real `browse_url` tool, with only the long `text`
field elided:

```json
{ "url": "https://example.com/app/rendering-budgets",
  "method": "markdownify", "raw_bytes": 48522, "clean_bytes": 46,
  "reduction_percent": 99.9, "word_count": 6 }
```

```json
{ "url": "https://example.com/app/rendering-budgets",
  "method": "trafilatura", "raw_bytes": 50063, "clean_bytes": 1558,
  "reduction_percent": 96.9, "word_count": 265 }
```

Note the trap in the first result. A 99.9 percent reduction looks like a triumph and is in
fact a total failure: the six words are the contents of the `<title>` element. Any metric that
only reports how much you discarded can be maximized by discarding everything. That is why the
server reports both counts and the method that produced them, and why an extraction that found
nothing is labelled `empty` rather than dressed up as a success.

Rendering the same page costs 1,541 extra bytes of document, about three percent, and turns
six words into 265.

## 3. Driving a headless browser

Two decisions in [browser.py](../../code/17-research-browser/src/research_browser/browser.py)
carry the rest of the project, and neither is about Playwright.

**The fetcher is a `Protocol`, not a class the tools import.** Everything that needs a page
asks `browser.require()` for whatever fetcher is installed:

```python
class PageFetcher(Protocol):
    name: str

    async def fetch(self, url: str, *, wait_until: str = DEFAULT_WAIT_UNTIL,
                    timeout_ms: int = DEFAULT_TIMEOUT_MS) -> PageResult: ...

    async def screenshot(self, url: str, *, full_page: bool = False,
                         timeout_ms: int = DEFAULT_TIMEOUT_MS) -> ScreenshotResult: ...

    async def close(self) -> None: ...
```

That is the entire seam. The real implementation drives Chromium; the test suite installs one
that reads saved HTML off disk. Nothing in `tools.py` or `research.py` knows which is running,
so all seventy-five tests execute the genuine protocol path with no network, no browser binary,
and no site that might change under them. If you take one idea from this post into your own
server, take this one: the expensive external dependency should sit behind a two-method
protocol, and the tests should install the cheap one.

**Playwright is imported lazily, inside `start()`.** Importing the module must not require the
package, because `pip install playwright` is only half of the installation. The browser
binaries are a separate download of several hundred megabytes, and a server that cannot import
its own modules cannot tell you what is missing:

```python
try:
    from playwright.async_api import async_playwright
except ImportError as exc:
    raise BrowserUnavailable(
        "playwright is not installed. Run `uv sync` and then "
        "`uv run playwright install chromium`."
    ) from exc
```

The browser itself is owned by the server's lifespan, one Chromium per process, in
[app.py](../../code/17-research-browser/src/research_browser/app.py):

```python
@asynccontextmanager
async def lifespan(server: MCPServer) -> AsyncIterator[BrowserSession]:
    await browser.start()
    log.info("research browser ready (fetcher=%s)", browser.require().name)
    try:
        yield browser
    finally:
        await browser.stop()
        cache.clear()
```

Launching Chromium costs a second or two, so it happens once rather than once per tool call.
The `finally` is load-bearing: a Chromium that outlives its server is a leaked process the
host has no way to reap. And `browser.start()` is a no-op when a fetcher has already been
injected, which is exactly what lets the test suite run this same lifespan.

Three smaller choices, each of which was a bug first:

- **`wait_until="domcontentloaded"` plus a fixed 0.5 second settle**, not `"networkidle"`.
  Waiting for the network to go quiet means waiting for analytics beacons and long-polling
  sockets that may never stop. The settle is what turns an empty `<div id="root">` into an
  article.
- **A new page per call, closed in a `finally`.** A page that is not closed is a tab that never
  goes away, and over a long-lived server Chromium eventually refuses to open new ones.
- **Logging to standard error, configured before anything else runs.** Under the standard
  input and output (stdio) transport, standard output *is* the protocol channel.
  [Post 04](../04-transports/index.md) has the full anatomy of that failure.

## 4. Extracting the article

![A vertical pipeline inside one tools/call. The call crosses the tool boundary into a cache lookup; a hit short-circuits down the right-hand side straight to the result without touching the browser. A miss continues through a Chromium navigation and settle, a read of the rendered document, an extract step that tries trafilatura then markdownify and reports empty if both fail, and a cache write, before rejoining the same result box.](diagrams/02-extraction-pipeline.svg)
*The cache decides how much of the pipeline runs, and the short-circuit returns at the tool boundary.*

[extract.py](../../code/17-research-browser/src/research_browser/extract.py) tries two
extractors in order and reports which one won.

`trafilatura` is built for articles: it finds the main text node and drops everything around
it. Its options are pinned so the numbers stay reproducible.

```python
text = trafilatura.extract(
    html, url=url or None, output_format="txt",
    include_comments=False, include_links=False,
    include_images=False, include_tables=True,
    favor_precision=True,
)
```

`markdownify` is the fallback for pages `trafilatura` declines: documentation, forums, search
results, directories, anything not shaped like an article. It keeps more boilerplate, which is
precisely why it is second and not first.

**Here is the defect that shipped in the previous edition, and it looked like a success.**
The old code passed `strip=["script", "style", "nav", "footer", "header"]` to `markdownify`.
That argument removes the *tag* and keeps the *text*. On a `<script>` element, the text is the
page's JavaScript. Run it against the client-rendered fixture in this repository and you get
577 words of minified bundle back, with a plausible word count and every appearance of a clean
extraction. A model reading that has been handed `addEventListener` and `dataLayer` and told
it is an article.

Elements have to be removed from the tree before the converter sees the document:

```python
soup = BeautifulSoup(html, "html.parser")
for element in soup(list(_DROP_ELEMENTS)):
    element.decompose()
return markdownify(str(soup), heading_style="ATX")
```

with `_DROP_ELEMENTS` covering `script`, `style`, `noscript`, `template`, `svg`, `iframe`,
`form`, `nav`, `header`, `footer`, and `aside`. There is a test whose only job is to keep this
fixed, and it asserts on the strings that used to leak:

```python
def test_the_fallback_never_returns_javascript_as_prose(spa_shell_html: str) -> None:
    result = extract(spa_shell_html, url="https://example.com/app/x")
    for javascript in ("function", "dataLayer", "addEventListener", "querySelector"):
        assert javascript not in result.text, javascript
    assert "buildId" not in result.text
    assert "__NEXT_DATA__" not in result.text
```

The last case matters as much as the other two. When both extractors come back empty, the
method is reported as `empty`:

```python
text = _tidy(text or "")
if not text:
    method = "empty"
```

A page that rendered nothing is a real answer, and it is the answer a JavaScript shell gives
you when it is fetched without a browser. Inventing content for it, or reporting an empty
string as a successful extraction, converts a diagnosable problem into a mysterious one.

## 5. Measuring what you saved

Every extraction carries its own receipt. `Extraction` is a dataclass with class-body
annotations, which is not a style preference: the software development kit (SDK) builds the
output schema by calling `get_type_hints()` on the return type, and a class that only assigns
attributes inside `__init__` publishes `outputSchema: null` with no exception and no warning.
[Post 06](../06-tools-in-depth/index.md) has that failure in full.

```python
@dataclass
class Extraction:
    url: str
    title: str
    text: str
    method: str
    raw_bytes: int
    clean_bytes: int
    reduction_percent: float
    word_count: int
```

The ratio is derived rather than asserted, so it cannot drift away from the two counts it
describes:

```python
clean_bytes = len(text.encode("utf-8"))
reduction = 0.0 if raw_bytes == 0 else (1 - clean_bytes / raw_bytes) * 100
```

and a test checks that derivation independently, because a hand-maintained percentage is a
number that lies eventually:

```python
def test_the_ratio_is_internally_consistent(article_html: str) -> None:
    result = extract(article_html, url="https://example.com/blog/x")
    expected = (1 - result.clean_bytes / result.raw_bytes) * 100
    assert abs(result.reduction_percent - expected) < 0.05
    assert result.clean_bytes == len(result.text.encode("utf-8"))
```

**These are the measured numbers**, produced by running the suite against the committed
fixtures. Raw byte counts are asserted exactly, because they are a property of the files in
this repository. Everything downstream of the extractor is asserted as a band, because it is a
property of a pinned dependency rather than of the repository:

| Fixture | Raw | Kept | Discarded | Method | Words |
|---|---:|---:|---:|---|---:|
| `article.html` | 76,201 B | 4,126 B | 94.6 % | `trafilatura` | 707 |
| `nav-heavy.html` | 243,128 B | 860 B | 99.6 % | `trafilatura` | 134 |
| `spa-shell.html`, fetched | 48,522 B | 46 B | 99.9 % | `markdownify` | 6 |
| `spa-shell.html`, rendered | 50,063 B | 1,558 B | 96.9 % | `trafilatura` | 265 |

The hero diagram at the top of this post is drawn from the first two rows, on one area scale.
The relationship worth internalizing is in the comparison, not in either row alone. The portal
page is three times heavier than the plain article and yields one fifth as much text, so it
reduces by 283 times against the article's 18. **The worse the page, the more the extractor is
worth**, which is the opposite of the intuition that a heavy page is a hard case.

Where do those bytes go? Measuring the fixtures by element gives an answer that is itself
worth knowing: on `article.html`, inline CSS is 49.2 percent of the document and scripts are
32.1 percent. On `nav-heavy.html`, scripts alone are 74.7 percent, most of it a serialized
store. Almost none of the weight of a modern page is the words on it.

Here is the receipt as it reaches the caller, from a real `browse_url` call against the article
fixture, with `text` elided:

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

That object is the tool's `structuredContent`; the Python SDK exposes the field as
`structured_content`. It is published as a JSON Schema on `tools/list`, so the model knows
`raw_bytes` and `clean_bytes` exist before it ever calls the tool, and a host can show them in
its own interface without parsing prose.

## 6. Caching what you already paid for

Browsing the same URL twice in one research session costs a second Chromium navigation, a
second extraction pass, and returns identical bytes.
[cache.py](../../code/17-research-browser/src/research_browser/cache.py) is bounded by two
things, and both bounds matter.

**Time to live (TTL), ten minutes.** Long enough to cover a research session, short enough
that nobody is served yesterday's news.

**A maximum of a hundred entries, oldest evicted first.** Without a ceiling, a long-running
server that browses widely holds every page it ever saw. Eviction is by insertion time and not
by last access: least-recently-used bookkeeping runs on every read, and at this size the
simpler rule behaves the same. One refinement is worth copying, though, because it is the
difference between a cache that helps and one that thrashes:

```python
def _evict_oldest(self) -> None:
    stale = [url for url, entry in self._store.items() if self._expired(entry)]
    if stale:
        for url in stale:
            del self._store[url]
        return
    oldest = min(self._store, key=lambda url: self._store[url].stored_at)
    del self._store[oldest]
    self._evictions += 1
```

Expired entries are dead weight. Clear those before you throw away a live one.

The clock is injected, which is what makes the TTL testable without sleeping:

```python
def test_an_entry_past_its_ttl_is_a_miss() -> None:
    clock = FakeClock()
    cache = PageCache(ttl_seconds=600.0, clock=clock)
    cache.put("https://example.com/a", make_extraction("https://example.com/a"))
    clock.advance(599)
    assert cache.get("https://example.com/a") is not None
    clock.advance(2)
    assert cache.get("https://example.com/a") is None
```

A suite that sleeps to test a TTL is a suite nobody runs.

**One honest note about the protocol.** This is a server-side cache and nothing more. Revision
2026-07-28 does add caching hints to results, `ttlMs` and `cacheScope` from `CacheableResult`,
but the specification lists exactly six methods that carry them: `server/discover`,
`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`, and
`resources/read`. `CallToolResult` does not extend `CacheableResult`, so a `tools/call` result
carries neither field and there is nothing to tell the client about this cache. What the tool
can do, and does, is report `from_cache` in its own structured result, so the model can tell a
fresh read from a reused one.

The counters survive `clear()` on purpose. Hits and misses describe how the cache has performed
over the process's life, and a manual flush is not a reason to forget that.

## 7. Screenshots come back as images

The previous edition of this server returned a screenshot like this:

```python
return [
    {"type": "text", "text": f"Screenshot of {result.url} ..."},
    {"type": "image", "data": result.data_base64, "mimeType": "image/png"},
]
```

The tool's declared return type was `list`, so the SDK serialized those dictionaries to JSON
and put the JSON inside a **text** block. The host received a few kilobytes of base64 as prose.
No picture appeared anywhere, and the model spent thousands of tokens on characters it could
not look at.

The specification's `ContentBlock` union is `TextContent | ImageContent | AudioContent |
ResourceLink | EmbeddedResource`, and an image block is a first-class member of it:

```json
{ "type": "image", "data": "iVBORw0KGgo...", "mimeType": "image/png" }
```

Getting a real image block *and* a published output schema takes one trick, and it is worth
knowing beyond this project. Annotate the return as `Annotated[CallToolResult, ScreenshotMeta]`.
The SDK reads the `CallToolResult` arm as "the handler owns the content blocks" and the second
arm as "derive the output schema from this":

```python
@mcp.tool(title="Screenshot a page", annotations=READ_ONLY)
async def screenshot_page(url: str, full_page: bool = False
                          ) -> Annotated[CallToolResult, ScreenshotMeta]:
    shot = await browser.require().screenshot(url, full_page=full_page)
    meta = ScreenshotMeta(url=shot.url, width=shot.width, height=shot.height,
                          png_bytes=len(shot.png), full_page=full_page)
    return CallToolResult(
        content=[
            TextContent(type="text",
                        text=f"Screenshot of {shot.url} at {shot.width}x{shot.height}."),
            ImageContent(type="image",
                         data=base64.b64encode(shot.png).decode("ascii"),
                         mime_type="image/png"),
        ],
        structured_content=asdict(meta),
    )
```

The published schema comes from `ScreenshotMeta`, which carries `url`, `width`, `height`,
`png_bytes`, and `full_page`. It deliberately does **not** carry the pixels: duplicating a
megabyte of base64 into `structuredContent` alongside the image block is a cost with no
benefit. The test asserts all of that, including the absence:

```python
async def test_screenshot_returns_a_real_image_content_block() -> None:
    async with Client(mcp) as c:
        result = await c.call_tool("screenshot_page", {"url": ARTICLE_URL})
    kinds = [block.type for block in result.content]
    assert kinds == ["text", "image"]
    image = result.content[1]
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data).startswith(b"\x89PNG\r\n\x1a\n")
    assert "base64" not in result.content[0].text
    assert image.data not in result.content[0].text
```

Decoding the data and checking the PNG signature is the part that catches a genuinely nasty
class of bug: a string that is valid base64 and decodes to something that is not an image at
all.

## 8. Bounding the result, and the resource-link alternative

Extraction is not a bound. A long documentation page can survive it and still be fifteen
thousand words, so `browse_url` caps what it returns and says so in the result:

```python
max_words = max(100, min(max_words, MAX_MAX_WORDS))
words = extraction.text.split()
truncated = len(words) > max_words
text = " ".join(words[:max_words]) if truncated else extraction.text
```

The default is 4,000 words, the ceiling is 20,000, and `truncated`, `word_count`, and
`returned_words` all travel back so nobody has to guess whether they got the whole page. The
clamp on the caller's own argument matters too, since `max_words` is model-supplied and a model
that asks for a million words should get a sane answer rather than a memory error.

For anything larger there are two better tools than a bigger cap. `search_in_page` returns only
the paragraphs matching a phrase, which is cheaper than reading a page you already know the
shape of. And `summarize_page`, which post 18 builds, returns the argument of the page instead
of the page.

There is a third option the protocol offers and this project does not use, and it is worth
knowing about. A tool may return a **resource link** instead of content: a `ContentBlock` of
type `resource_link` carrying a Uniform Resource Identifier (URI) the client can read later
through `resources/read`.

```json
{
  "type": "resource_link",
  "uri": "browser://page/9f2c",
  "name": "what-a-page-costs",
  "mimeType": "text/plain"
}
```

That turns a giant tool result into a pointer, and it moves the decision about how much to read
from the server to the application. Two things make it the wrong default here. The
specification notes that resource links returned by tools are not guaranteed to appear in
`resources/list`, so a client has to be willing to read a URI it never saw advertised, and not
every host does. And a research pass needs the text *now*, in the same turn, to summarize it.
Reach for resource links when the artifact is large, durable, and optional to read: a generated
report, a captured log, a downloaded dataset. Not when the tool's whole purpose is to put words
in front of the model this turn.

## 9. Running it

The suite needs no network, no API key, and no browser binary:

```bash
cd code/17-research-browser && PYTHONPATH=src pytest tests -q
```

```
........................................................................ [ 94%]
..s.                                                                     [100%]
75 passed, 1 skipped in 1.92s
```

The skip is `test_a_key_selects_the_provider_when_the_sdk_is_installed`, which belongs to post
18 and is skipped here because the optional provider extra is not installed. That is the
intended state: the server and its entire suite work without it.

Under a real host, the server is spawned over stdio and needs Chromium present:

```bash
uv sync
uv run playwright install chromium
uv run mcp-research-browser
```

The registered surface is five tools in this post, plus two more in post 18, one resource, and
one prompt:

```
browse_url         inputs=['url', 'use_cache', 'max_words']     outputSchema=yes  readOnly=True
screenshot_page    inputs=['url', 'full_page']                  outputSchema=yes  readOnly=True
search_in_page     inputs=['url', 'query', 'max_matches']       outputSchema=yes  readOnly=True
get_cache_stats    inputs=[]                                    outputSchema=yes  readOnly=True
clear_cache        inputs=[]                                    outputSchema=yes  readOnly=False
```

`clear_cache` is the only tool that is not read-only, and it says so through
`ToolAnnotations(read_only_hint=False, destructive_hint=True, ...)`. Annotations are hints for
the host, never enforcement; nothing stops a badly written tool from lying about itself. The
enforcement is that these functions genuinely do what they claim, and there is a test asserting
that the set of non-read-only tools is exactly `["clear_cache"]` rather than merely "does not
include something".

A page that will not load is an error result, not a success-shaped result with an apology in a
text field:

```python
raise ToolError(f"could not load {url}: {exc}") from exc
```

which reaches the client as a normal response carrying `isError: true`:

```
is_error: True  result_type: complete
Error executing tool browse_url: could not load https://example.com/gone: net::ERR_NAME_NOT_RESOLVED
```

Read `resultType` first, then `isError`. A `resultType` of `"complete"` means the call
finished, and says nothing at all about whether the tool succeeded.
[Post 12](../12-testing-and-debugging/index.md) covers the two places a failure hides.

## 10. When not to build this

An honest edge. Three cases where a browsing server is the wrong answer:

**The site has an API.** A JSON endpoint gives you structured data with no extraction, no
rendering, and no heuristics deciding what the article is. Driving a browser against a site
that publishes an API is choosing the fragile path on purpose.

**You need many pages at once.** One Chromium with one page per call is a serial pipeline by
design, and each page costs a navigation plus the settle. If your workload is hundreds of
pages, you want a queue, a worker pool, and probably the Tasks extension from
[Post 09](../09-tasks/index.md) rather than a tool call that blocks.

**You are inside a security boundary that matters.** This server will fetch any Uniform
Resource Locator (URL) it is given, and the URL usually comes from a model, which may be acting
on text it read on a previous page. That is server-side request forgery with extra steps.
Loopback binding is the default here for exactly that reason, and it is not sufficient on its
own. [Post 19](../19-security/index.md) is the one to read before you deploy this anywhere
interesting.

---

## Common pitfalls

- **Passing `strip=["script"]` to `markdownify` and thinking you removed the scripts.** It
  removes the tag and keeps the text, so the page's minified bundle comes back as extracted
  prose with a healthy-looking word count. Remove the elements from the tree with
  `decompose()` before the converter sees the document, and assert on the strings that used
  to leak.
- **Reporting a reduction percentage as if it were a quality score.** A page that rendered
  nothing reduces by 99.9 percent. Report the bytes in, the bytes out, the word count, *and*
  the method, and label an empty extraction `empty` so nobody reads a total failure as a
  triumph.
- **Returning image content as a list of dictionaries.** The SDK serializes it into a text
  block, and the host gets base64 as prose instead of a picture. Return a `CallToolResult`
  with a real `ImageContent`, and annotate the return as
  `Annotated[CallToolResult, YourMeta]` so an output schema is still published.
- **Waiting for `networkidle`.** Analytics beacons and long-polling sockets mean the network
  may never go quiet, and your tool times out on pages that rendered fine a second in. Use
  `domcontentloaded` plus a short fixed settle.
- **Forgetting `await page.close()`.** Every leaked page is a tab that never goes away, and a
  long-lived server eventually reaches a Chromium that refuses to open new ones. Put it in a
  `finally`, and put the browser shutdown in the lifespan's `finally` too.
- **Making Playwright a hard import at module load.** The Python package installs the
  bindings, not the browsers. A server that cannot import its own modules cannot tell the
  operator to run `playwright install chromium`. Import inside `start()` and raise a message
  that names the fix.
- **Caching without a ceiling on entries.** A TTL alone bounds staleness, not memory. A
  long-running server that browses widely will hold every page it ever saw until something
  restarts it.

---

## Further reading

- Specification, *"Tools"* § Content block types, revision 2026-07-28. The `ContentBlock`
  union, the image block shape, and the note that tool-returned resource links need not appear
  in `resources/list`. <https://modelcontextprotocol.io/specification/draft/server/tools>
- Specification, *"Caching"*, revision 2026-07-28. The six methods that carry `ttlMs` and
  `cacheScope`, and the fact that `tools/call` is not among them.
  <https://modelcontextprotocol.io/specification/draft/server/utilities/caching>
- Playwright for Python. <https://playwright.dev/python/>. Navigation lifecycle events, and
  why `install` and `install-deps` are separate commands.
- `trafilatura` documentation. <https://trafilatura.readthedocs.io/>. Extraction options, and
  what `favor_precision` changes.
- MCP Python SDK, `mcp==2.0.0b2`. Every result in this post came from this version driving
  [code/17-research-browser/](../../code/17-research-browser/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 18 — Project 3 · Server-side model calls and multi-page research](../18-server-side-models/index.md)**:
  the other half of this server, where the page still does not fit and the server needs a model
  of its own, which is no longer the host's model.
- **[Post 16 — Project 2 · Safe remediation with approval](../16-devops-remediation/index.md)**:
  the previous project, and the approval machinery this one deliberately does not need because
  every tool here is read-only.
- **[Post 06 — Tools in depth: schemas, structured output, and annotations](../06-tools-in-depth/index.md)**:
  the class-body annotation rule behind every dataclass in this project, and the silent
  `outputSchema: null` it prevents.
