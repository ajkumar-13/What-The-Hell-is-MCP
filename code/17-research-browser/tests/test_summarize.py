"""Summarization: the offline default, and never lying about which one ran.

Post 18's whole argument is that sampling is gone, so a server that wants a
model calls one directly -- which means it needs a real answer for the case
where there is no model configured, and an honest answer for the case where the
configured model fails.

Nothing here needs a network or an API key. The provider adapter is exercised
through an injected fake client, which is also how you would test it in a real
project.
"""

from __future__ import annotations

import pytest

from research_browser.summarize import (
    AnthropicSummarizer,
    ExtractiveSummarizer,
    Summary,
    default_summarizer,
    split_sentences,
    summarize,
)

TEXT = (
    "Extraction removes the boilerplate from a web page. "
    "The remaining article is still often too long for a context window. "
    "Boilerplate removal and summarization solve different halves of the same "
    "problem. "
    "A cache avoids paying for extraction twice in one session. "
    "Screenshots are useful when the answer is visual rather than textual. "
    "Citations matter because a claim with no source cannot be checked."
)


# --------------------------------------------------------------------------
# Sentence splitting
# --------------------------------------------------------------------------


def test_sentences_split_on_terminators_and_line_breaks() -> None:
    sentences = split_sentences("One. Two! Three?\n\nFour.")
    assert sentences == ["One.", "Two!", "Three?", "Four."]


def test_splitting_empty_text_gives_no_sentences() -> None:
    assert split_sentences("   \n  \n ") == []


# --------------------------------------------------------------------------
# The extractive default
# --------------------------------------------------------------------------


async def test_extractive_returns_sentences_from_the_source_verbatim() -> None:
    """The property post 18 leans on: every returned sentence is on the page."""
    summarizer = ExtractiveSummarizer()
    result = await summarizer.summarize(TEXT, max_sentences=3)

    assert result
    for sentence in split_sentences(result):
        assert sentence in TEXT


async def test_extractive_respects_the_sentence_budget() -> None:
    summarizer = ExtractiveSummarizer()
    assert len(split_sentences(await summarizer.summarize(TEXT, max_sentences=2))) == 2
    assert len(split_sentences(await summarizer.summarize(TEXT, max_sentences=4))) == 4


async def test_extractive_keeps_document_order() -> None:
    summarizer = ExtractiveSummarizer()
    result = await summarizer.summarize(TEXT, max_sentences=3)

    positions = [TEXT.index(s) for s in split_sentences(result)]
    assert positions == sorted(positions)


async def test_extractive_is_deterministic() -> None:
    """Identical input, identical output. Otherwise nothing downstream is testable."""
    summarizer = ExtractiveSummarizer()
    first = await summarizer.summarize(TEXT, max_sentences=3)
    second = await ExtractiveSummarizer().summarize(TEXT, max_sentences=3)

    assert first == second


async def test_focus_moves_the_selection() -> None:
    summarizer = ExtractiveSummarizer()
    neutral = await summarizer.summarize(TEXT, max_sentences=1)
    focused = await summarizer.summarize(TEXT, focus="citations", max_sentences=1)

    assert "Citations matter" in focused
    assert focused != neutral


async def test_short_text_comes_back_whole() -> None:
    summarizer = ExtractiveSummarizer()
    assert await summarizer.summarize("One sentence only.", max_sentences=5) == (
        "One sentence only."
    )


async def test_empty_text_summarizes_to_nothing() -> None:
    assert await ExtractiveSummarizer().summarize("", max_sentences=3) == ""


# --------------------------------------------------------------------------
# Provenance: what actually ran
# --------------------------------------------------------------------------


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeMessages:
    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> FakeMessage:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return FakeMessage(self.reply or "")


class FakeClient:
    def __init__(self, reply: str | None = None, error: Exception | None = None) -> None:
        self.messages = FakeMessages(reply, error)


