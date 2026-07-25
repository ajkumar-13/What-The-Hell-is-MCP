"""In-memory protocol tests.

`Client(mcp)` connects straight to the server object with no subprocess and no
socket, so these run in milliseconds and still go through the real protocol
machinery: schemas, validation, result shapes, and error mapping.

Note the shape of every test: the client is opened with `async with` inside the
test body, not handed over by a yield fixture. The client owns an anyio task
group, and a task group has to be exited by the same task that entered it. A
yield fixture tears down in a different task and every test fails with
"Attempted to exit cancel scope in a different task".

The section at the bottom is the one this post exists for. Post 23 claims one
server works on every client, and the honest version of that claim is graceful
degradation: a client with tools only must lose nothing. Those tests assert it
rather than asserting the claim.
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp_types import PromptReference

from knowledge_base import mcp


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

async def test_the_expected_tools_are_published():
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {"search_docs", "get_doc", "list_topics"}


async def test_every_tool_publishes_an_output_schema():
    """A missing output schema is a silent failure, so assert on it."""
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
        assert tools, "server registered no tools"
        for tool in tools:
            assert tool.output_schema is not None, f"{tool.name} has no output schema"


async def test_every_tool_declares_annotations():
    async with Client(mcp) as c:
        for tool in (await c.list_tools()).tools:
            assert tool.annotations is not None, f"{tool.name} has no annotations"
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False


async def test_the_server_ships_instructions():
    """The only documentation some clients ever show. It has to name the tools."""
    async with Client(mcp) as c:
        instructions = c.instructions or ""
        for name in ("search_docs", "get_doc", "list_topics"):
            assert name in instructions


async def test_no_emoji_anywhere_in_the_published_surface():
    """Tool text lands in terminals, log files, and other people's UIs.

    Anything outside the basic multilingual plane is the cheapest thing to get
    wrong and the most annoying to debug when a Windows console refuses to
    encode it.
    """
    async with Client(mcp) as c:
        blobs: list[str] = [c.instructions or ""]
        for tool in (await c.list_tools()).tools:
            blobs += [tool.name, tool.title or "", tool.description or ""]
        for prompt in (await c.list_prompts()).prompts:
            blobs += [prompt.name, prompt.title or "", prompt.description or ""]
        result = await c.call_tool("search_docs", {"query": "database failover"})
        blobs.append(result.content[0].text)

        for blob in blobs:
            for character in blob:
                assert ord(character) < 0x2190, f"non-plain character {character!r}"


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

async def test_search_returns_structured_hits():
    async with Client(mcp) as c:
        result = await c.call_tool(
            "search_docs", {"query": "database failover", "limit": 3}
        )
        assert result.result_type == "complete"
        data = result.structured_content
        assert data["query"] == "database failover"
        assert 0 < data["returned"] <= 3
        assert data["total_matches"] >= data["returned"]
        top = data["results"][0]
        assert top["slug"] == "runbooks"
        assert top["excerpt"]
        assert top["score"] > 0


async def test_search_clamps_the_limit():
    async with Client(mcp) as c:
        result = await c.call_tool("search_docs", {"query": "the", "limit": 9999})
        assert result.structured_content["returned"] <= 20


async def test_search_with_no_matches_is_not_an_error():
    async with Client(mcp) as c:
        result = await c.call_tool("search_docs", {"query": "quokka bioluminescence"})
        assert result.is_error is not True
        data = result.structured_content
        assert data["returned"] == 0
        assert data["results"] == []


async def test_get_doc_returns_the_whole_document():
    async with Client(mcp) as c:
        result = await c.call_tool("get_doc", {"slug": "architecture"})
        data = result.structured_content
        assert data["slug"] == "architecture"
        assert data["section"] == ""
        assert data["word_count"] > 100
        assert data["sections"], "the document should advertise its sections"
        assert data["text"].startswith("# ")


async def test_get_doc_returns_one_section():
    async with Client(mcp) as c:
        result = await c.call_tool(
            "get_doc", {"slug": "runbooks", "section": "database-failover"}
        )
        data = result.structured_content
        assert data["section"] == "Database failover"
        assert "orchard db topology" in data["text"]
        assert data["word_count"] < 400


async def test_a_search_hit_feeds_straight_into_get_doc():
    """The composition the instructions promise, exercised end to end."""
    async with Client(mcp) as c:
        hit = (
            await c.call_tool("search_docs", {"query": "poison message", "limit": 1})
        ).structured_content["results"][0]

        doc = await c.call_tool(
            "get_doc", {"slug": hit["slug"], "section": hit["anchor"]}
        )
        assert doc.is_error is not True
        assert doc.structured_content["section"] == hit["heading"]


async def test_unknown_slug_is_an_error_result_that_names_the_alternatives():
    """An error a model can recover from beats a protocol failure it cannot."""
    async with Client(mcp) as c:
        result = await c.call_tool("get_doc", {"slug": "nope"})
        assert result.is_error is True
        text = result.content[0].text
        assert "No document named 'nope'" in text
        assert "runbooks" in text


async def test_unknown_section_names_the_valid_anchors():
    async with Client(mcp) as c:
        result = await c.call_tool("get_doc", {"slug": "runbooks", "section": "nope"})
        assert result.is_error is True
        assert "database-failover" in result.content[0].text


async def test_list_topics_describes_every_document():
    async with Client(mcp) as c:
        data = (await c.call_tool("list_topics", {})).structured_content
        assert data["total"] == len(data["topics"])
        slugs = {t["slug"] for t in data["topics"]}
        assert {"onboarding", "runbooks", "architecture", "faq", "snippets"} <= slugs
        for topic in data["topics"]:
            assert topic["summary"]
            assert topic["sections"]


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------

async def test_every_document_is_listed_as_a_concrete_resource():
    """Concrete resources are what a user can pick from a menu without typing a URI."""
    async with Client(mcp) as c:
        uris = {str(r.uri) for r in (await c.list_resources()).resources}
        assert "knowledge://index" in uris
        for slug in ("onboarding", "runbooks", "architecture", "faq", "snippets"):
            assert f"knowledge://doc/{slug}" in uris


async def test_the_templates_are_advertised():
    async with Client(mcp) as c:
        templates = {
            t.uri_template for t in (await c.list_resource_templates()).resource_templates
        }
        assert "knowledge://doc/{slug}" in templates
        assert "knowledge://section/{slug}/{anchor}" in templates


async def test_a_concrete_resource_wins_over_the_overlapping_template():
    """`knowledge://doc/runbooks` must serve the document, not the fallback handler."""
    async with Client(mcp) as c:
        text = (await c.read_resource("knowledge://doc/runbooks")).contents[0].text
        assert text.startswith("# Runbooks")


async def test_the_index_resource_lists_the_corpus():
    async with Client(mcp) as c:
        text = (await c.read_resource("knowledge://index")).contents[0].text
        assert "knowledge://doc/onboarding" in text
        assert "database-failover" in text


async def test_a_section_resource_resolves_by_anchor():
    async with Client(mcp) as c:
        text = (
            await c.read_resource("knowledge://section/runbooks/database-failover")
        ).contents[0].text
        assert text.startswith("# Database failover")


async def test_an_unknown_slug_reaching_the_template_fails_usefully():
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("knowledge://doc/nope")
        assert "Unknown document" in str(excinfo.value)


async def test_a_traversal_shaped_uri_does_not_match_the_template_at_all():
    """A URI with extra separators fails to match before any handler is reached."""
    async with Client(mcp) as c:
        with pytest.raises(Exception) as excinfo:
            await c.read_resource("knowledge://doc/../../etc/passwd")
        assert "Unknown resource" in str(excinfo.value)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

async def test_the_expected_prompts_are_published():
    async with Client(mcp) as c:
        names = {p.name for p in (await c.list_prompts()).prompts}
        assert names == {"onboard_engineer", "incident_playbook", "explain_topic"}


async def test_a_prompt_embeds_real_handbook_content():
    """A prompt that only restates the question has saved the user nothing."""
    async with Client(mcp) as c:
        result = await c.get_prompt(
            "incident_playbook", {"symptom": "reconciler lag alert is firing"}
        )
        text = result.messages[0].content.text
        assert "reconciler lag alert is firing" in text
        assert "get_doc(" in text, "the prompt should hand the model its next call"
        assert "Runbooks" in text


async def test_a_prompt_names_the_tools_a_minimal_client_still_has():
    async with Client(mcp) as c:
        text = (
            await c.get_prompt("onboard_engineer", {"area": "payments"})
        ).messages[0].content.text
        assert "payments" in text
        assert "get_doc" in text


async def test_explain_topic_degrades_on_a_bad_slug():
    """An expansion is not a place to raise. Say what went wrong, in the message."""
    async with Client(mcp) as c:
        text = (
            await c.get_prompt("explain_topic", {"slug": "nope"})
        ).messages[0].content.text
        assert "does not exist" in text
        assert "list_topics" in text


async def test_completion_filters_by_prefix():
    async with Client(mcp) as c:
        hit = await c.complete(
            ref=PromptReference(name="explain_topic"),
            argument={"name": "slug", "value": "run"},
        )
        assert "runbooks" in hit.completion.values

        miss = await c.complete(
            ref=PromptReference(name="explain_topic"),
            argument={"name": "slug", "value": "zzz"},
        )
        assert miss.completion.values == []


# --------------------------------------------------------------------------
# Graceful degradation: the actual claim of post 23
# --------------------------------------------------------------------------

async def test_every_resource_is_reachable_through_a_tool():
    """A tools-only client must lose presentation, never capability.

    This is the test that keeps the table in `tools.py` honest. For every
    concrete resource the server publishes, some tool call returns the same
    text. Add a resource without adding a tool path to it and this fails.
    """
    async with Client(mcp) as c:
        for resource in (await c.list_resources()).resources:
            uri = str(resource.uri)
            via_resource = (await c.read_resource(uri)).contents[0].text

            if uri == "knowledge://index":
                # Covered by list_topics, which returns the same facts as data
                # rather than as Markdown, so compare on content not on bytes.
                topics = (await c.call_tool("list_topics", {})).structured_content
                for topic in topics["topics"]:
                    assert f"knowledge://doc/{topic['slug']}" in via_resource
                continue

            slug = uri.rsplit("/", 1)[-1]
            via_tool = (
                await c.call_tool("get_doc", {"slug": slug})
            ).structured_content["text"]
            assert via_tool == via_resource, f"{uri} is not reachable through get_doc"


async def test_a_tools_only_client_can_still_answer_a_real_question():
    """The whole workflow, using nothing but tools/list and tools/call.

    This is what the server looks like to the least capable client in
    `clients/`. If this test passes, the post's claim holds for all of them.
    """
    async with Client(mcp) as c:
        assert (await c.list_tools()).tools

        topics = (await c.call_tool("list_topics", {})).structured_content
        assert topics["total"] >= 5

        hits = (
            await c.call_tool(
                "search_docs", {"query": "what do I do if I leaked a secret"}
            )
        ).structured_content["results"]
        assert hits

        body = (
            await c.call_tool(
                "get_doc", {"slug": hits[0]["slug"], "section": hits[0]["anchor"]}
            )
        ).structured_content["text"]
        assert "rotat" in body.lower()
