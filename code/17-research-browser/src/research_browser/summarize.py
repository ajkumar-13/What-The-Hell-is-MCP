"""Server-side summarization, without a back-channel.

Post 18. The old answer to "the server needs a model" was **sampling**: the
server asked the host's LLM through the MCP session. Sampling is deprecated as
of revision 2026-07-28 (SEP-2577), and the modern transports have no
server-to-client request channel at all -- `can_send_request` is hardcoded
false on the 2026-07-28 streamable-HTTP path. There is nothing to call back to.

So a server that needs a model calls a provider **directly**, and that moves
three things onto the server operator: the bill, the consent, and the prompt.
Which means the server has to be honest about which model ran, and has to work
at all when no model is configured.

That is the whole shape of this module:

- `Summarizer` is a two-line Protocol.
- `ExtractiveSummarizer` is the default. It needs no network, no key, and no
  budget: it ranks the document's own sentences by term frequency and returns
  the best few, verbatim. Worse than a model. Always available.
- `AnthropicSummarizer` is opt-in, and activates only when an API key is
  present and the `anthropic` package is installed.
- `summarize()` wraps whichever one you hand it and **records which one
  actually produced the text.** A provider call that fails falls back to the
  extractive summarizer and says so in the result. Labelling a fallback as if
  the model had run is the bug this module exists to not have.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

log = logging.getLogger("research-browser.summarize")

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_SENTENCES = 5

API_KEY_ENV = "ANTHROPIC_API_KEY"
MODEL_ENV = "RESEARCH_BROWSER_MODEL"
# Set this to force the no-network path even where a key is configured, which
# is what you want in CI and when you are demonstrating the offline behaviour.
FORCE_ENV = "RESEARCH_BROWSER_SUMMARIZER"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")

# Small and deliberate. A longer list makes the ranking better on English prose
# and worse on everything else; term frequency already discounts common words.
_STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because
    been before being below between both but by can cannot could did do does doing
    down during each few for from further had has have having he her here hers him
    his how i if in into is it its itself just me more most my no nor not now of
    off on once only or other our out over own same she should so some such than
    that the their them then there these they this those through to too under
    until up very was we were what when where which while who whom why will with
    would you your
    """.split()
)


@dataclass
class Summary:
    """A summary and its provenance.

    `summarizer` is what actually ran. `requested` is what was asked for. When
    they differ, `fell_back` is true and `note` says why. A caller can never be
    told a model wrote this when it did not.
    """

    text: str
    summarizer: str
    requested: str
    fell_back: bool
    note: str
    input_words: int
    summary_words: int
    reduction_percent: float


class Summarizer(Protocol):
    """Anything that can shorten text.

    Two members. That is the entire seam between "this server summarizes" and
    "this server has an opinion about which vendor you pay".
    """

    name: str

    async def summarize(
        self,
        text: str,
        *,
        focus: str | None = None,
        max_sentences: int = DEFAULT_MAX_SENTENCES,
    ) -> str: ...


# --------------------------------------------------------------------------
# The default: extractive, offline, deterministic
# --------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, treating blank lines as hard breaks."""
    sentences: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        for candidate in _SENTENCE_SPLIT.split(block):
            candidate = candidate.strip()
            if candidate:
                sentences.append(candidate)
    return sentences


def _content_words(sentence: str) -> list[str]:
    return [w for w in _WORD.findall(sentence.lower()) if w not in _STOPWORDS]


class ExtractiveSummarizer:
    """Rank the document's own sentences by term frequency; keep the best few.

    No network, no key, no tokens billed to anyone. The sentences come back
    verbatim and in document order, which has a property a generated summary
    does not: every sentence provably appeared on the page. Post 18 leans on
    that when it tracks citations.
    """

    name = "extractive"

    async def summarize(
        self,
        text: str,
        *,
        focus: str | None = None,
        max_sentences: int = DEFAULT_MAX_SENTENCES,
    ) -> str:
        sentences = split_sentences(text)
        if not sentences:
            return ""
        max_sentences = max(1, max_sentences)
        if len(sentences) <= max_sentences:
            return "\n".join(sentences)

        frequencies: Counter[str] = Counter()
        for sentence in sentences:
            frequencies.update(_content_words(sentence))
        if not frequencies:
            return " ".join(sentences[:max_sentences])

        peak = max(frequencies.values())
        focus_terms = set(_content_words(focus)) if focus else set()

        scored: list[tuple[float, int]] = []
        for index, sentence in enumerate(sentences):
            words = _content_words(sentence)
            if not words:
                continue
            # Normalising by sqrt(length) stops a single long paragraph-sentence
            # from winning on sheer word count alone.
            score = sum(frequencies[w] / peak for w in words) / math.sqrt(len(words))
            if focus_terms:
                overlap = len(focus_terms.intersection(words))
                score *= 1.0 + 0.5 * overlap
            scored.append((score, index))

        if not scored:
            return "\n".join(sentences[:max_sentences])

        # Sort by score, then by position, so ties resolve the same way every
        # run. A summarizer that returns different text for identical input is
        # impossible to test and impossible to cache.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        chosen = sorted(index for _, index in scored[:max_sentences])

        # One sentence per line, not space-joined. Headings and list items have
        # no terminating punctuation, so a space-joined summary cannot be split
        # back into the sentences it was built from -- and post 18 needs exactly
        # that to attribute each claim to the page it came from.
        return "\n".join(sentences[i] for i in chosen)