async def test_a_successful_provider_call_is_labelled_as_the_provider() -> None:
    client = FakeClient(reply="A model wrote this.")
    provider = AnthropicSummarizer(model="claude-opus-5", client=client)

    result = await summarize(TEXT, summarizer=provider, max_sentences=3)

    assert isinstance(result, Summary)
    assert result.text == "A model wrote this."
    assert result.summarizer == "anthropic:claude-opus-5"
    assert result.requested == "anthropic:claude-opus-5"
    assert result.fell_back is False
    assert result.note == ""


async def test_a_failed_provider_call_is_never_labelled_as_the_provider() -> None:
    """The defect this module exists to not have.

    The first edition caught the exception, fell through to truncated text, and
    footered the result with "Summarized via MCP Sampling". The caller had no
    way to tell a model summary from a fallback.
    """
    client = FakeClient(error=RuntimeError("401 invalid x-api-key"))
    provider = AnthropicSummarizer(model="claude-opus-5", client=client)

    result = await summarize(TEXT, summarizer=provider, max_sentences=3)

    assert result.requested == "anthropic:claude-opus-5"
    assert result.summarizer == "extractive"
    assert result.fell_back is True
    assert "401 invalid x-api-key" in result.note
    # And the fallback text is real text, not an apology.
    assert result.text
    for sentence in split_sentences(result.text):
        assert sentence in TEXT


async def test_the_fallback_still_produces_a_usable_reduction() -> None:
    client = FakeClient(error=TimeoutError("read timed out"))
    provider = AnthropicSummarizer(model="claude-opus-5", client=client)

    result = await summarize(TEXT, summarizer=provider, max_sentences=2)

    assert result.input_words > result.summary_words
    assert result.reduction_percent > 0


async def test_the_provider_prompt_carries_the_focus_and_the_budget() -> None:
    client = FakeClient(reply="ok")
    provider = AnthropicSummarizer(model="claude-opus-5", client=client)

    await provider.summarize(TEXT, focus="citations", max_sentences=4)

    call = client.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    prompt = call["messages"][0]["content"]
    assert "at most 4 sentences" in prompt
    assert "Focus on: citations" in prompt
    # Sampling parameters were removed on this model line; sending one is a 400.
    assert "temperature" not in call
    assert "top_p" not in call


async def test_provider_reads_text_blocks_by_type_not_by_position() -> None:
    """`content[0]` is not guaranteed to be the text block."""

    class ThinkingFirst(FakeMessages):
        async def create(self, **kwargs: object) -> FakeMessage:
            message = FakeMessage("the summary")
            thinking = _FakeBlock("")
            thinking.type = "thinking"
            message.content.insert(0, thinking)
            return message

    client = FakeClient()
    client.messages = ThinkingFirst()
    provider = AnthropicSummarizer(model="claude-opus-5", client=client)

    assert await provider.summarize(TEXT) == "the summary"


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_no_key_means_no_network() -> None:
    """The default has to work on a machine that has never seen a credential."""
    assert default_summarizer(env={}).name == "extractive"


def test_an_explicit_override_beats_a_configured_key() -> None:
    env = {"ANTHROPIC_API_KEY": "sk-ant-not-a-real-key", "RESEARCH_BROWSER_SUMMARIZER": "extractive"}
    assert default_summarizer(env=env).name == "extractive"


def test_a_key_selects_the_provider_when_the_sdk_is_installed() -> None:
    anthropic = pytest.importorskip(
        "anthropic", reason="the provider extra is optional and not installed here"
    )
    assert anthropic  # silence the unused-name warning

    env = {"ANTHROPIC_API_KEY": "sk-ant-not-a-real-key"}
    assert default_summarizer(env=env).name.startswith("anthropic:")


def test_a_key_without_the_sdk_degrades_to_extractive_rather_than_crashing() -> None:
    """A missing optional dependency must not take the server down."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        env = {"ANTHROPIC_API_KEY": "sk-ant-not-a-real-key"}
        assert default_summarizer(env=env).name == "extractive"
    else:
        pytest.skip("the anthropic package is installed, so this path cannot run")
