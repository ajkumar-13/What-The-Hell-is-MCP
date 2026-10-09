"""Post 18, sections 4, 5 and 6: the offline default, the opt-in provider, and an honest label.

Run from the series root:
    uv run python posts/18-server-side-models/snippets/summarizers.py

Imports summarize.py of the research browser (code/17-research-browser/) in memory. It
prints which summarizer the environment selects in four configurations (the environments
are dictionaries built here, so the real environment and any real key are never read),
the extractive summary of a six-sentence text, why sentences are joined with a newline,
and three runs of the provider adapter on stub clients: one that answers, one whose
first content block is not text, and one that raises. No provider is contacted and no
package beyond the root environment is needed; the anthropic package is not installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

sys.dont_write_bytecode = True  # leave no __pycache__ behind in code/
ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "code" / "17-research-browser"
sys.path[:0] = [str(PROJECT / "src"), str(PROJECT / "tests")]

from conftest import read_fixture  # noqa: E402

from research_browser.extract import extract  # noqa: E402
from research_browser.summarize import (  # noqa: E402
    AnthropicSummarizer,
    ExtractiveSummarizer,
    default_summarizer,
    split_sentences,
    summarize,
)

# The same six sentences the project's tests/test_summarize.py uses.
TEXT = (
    "Extraction removes the boilerplate from a web page. "
    "The remaining article is still often too long for a context window. "
    "Boilerplate removal and summarization solve different halves of the same "
    "problem. "
    "A cache avoids paying for extraction twice in one session. "
    "Screenshots are useful when the answer is visual rather than textual. "
    "Citations matter because a claim with no source cannot be checked."
)
FAKE_KEY = "sk-ant-not-a-real-key"


class Warnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")


class Block:
    def __init__(self, kind: str, text: str) -> None:
        self.type = kind
        self.text = text


class StubMessages:
    """Records each request and answers from a script; nothing leaves the process."""

    def __init__(self, blocks: list[Block] | None = None, error: Exception | None = None) -> None:
        self.blocks = blocks or []
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return type("Message", (), {"content": list(self.blocks)})()


class StubClient:
    def __init__(self, messages: StubMessages) -> None:
        self.messages = messages


async def main() -> None:
    warnings = Warnings()
    logging.getLogger("research-browser.summarize").addHandler(warnings)

    print("== which summarizer the environment selects ==")
    print(f"anthropic package installed: {importlib.util.find_spec('anthropic') is not None}")
    for label, env in (
        ("no variables at all", {}),
        ("a key, and the override set to extractive",
         {"ANTHROPIC_API_KEY": FAKE_KEY, "RESEARCH_BROWSER_SUMMARIZER": "extractive"}),
        ("a key, package missing", {"ANTHROPIC_API_KEY": FAKE_KEY}),
    ):
        warnings.lines.clear()
        chosen = default_summarizer(env=env).name
        print(f"{label:42} -> {chosen}")
        for line in warnings.lines:
            print(f"{'':42}    logged: {line}")

    print("\n== the extractive default, three sentences of six ==")
    first = await ExtractiveSummarizer().summarize(TEXT, max_sentences=3)
    second = await ExtractiveSummarizer().summarize(TEXT, max_sentences=3)
    print(first)
    print(f"identical on a second run: {first == second}")
    print(f"every sentence is in the source: {all(s in TEXT for s in split_sentences(first))}")
    positions = [TEXT.index(s) for s in split_sentences(first)]
    print(f"in document order: {positions == sorted(positions)}")

    print("\n== why the sentences are joined with a newline ==")
    article = extract(read_fixture("article.html"), url="https://example.com/blog/what-a-page-costs")
    sentences = split_sentences(article.text)
    at = next(i for i, s in enumerate(sentences) if not s.endswith((".", "!", "?")) and i > 0)
    pair = sentences[at : at + 2]
    print(f"two consecutive units of the article fixture: {pair}")
    print(f"space-joined, split again:   {split_sentences(' '.join(pair))}")
    print(f"newline-joined, split again: {split_sentences(chr(10).join(pair))}")

    print("\n== the provider adapter, on a stub client that answers ==")
    messages = StubMessages([Block("text", "A model wrote this.")])
    provider = AnthropicSummarizer(model="claude-opus-5", client=StubClient(messages))
    result = await summarize(TEXT, summarizer=provider, max_sentences=3)
    print(f"summarizer={result.summarizer!r} requested={result.requested!r} "
          f"fell_back={result.fell_back} note={result.note!r}")
    call = messages.calls[0]
    print(f"request keys: {sorted(call)}")
    print(f"output_config: {call['output_config']}, max_tokens: {call['max_tokens']}")
    print(f"temperature sent: {'temperature' in call}, top_p sent: {'top_p' in call}")

    print("\n== a reply whose first content block is not text ==")
    messages = StubMessages([Block("thinking", ""), Block("text", "the summary")])
    provider = AnthropicSummarizer(model="claude-opus-5", client=StubClient(messages))
    print(f"content types: {[b.type for b in messages.blocks]} -> summary {await provider.summarize(TEXT)!r}")

    print("\n== the provider adapter, on a stub client that raises ==")
    warnings.lines.clear()
    messages = StubMessages(error=RuntimeError("401 invalid x-api-key"))
    provider = AnthropicSummarizer(model="claude-opus-5", client=StubClient(messages))
    result = await summarize(TEXT, summarizer=provider, max_sentences=3)
    print(json.dumps(asdict(result), indent=2))
    for line in warnings.lines:
        print(f"logged: {line}")


asyncio.run(main())
