"""Load the Markdown corpus and rank it.

This module has no dependency on MCP at all, and no dependency on anything
outside the standard library. That is deliberate twice over:

- **No MCP.** The retrieval logic is testable on its own, and `tests/test_index.py`
  exercises it without ever opening a client. When a search result looks wrong you
  can bisect it here instead of through the protocol.
- **No external packages.** Post 23 is about one server reaching every client, and
  the fewer things a reader has to install before the server starts, the more
  likely it is that it starts. The ranking below is Okapi BM25, written out by
  hand in about sixty lines. It is not a replacement for a real search engine; it
  is enough to make a five-document team handbook genuinely searchable.

## The retrieval unit is a section, not a document

Splitting on `##` headings and ranking the pieces is what makes the results
useful. A whole-document match tells a model that `runbooks.md` is relevant,
which it already suspected. A section match tells it that
"Reconciliation backlog" is the part to read, and the excerpt proves it.

## BM25 in one paragraph

For each query term, a section scores highly when the term is frequent *in that
section* (term frequency) and rare *across the corpus* (inverse document
frequency), with two corrections. `k1` saturates term frequency, so the tenth
occurrence of "database" adds far less than the second. `b` normalizes for
length, so a long section does not out-score a short one merely by containing
more words. Both are the standard values below.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# `index.py` lives at <project>/src/knowledge_base/index.py, so three parents up
# is the project root and the corpus sits beside `src`.
_DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge"

# BM25 parameters. These are the values almost every implementation ships with,
# and there is no corpus this small that justifies tuning them.
_K1 = 1.5
_B = 0.75

# How many times a section's heading is counted into its own term frequencies.
#
# This is field boosting, and on a handbook it earns its keep immediately.
# Searching "database failover" against the raw text ranks the architecture
# document's two-sentence aside about managed failover above the runbook section
# actually titled "Database failover", because the aside is short and BM25's
# length normalization rewards that. A heading is the most deliberate summary a
# section has; counting it three times says so. Raise it much further and a
# section starts matching its title regardless of whether the body is relevant.
_HEADING_WEIGHT = 3

_WORD = re.compile(r"[a-z0-9]+")

# A deliberately tiny stop list. Removing "the" and "is" stops them dominating a
# short query; removing much more than this starts losing real queries, because
# "how do I roll back" is a question a reader actually types.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by can do does for from had has have how i if in
    into is it its of on or our so than that the their then there these they
    this to was we were what when where which who why will with you your
    """.split()
)

# How much text an excerpt is allowed to carry back to the model. Long enough to
# prove the match, short enough that five hits do not flood the context window.
_EXCERPT_CHARS = 320


