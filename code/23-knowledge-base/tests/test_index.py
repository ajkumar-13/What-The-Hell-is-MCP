"""Tests for the retrieval layer, with no MCP anywhere in the file.

`index.py` imports nothing from the SDK, so it can be tested as a plain library.
That separation is worth keeping: when a search result looks wrong, these tests
tell you whether the bug is in the ranking or in the protocol layer, and you do
not have to open a client to find out.
"""

from __future__ import annotations

import pytest

from knowledge_base.index import (
    KnowledgeIndex,
    excerpt_for,
    load_corpus,
    slugify,
    tokenize,
)


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


@pytest.fixture(scope="module")
def index(corpus):
    return KnowledgeIndex(corpus)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def test_the_corpus_is_actually_committed(corpus):
    """The corpus is part of the deliverable, not a thing the reader supplies.

    A server that starts cleanly and answers every search with nothing looks
    exactly like a ranking bug and is usually a missing directory. This test is
    here so that failure mode can never reach a reader.
    """
    assert len(corpus) >= 5, "knowledge/ should contain the committed handbook"
    slugs = {d.slug for d in corpus}
    assert {"onboarding", "runbooks", "architecture", "faq", "snippets"} <= slugs


def test_every_document_has_a_title_and_a_summary(corpus):
    for document in corpus:
        assert document.title and not document.title.startswith("#")
        assert document.summary, f"{document.slug} has no summary paragraph"


def test_documents_are_split_into_sections(corpus):
    for document in corpus:
        assert len(document.sections) >= 3, f"{document.slug} barely split"
        for section in document.sections:
            assert section.heading
            assert section.anchor
            assert section.text.strip()


def test_missing_directory_yields_an_empty_corpus(tmp_path):
    """Absent input is not an exception. The server logs a warning and serves nothing."""
    assert load_corpus(tmp_path / "nope") == []


def test_an_empty_index_answers_rather_than_raising():
    empty = KnowledgeIndex([])
    assert empty.search("anything") == []
    assert empty.count_matches("anything") == 0
    assert empty.section_count == 0


# --------------------------------------------------------------------------
# Tokenizing
# --------------------------------------------------------------------------

def test_tokenize_lowercases_and_drops_stopwords():
    assert tokenize("How do I roll BACK the gateway?") == ["roll", "back", "gateway"]


def test_tokenize_drops_single_characters():
    assert tokenize("a b c postgres 9 10") == ["postgres", "10"]


def test_slugify_is_stable_and_uri_safe():
    assert slugify("Database failover") == "database-failover"
    assert slugify("Elevated 5xx rate at the gateway") == "elevated-5xx-rate-at-the-gateway"
    # Stability matters: a pinned resource URI must keep resolving tomorrow.
    assert slugify("Database  failover!") == slugify("Database failover")


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def test_search_finds_the_right_section_not_just_the_right_file(index):
    hits = index.search("database failover promote replica", limit=3)
    assert hits, "no hits for a query the runbooks clearly answer"
    assert hits[0].slug == "runbooks"
    assert hits[0].heading == "Database failover"


def test_the_heading_boost_beats_a_short_passing_mention(index):
    """The regression that motivated `_HEADING_WEIGHT`.

    `architecture.md` mentions "managed failover" in a two-sentence aside about
    Postgres. Without the boost that aside out-scores the runbook section
    literally titled "Database failover", because it is short and BM25 rewards
    short matches. A section's heading is its most deliberate summary and has to
    count for more than a mention in passing.
    """
    hits = index.search("database failover", limit=5)
    assert hits[0].heading == "Database failover"
    assert hits[0].slug == "runbooks"


def test_search_ranks_the_incident_runbook_above_the_faq(index):
    """Both documents mention the reconciler. The runbook is the one to read."""
    hits = index.search("reconciler lag poison message quarantine", limit=5)
    top = hits[0]
    assert top.slug == "runbooks"
    assert "econcil" in top.heading


def test_search_crosses_documents(index):
    """The same query should be able to reach whichever document answers it."""
    idempotency = index.search("idempotency key retry double payment", limit=1)
    assert idempotency[0].slug in {"snippets", "faq"}

    boundaries = index.search("load-bearing boundaries design note", limit=1)
    assert boundaries[0].slug == "architecture"


def test_scores_are_positive_and_ordered(index):
    hits = index.search("secret vault rotation", limit=5)
    assert hits
    assert all(hit.score > 0 for hit in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_limit_is_honored(index):
    assert len(index.search("the database", limit=2)) <= 2
    assert index.search("the database", limit=0) == []


def test_a_query_with_no_matches_returns_nothing(index):
    """An empty result is an answer, not an error."""
    assert index.search("quokka bioluminescence") == []
    assert index.count_matches("quokka bioluminescence") == 0


def test_an_empty_query_returns_nothing(index):
    assert index.search("") == []
    assert index.search("   the a of   ") == []


def test_total_matches_can_exceed_what_is_returned(index):
    """The distinction a model needs to decide whether to narrow its query."""
    total = index.count_matches("database")
    returned = len(index.search("database", limit=2))
    assert total >= returned
    assert total > 2


# --------------------------------------------------------------------------
# Excerpts
# --------------------------------------------------------------------------

def test_excerpt_prefers_the_paragraph_containing_the_terms():
    text = (
        "An opening paragraph about nothing in particular.\n"
        "\n"
        "The connection pool is created once at startup and closed at shutdown.\n"
    )
    got = excerpt_for(text, ["connection", "pool"])
    assert "connection pool" in got


def test_excerpt_is_bounded_and_cut_on_a_word_boundary():
    text = " ".join(["reconciliation"] * 400)
    got = excerpt_for(text, ["reconciliation"])
    assert len(got) < 400
    assert got.endswith("...")
    assert "reconcilia ..." not in got


def test_excerpt_is_a_single_line(index):
    """Multi-line excerpts wreck any client that renders hits in a list."""
    for hit in index.search("rollback deployment gateway", limit=5):
        assert "\n" not in hit.excerpt


def test_every_hit_carries_a_usable_excerpt(index):
    for hit in index.search("tls certificate expiry renew", limit=5):
        assert hit.excerpt.strip()


# --------------------------------------------------------------------------
# Addressing
# --------------------------------------------------------------------------

def test_a_hit_can_be_fetched_back_by_slug_and_anchor(index):
    """This is the contract that makes `search_docs` and `get_doc` compose."""
    hits = index.search("expired certificate mesh authority", limit=3)
    assert hits
    for hit in hits:
        section = index.section(hit.slug, hit.anchor)
        assert section is not None, f"{hit.slug}/{hit.anchor} did not resolve"
        assert section.heading == hit.heading


def test_unknown_identifiers_resolve_to_none(index):
    assert index.document("nope") is None
    assert index.section("runbooks", "nope") is None
    assert index.section("nope", "nope") is None