# --------------------------------------------------------------------------
# The opt-in: a real model, paid for by whoever runs this server
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You summarize web pages for a research assistant. Preserve concrete facts, "
    "numbers, names, and conclusions. Do not add information that is not in the "
    "text. Do not use emoji. Write plain prose, no headings and no bullet lists."
)


class AnthropicSummarizer:
    """Calls the Claude API directly. Requires a key the operator supplies.

    Nothing in this class is reachable unless the operator installed the extra
    and set a key. That is the point: the capability is opt-in, the cost lands
    on whoever opted in, and the prompt above is the server author's to defend.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: Any = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.name = f"anthropic:{model}"
        self._client = client
        self._api_key = api_key

    def _require_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "the anthropic package is not installed. Install the optional "
                "extra: `uv sync --extra provider`."
            ) from exc
        self._client = (
            AsyncAnthropic(api_key=self._api_key)
            if self._api_key
            else AsyncAnthropic()
        )
        return self._client

    async def summarize(
        self,
        text: str,
        *,
        focus: str | None = None,
        max_sentences: int = DEFAULT_MAX_SENTENCES,
    ) -> str:
        if not text.strip():
            return ""

        instruction = f"Summarize the page below in at most {max_sentences} sentences."
        if focus:
            instruction += f" Focus on: {focus}."

        message = await self._require_client().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            # `effort` is where thinking depth is controlled now; a summary does
            # not need deep reasoning, and max_tokens covers thinking plus text.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"{instruction}\n\n{text}"}],
        )

        # content is a list of blocks, and the first one is not guaranteed to be
        # text. Filter by type rather than indexing.
        parts = [
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        ]
        return "\n\n".join(part for part in parts if part).strip()


# --------------------------------------------------------------------------
# Selection and the honest wrapper
# --------------------------------------------------------------------------


def default_summarizer(env: Mapping[str, str] | None = None) -> Summarizer:
    """Pick a summarizer from the environment, defaulting to no network.

    The order matters. An explicit `RESEARCH_BROWSER_SUMMARIZER=extractive`
    wins over a configured key, so a deployment can turn the provider off
    without unsetting credentials the rest of the box needs.
    """
    env = os.environ if env is None else env

    forced = (env.get(FORCE_ENV) or "").strip().lower()
    if forced == "extractive":
        return ExtractiveSummarizer()

    api_key = (env.get(API_KEY_ENV) or "").strip()
    if not api_key and forced != "anthropic":
        return ExtractiveSummarizer()

    try:
        import anthropic  # noqa: F401
    except ImportError:
        log.warning(
            "%s is set but the anthropic package is not installed; "
            "using the extractive summarizer",
            API_KEY_ENV,
        )
        return ExtractiveSummarizer()

    return AnthropicSummarizer(model=env.get(MODEL_ENV) or DEFAULT_MODEL)


async def summarize(
    text: str,
    *,
    summarizer: Summarizer,
    focus: str | None = None,
    max_sentences: int = DEFAULT_MAX_SENTENCES,
) -> Summary:
    """Summarize, and record which summarizer actually produced the text.

    A provider call can fail for reasons that have nothing to do with the page:
    an expired key, a rate limit, a network that is down. When it does, the
    extractive summarizer runs instead and the result says so. The one thing
    this must never do is return provider-shaped metadata for text a fallback
    wrote.
    """
    requested = summarizer.name
    input_words = len(text.split())
    fell_back = False
    note = ""

    try:
        summary_text = await summarizer.summarize(
            text, focus=focus, max_sentences=max_sentences
        )
        ran = requested
    except Exception as exc:
        fell_back = True
        # str(exc) can be long and can echo the request; one line is enough to
        # diagnose and short enough not to blow up the tool result.
        note = f"{requested} failed ({type(exc).__name__}: {exc}); used extractive"
        log.warning("summarizer %s failed: %s", requested, exc)
        fallback = ExtractiveSummarizer()
        summary_text = await fallback.summarize(
            text, focus=focus, max_sentences=max_sentences
        )
        ran = fallback.name

    summary_words = len(summary_text.split())
    reduction = 0.0 if input_words == 0 else (1 - summary_words / input_words) * 100

    return Summary(
        text=summary_text,
        summarizer=ran,
        requested=requested,
        fell_back=fell_back,
        note=note,
        input_words=input_words,
        summary_words=summary_words,
        reduction_percent=round(reduction, 1),
    )


# The summarizer this process uses. Resolved lazily so that importing the
# package never touches the environment, and replaceable so that tests never
# depend on what the developer happens to have exported.
_active: Summarizer | None = None


def active_summarizer() -> Summarizer:
    global _active
    if _active is None:
        _active = default_summarizer()
        log.info("summarizer: %s", _active.name)
    return _active


def use_summarizer(summarizer: Summarizer) -> None:
    global _active
    _active = summarizer