def tokenize(text: str) -> list[str]:
    """Lowercase, split on runs of letters and digits, drop stop words.

    Single characters go too: a lone "a" or "5" is never the term that makes a
    section relevant, and keeping them inflates every section's length.
    """
    return [
        token
        for token in _WORD.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def slugify(text: str) -> str:
    """Turn a heading into a URI-safe anchor.

    Used for the `{anchor}` variable of the templated section resource, so it
    has to be stable: the same heading must always produce the same anchor, or
    a URI a client pinned yesterday stops resolving today.
    """
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


@dataclass
class Section:
    """One `##` section of one document, and the unit that gets ranked.

    Class-body annotations, because `SearchHit` below is returned straight out
    of a tool and the same discipline has to apply to everything near it.
    """

    slug: str
    ordinal: int
    heading: str
    anchor: str
    text: str


@dataclass
class Document:
    """One Markdown file from `knowledge/`.

    Internal only. `path` is a `Path`, which is exactly the kind of field that
    has no business in a tool's output schema, so no tool ever returns this
    type; `tools.py` builds its own plain-JSON dataclasses instead.
    """

    slug: str
    title: str
    path: Path
    text: str
    summary: str
    sections: list[Section] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class SearchHit:
    """One ranked section, shaped for the wire.

    This dataclass is nested inside a tool's return value, so every field is a
    plain JSON type and every field is annotated in the class body. A class that
    only assigns attributes in `__init__` produces `outputSchema: null` with no
    warning at all, and the tool silently ships unstructured.
    """

    slug: str
    title: str
    heading: str
    anchor: str
    score: float
    excerpt: str


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _first_paragraph(text: str) -> str:
    """The first prose paragraph, used as a one-line topic summary."""
    for block in re.split(r"\n\s*\n", text):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        return " ".join(stripped.split())
    return ""


def _split_sections(slug: str, title: str, text: str) -> list[Section]:
    """Split a document on its `##` headings.

    Everything before the first `##` becomes section zero, headed by the
    document title. A document with no `##` at all therefore still yields one
    searchable section rather than disappearing from the index.
    """
    sections: list[Section] = []
    heading = title
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(
                Section(
                    slug=slug,
                    ordinal=len(sections),
                    heading=heading,
                    anchor=slugify(heading),
                    text=body,
                )
            )

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            buffer = []
        elif line.startswith("# "):
            # The document title. Already captured, and repeating it in the body
            # would give every document a free hit on its own name.
            continue
        else:
            buffer.append(line)

    flush()
    return sections


def load_corpus(directory: Path | None = None) -> list[Document]:
    """Read every `*.md` under `directory`, sorted by file name.

    The corpus is read once, at import time, and held in memory. It is a few
    tens of kilobytes of Markdown; re-reading it per request would buy nothing
    and would make results depend on when the file was last saved.
    """
    root = Path(directory or os.environ.get("KNOWLEDGE_DIR") or _DEFAULT_KNOWLEDGE_DIR)
    documents: list[Document] = []

    if not root.is_dir():
        return documents

    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        slug = path.stem
        title = _first_heading(text) or slug.replace("-", " ").title()
        documents.append(
            Document(
                slug=slug,
                title=title,
                path=path,
                text=text,
                summary=_first_paragraph(text),
                sections=_split_sections(slug, title, text),
            )
        )

    return documents


class KnowledgeIndex:
    """An in-memory BM25 index over the sections of a corpus."""

    def __init__(self, documents: list[Document]) -> None:
        self.documents: dict[str, Document] = {d.slug: d for d in documents}
        self.sections: list[Section] = [s for d in documents for s in d.sections]

        # term -> {section position -> term frequency}
        self._postings: dict[str, dict[int, int]] = {}
        self._lengths: list[int] = []

        for position, section in enumerate(self.sections):
            tokens = tokenize(section.heading) * _HEADING_WEIGHT + tokenize(section.text)
            self._lengths.append(len(tokens))
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            for token, count in counts.items():
                self._postings.setdefault(token, {})[position] = count

        total = sum(self._lengths)
        self._average_length = total / len(self._lengths) if self._lengths else 0.0

    # -- introspection -----------------------------------------------------

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def term_count(self) -> int:
        return len(self._postings)

    def document(self, slug: str) -> Document | None:
        return self.documents.get(slug)

    def section(self, slug: str, anchor: str) -> Section | None:
        document = self.documents.get(slug)
        if document is None:
            return None
        for section in document.sections:
            if section.anchor == anchor:
                return section
        return None

    # -- ranking -----------------------------------------------------------

    def _idf(self, term: str) -> float:
        """Inverse document frequency, in the variant that cannot go negative.

        The textbook form goes negative for a term present in more than half the
        corpus, which on a five-document handbook means a common word can
        actively subtract from a score. This variant is what Lucene uses.
        """
        document_frequency = len(self._postings.get(term, ()))
        if document_frequency == 0:
            return 0.0
        n = len(self.sections)
        return math.log(1 + (n - document_frequency + 0.5) / (document_frequency + 0.5))

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Rank sections against `query` and return the best `limit` of them.

        Returns an empty list for an empty query or for a query whose every term
        is absent from the corpus. An empty result is a legitimate answer, not
        an error: the honest thing to tell a model is that the handbook does not
        cover this.
        """
        terms = tokenize(query)
        if not terms or not self.sections:
            return []

        scores: dict[int, float] = {}
        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            for position, frequency in postings.items():
                length_ratio = (
                    self._lengths[position] / self._average_length
                    if self._average_length
                    else 1.0
                )
                denominator = frequency + _K1 * (1 - _B + _B * length_ratio)
                scores[position] = scores.get(position, 0.0) + idf * (
                    frequency * (_K1 + 1) / denominator
                )

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

        hits: list[SearchHit] = []
        for position, score in ranked[: max(0, limit)]:
            section = self.sections[position]
            document = self.documents[section.slug]
            hits.append(
                SearchHit(
                    slug=section.slug,
                    title=document.title,
                    heading=section.heading,
                    anchor=section.anchor,
                    score=round(score, 4),
                    excerpt=excerpt_for(section.text, terms),
                )
            )
        return hits

    def count_matches(self, query: str) -> int:
        """How many sections match at all, before the limit is applied."""
        terms = tokenize(query)
        positions: set[int] = set()
        for term in terms:
            positions.update(self._postings.get(term, ()))
        return len(positions)


def excerpt_for(text: str, terms: list[str]) -> str:
    """Pick the paragraph that best demonstrates the match, and trim it.

    Returning the head of the section would be cheaper and would routinely
    return a paragraph that contains none of the query terms, which reads to a
    model as a false positive.
    """
    wanted = set(terms)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return ""

    best = blocks[0]
    best_score = -1
    for block in blocks:
        present = wanted & set(tokenize(block))
        if len(present) > best_score:
            best_score = len(present)
            best = block

    flattened = " ".join(best.split())
    if len(flattened) <= _EXCERPT_CHARS:
        return flattened

    # Cut on a word boundary so the excerpt does not end mid-token.
    cut = flattened[:_EXCERPT_CHARS]
    space = cut.rfind(" ")
    if space > _EXCERPT_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + " ..."
