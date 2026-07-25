"""Tools: the floor every client stands on.

## Why this file is the graceful-degradation strategy

A client that supports tools and nothing else must lose no capability. That is
not a slogan, it is a constraint this file is written against, and it is
checkable: for every resource in `resources.py` and every prompt in
`prompts.py`, there is a tool here that reaches the same text.

| Also exposed as | Tool that covers it |
|---|---|
| `knowledge://index` (resource) | `list_topics()` |
| `knowledge://doc/{slug}` (resource) | `get_doc(slug)` |
| `knowledge://section/{slug}/{anchor}` (resource) | `get_doc(slug, section)` |
| every prompt in `prompts.py` | `search_docs()` plus `get_doc()` |

`tests/test_server.py::test_every_resource_is_reachable_through_a_tool` asserts
the first three rows, so the table cannot quietly rot.

The rule that makes this work is worth stating plainly, because it is easy to
get backwards: **resources and prompts are additive, never load-bearing.** Put
the capability in a tool and let the richer clients present it more nicely. Do
it the other way around and your server silently does less on Zed than it does
on Claude Desktop, and nobody can tell you why.

## Every tool is annotated

`ToolAnnotations` are hints for the host, not enforcement; nothing stops a badly
written tool from claiming `read_only_hint=True` and deleting your files. The
enforcement here is that these three functions genuinely only read an in-memory
index. What the annotation buys is that a host can skip a confirmation prompt
for them, which is the difference between a knowledge base that gets used and
one that asks permission four times per question.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .app import index, mcp
from .index import SearchHit

# Read-only, non-destructive, idempotent, and closed-world: the answers come
# from a fixed corpus loaded at startup, so the same query returns the same
# result and nothing outside this process is touched.
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

_MAX_RESULTS = 20


@dataclass
class SearchResults:
    """What `search_docs` returns.

    `total_matches` is deliberately separate from `returned`. A model that sees
    `returned: 5, total_matches: 5` should stop; one that sees
    `returned: 5, total_matches: 23` knows a narrower query would help. Collapse
    the two into one number and you have removed the model's ability to tell
    "that is everything" from "that is the first page".
    """

    query: str
    total_matches: int
    returned: int
    results: list[SearchHit]


@dataclass
class DocumentText:
    """What `get_doc` returns: one document, or one section of one."""

    slug: str
    title: str
    section: str
    word_count: int
    sections: list[str]
    text: str


@dataclass
class Topic:
    """One document, described rather than quoted."""

    slug: str
    title: str
    summary: str
    word_count: int
    sections: list[str]


@dataclass
class TopicList:
    """What `list_topics` returns."""

    total: int
    topics: list[Topic]


def _known_slugs() -> str:
    return ", ".join(sorted(index.documents)) or "none (the corpus is empty)"


@mcp.tool(
    title="Search the handbook",
    annotations=READ_ONLY,
)
def search_docs(query: str, limit: int = 5) -> SearchResults:
    """Search the team handbook and return the best-matching sections.

    Ranking is BM25 over handbook sections, so a hit names the section to read,
    not just the file. Each hit carries a short excerpt, plus the `slug` and
    `section` values that `get_doc` takes.

    Args:
        query: What you are looking for, in plain words. Whole questions work.
        limit: Maximum number of sections to return, between 1 and 20.
    """
    # Clamp rather than reject. A model asking for 500 results is not being
    # hostile, it just does not know the ceiling; doing the sensible thing
    # quietly beats an error it has to recover from.
    limit = max(1, min(limit, _MAX_RESULTS))

    hits = index.search(query, limit=limit)
    return SearchResults(
        query=query,
        total_matches=index.count_matches(query),
        returned=len(hits),
        results=hits,
    )


@mcp.tool(
    title="Read a handbook document",
    annotations=READ_ONLY,
)
def get_doc(slug: str, section: str = "") -> DocumentText:
    """Return the full text of one handbook document, or of one section of it.

    Args:
        slug: Document identifier, such as `runbooks`. Use `list_topics` to see
            every valid value, or take one from a `search_docs` hit.
        section: Optional section anchor, such as `database-failover`, taken
            from a `search_docs` hit. Omit it to get the whole document.
    """
    document = index.document(slug)
    if document is None:
        # A ToolError becomes an error result the model can read and recover
        # from, rather than a protocol-level failure the host has to surface as
        # a broken server. Naming the valid slugs turns a dead end into a retry.
        raise ToolError(f"No document named {slug!r}. Available documents: {_known_slugs()}.")

    headings = [s.heading for s in document.sections]

    if not section:
        return DocumentText(
            slug=document.slug,
            title=document.title,
            section="",
            word_count=document.word_count,
            sections=headings,
            text=document.text,
        )

    found = index.section(slug, section)
    if found is None:
        anchors = ", ".join(s.anchor for s in document.sections) or "none"
        raise ToolError(
            f"No section {section!r} in {slug!r}. Available sections: {anchors}."
        )

    return DocumentText(
        slug=document.slug,
        title=document.title,
        section=found.heading,
        word_count=len(found.text.split()),
        sections=headings,
        text=found.text,
    )


@mcp.tool(
    title="List handbook topics",
    annotations=READ_ONLY,
)
def list_topics() -> TopicList:
    """List every document in the handbook, with a one-line summary of each.

    Call this when you need to know what the handbook covers before you can ask
    a useful question. It is also the tool-only equivalent of reading the
    `knowledge://index` resource.
    """
    return TopicList(
        total=len(index.documents),
        topics=[
            Topic(
                slug=document.slug,
                title=document.title,
                summary=document.summary,
                word_count=document.word_count,
                sections=[s.heading for s in document.sections],
            )
            for document in sorted(index.documents.values(), key=lambda d: d.slug)
        ],
    )
