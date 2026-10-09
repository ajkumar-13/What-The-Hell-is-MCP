"""Post 17, sections 2, 5, 8, 10 and 11: the browsing tools as a client sees them.

Run from the series root:
    uv run python posts/17-research-browser/snippets/browse_tools.py

Imports the package under code/17-research-browser/src, installs the suite's
`FixtureFetcher` from tests/conftest.py in place of Chromium (no browser, no
network: the committed fixtures stand in for three URLs under example.com), and
connects an in-memory client. Prints the published surface, the browse_url
receipt for the article page, the client-rendered page fetched and rendered,
what the word cap does with a too-small and a too-large request, and the
result for a URL that will not load.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"

sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

from importlib.metadata import version  # noqa: E402

from conftest import ARTICLE_URL, MISSING_URL, SPA_URL, FixtureFetcher  # noqa: E402
from mcp import Client  # noqa: E402

import research_browser  # noqa: E402
from research_browser.browser import browser  # noqa: E402

logging.disable(logging.CRITICAL)

RECEIPT = ("url", "method", "raw_bytes", "clean_bytes", "reduction_percent", "word_count")


async def surface() -> None:
    async with Client(research_browser.mcp) as client:
        tools = (await client.list_tools()).tools
        resources = (await client.list_resources()).resources
        prompts = (await client.list_prompts()).prompts
    print(f"mcp {version('mcp')}, Python {sys.version.split()[0]}")
    print()
    print("1. tools/list")
    for t in tools:
        inputs = list(t.input_schema.get("properties", {}))
        schema = "yes" if t.output_schema is not None else "no"
        a = t.annotations
        print(f"   {t.name:<16}inputs={str(inputs):<58}outputSchema={schema}  "
              f"readOnly={a.read_only_hint}  destructive={a.destructive_hint}")
    print(f"   resources: {[str(r.uri) for r in resources]}   prompts: {[p.name for p in prompts]}")
    browse = next(t for t in tools if t.name == "browse_url")
    print(f"   browse_url outputSchema properties: {list(browse.output_schema['properties'])}")
    print()


async def receipt() -> None:
    browser.use(FixtureFetcher())
    async with Client(research_browser.mcp) as client:
        result = await client.call_tool("browse_url", {"url": ARTICLE_URL})
    data = dict(result.structured_content)
    full_text = data["text"]
    data["text"] = full_text[:80].rstrip() + " ..."
    print("2. browse_url on the article page: structuredContent, text shortened")
    print(json.dumps(data, indent=2))
    blocks = [block.type for block in result.content]
    same = json.loads(result.content[0].text) == result.structured_content
    print(f"   content blocks: {blocks}; the text block parses to structuredContent: {same}")
    print(f"   resultType={result.result_type}  isError={result.is_error}  _meta={json.dumps(result.meta)}")
    print()


async def fetched_and_rendered() -> None:
    print("3. the client-rendered page through browse_url, fetched and then rendered")
    for render in (False, True):
        browser.use(FixtureFetcher(render=render))
        async with Client(research_browser.mcp) as client:
            result = await client.call_tool("browse_url", {"url": SPA_URL})
        data = result.structured_content
        print("   " + json.dumps({k: data[k] for k in RECEIPT}))
    print()


async def word_cap() -> None:
    print("4. the word cap")
    browser.use(FixtureFetcher())
    async with Client(research_browser.mcp) as client:
        for asked in (5, 1_000_000):
            data = (await client.call_tool(
                "browse_url", {"url": ARTICLE_URL, "max_words": asked})).structured_content
            print(f"   max_words={asked:<8} truncated={data['truncated']!s:<6} "
                  f"word_count={data['word_count']}  returned_words={data['returned_words']}")
    print()


async def will_not_load() -> None:
    print("5. a URL the fetcher cannot load")
    browser.use(FixtureFetcher())
    async with Client(research_browser.mcp) as client:
        result = await client.call_tool("browse_url", {"url": MISSING_URL})
    print(f"   resultType={result.result_type}  isError={result.is_error}  "
          f"structuredContent={result.structured_content}")
    print(f"   text: {result.content[0].text}")


async def main() -> None:
    await surface()
    await receipt()
    await fetched_and_rendered()
    await word_cap()
    await will_not_load()


if __name__ == "__main__":
    asyncio.run(main())
