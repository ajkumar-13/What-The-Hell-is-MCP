# research-browser

The server from posts 17 and 18: a headless browser behind an MCP boundary, so a model can
read a page it was never trained on without the host ever handling raw HTML.

The extraction step is the point of the whole thing. A modern article page is mostly
navigation, scripts, and markup; what the model needs is the prose. `extract.py` is where
that reduction happens, and post 17 quotes the numbers the test suite measures against the
committed fixtures in [`tests/fixtures/`](tests/fixtures/).

| Path | What it is |
|---|---|
| `src/research_browser/app.py` | The server instance every other module hangs a registration off. |
| `src/research_browser/browser.py` | Headless browsing. Playwright is imported lazily, inside `start()`. |
| `src/research_browser/extract.py` | Content extraction, and the size reduction that justifies the server. |
| `src/research_browser/cache.py` | A time-to-live cache for extracted pages. |
| `src/research_browser/research.py` | The multi-page loop, its budget, and its citations. |
| `src/research_browser/summarize.py` | Post 18's server-side summarization, with no back-channel. |
| `src/research_browser/tools.py` | The browsing tools, the cache resource, and the research prompt. |

What it publishes:

| Kind | Name |
|---|---|
| Tool | `browse_url`, `screenshot_page`, `search_in_page`, `get_cache_stats`, `clear_cache` |
| Tool | `summarize_page`, `research_urls` |
| Resource | `cache_stats_resource` |
| Prompt | `research_topic` |

## Requirements

Python 3.10 or newer, and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync --extra dev
```

The `mcp` dependency is **pinned exactly** to `2.0.0b2`, the pre-release that implements
protocol revision 2026-07-28. Floating that pin breaks the imports, because the 1.x line
uses a different module layout. `trafilatura` and `markdownify` are pinned to a major line
for a different reason: post 17 quotes size-reduction numbers that a different extractor
version would move.

`uv sync` installs the Playwright **package**. It does not install a browser, which is a
separate download:

```bash
uv run playwright install chromium
```

Everything except live browsing works without that step, including the whole test suite,
because the tests run against the committed HTML fixtures rather than the network.

## Run

```bash
uv run mcp-research-browser              # stdio, what a desktop host spawns
uv run mcp-research-browser --http       # Streamable HTTP on 127.0.0.1:8000
```

## Test

```bash
uv run pytest
```

76 tests across four files, no network and no API key. Without the optional `provider`
extra you get `75 passed, 1 skipped`; the skip is the one test that needs it.

| File | Covers |
|---|---|
| `test_extract.py` | The reduction from raw HTML to prose, against three fixtures. |
| `test_cache.py` | Expiry, eviction, and the statistics the resource reports. |
| `test_server.py` | The published tool surface and what each tool returns. |
| `test_summarize.py` | Post 18's summarizer, including the path with no provider installed. |

## The optional provider

Post 18 adds a summarizer that can call a model server-side:

```bash
uv sync --extra provider
```

The server runs, and the entire test suite passes, without that extra and without an API
key. That is deliberate, and post 18 explains why the fallback path is the one worth
designing first.
