"""Reading a result. Post 10.

These are pure unit tests: no client, no server, no event loop. Result
handling is the one part of a client with no I/O in it, and that is exactly
why it should be the most thoroughly tested part.

The first two tests cover the two defects that make a client lie to its model:
a failed tool reported as an answer, and structured content thrown away.
"""

from __future__ import annotations

from mcp_types import (
    AudioContent,
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)

from mcp_host.results import ToolOutcome, describe_block, read_result


# --------------------------------------------------------------------------
# The isError branch
# --------------------------------------------------------------------------


def test_a_failed_tool_is_not_an_answer():
    """The defect this module exists to prevent.

    The call did not raise. It returned successfully, carrying a failure. A
    client that only catches exceptions hands this text to the model as the
    result of the call.
    """
    result = CallToolResult(
        content=[TextContent(text="Error executing tool find_process: bad input")],
        is_error=True,
    )
    outcome = read_result("find_process", result)

    assert outcome.ok is False
    assert outcome.for_model().startswith("TOOL FAILED (find_process)")
    assert "bad input" in outcome.for_model()


def test_a_successful_tool_is_not_labeled_as_failed():
    result = CallToolResult(content=[TextContent(text="12 processes")])
    outcome = read_result("find_process", result)

    assert outcome.ok is True
    assert outcome.for_model() == "12 processes"


def test_is_error_defaults_to_false():
    """A server that says nothing about `isError` succeeded.

    Worth pinning: the field is optional on the wire, so "absent" and "false"
    must mean the same thing to the client.
    """
    result = CallToolResult(content=[TextContent(text="ok")])
    assert result.is_error is False
    assert read_result("t", result).ok is True


def test_a_transport_failure_produces_the_same_shape():
    """A raised exception and an isError result must be indistinguishable to
    the caller, or every call site has to handle both."""
    outcome = ToolOutcome.failed("find_process", "ConnectError: refused")
    assert outcome.ok is False
    assert "ConnectError" in outcome.for_model()


# --------------------------------------------------------------------------
# Structured content
# --------------------------------------------------------------------------


def test_structured_content_is_kept():
    result = CallToolResult(
        content=[TextContent(text='{"cpu_percent": 12.5}')],
        structured_content={"cpu_percent": 12.5},
    )
    outcome = read_result("get_system_info", result)

    assert outcome.structured == {"cpu_percent": 12.5}
    assert outcome.text == '{"cpu_percent": 12.5}'


def test_structured_content_is_used_when_there_is_no_text():
    """A server may return structured output and no text blocks at all."""
    result = CallToolResult(content=[], structured_content={"result": 5})
    outcome = read_result("add", result)

    assert outcome.ok is True
    assert "result" in outcome.for_model()


def test_a_tool_that_returns_nothing_still_says_something():
    outcome = read_result("noop", CallToolResult(content=[]))
    assert outcome.ok is True
    assert outcome.for_model() == "(the tool returned no content)"


# --------------------------------------------------------------------------
# Every content block type
# --------------------------------------------------------------------------


def test_text_block():
    block = describe_block(TextContent(text="hello"))
    assert block.kind == "text"
    assert block.text == "hello"


def test_image_block_is_described_but_not_inlined():
    """The base64 must not end up in a model's context.

    A screenshot is a megabyte of base64. Pasting it into the transcript costs
    a few hundred thousand tokens and tells the model nothing.
    """
    payload = "A" * 4096
    block = describe_block(ImageContent(data=payload, mime_type="image/png"))

    assert block.kind == "image"
    assert payload not in block.text
    assert "image/png" in block.text
    # ...but a host that wants to display it still can.
    assert block.data["data"] == payload


def test_audio_block():
    block = describe_block(AudioContent(data="BBBB", mime_type="audio/wav"))
    assert block.kind == "audio"
    assert "audio/wav" in block.text


def test_resource_link_keeps_the_uri():
    block = describe_block(
        ResourceLink(name="top", uri="system://processes/top", mime_type="text/markdown")
    )
    assert block.kind == "resource_link"
    assert block.data["uri"] == "system://processes/top"
    assert "system://processes/top" in block.text


def test_embedded_text_resource_is_inlined():
    block = describe_block(
        EmbeddedResource(
            resource=TextResourceContents(uri="config://app", text="hello world")
        )
    )
    assert block.kind == "resource"
    assert "hello world" in block.text


def test_embedded_binary_resource_is_not_inlined():
    payload = "Z" * 2048
    block = describe_block(
        EmbeddedResource(
            resource=BlobResourceContents(
                uri="file:///x.bin", blob=payload, mime_type="application/octet-stream"
            )
        )
    )
    assert block.kind == "resource"
    assert payload not in block.text


def test_an_unknown_block_type_does_not_raise():
    """The content union is versioned. A newer server can send a block type
    this client has never seen, and the right answer is a placeholder, not a
    crash on `block.type`."""

    class FutureContent:
        type = "hologram"

    block = describe_block(FutureContent())
    assert block.kind == "unknown"
    assert "hologram" in block.text


def test_mixed_content_keeps_every_block_in_order():
    result = CallToolResult(
        content=[
            TextContent(text="here is the chart"),
            ImageContent(data="AAAA", mime_type="image/png"),
            ResourceLink(name="data", uri="file:///data.csv"),
        ]
    )
    outcome = read_result("chart", result)

    assert [b.kind for b in outcome.blocks] == ["text", "image", "resource_link"]
    assert outcome.for_model().splitlines()[0] == "here is the chart"
