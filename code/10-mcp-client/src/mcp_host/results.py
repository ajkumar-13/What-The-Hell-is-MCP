"""Reading a tool result properly.

Post 10. This is the smallest module in the project and the one that most
client code gets wrong, in two specific ways.

**One: a failed tool is a successful response.** `call_tool` raises only when
the *transport* or the *protocol* failed: the process died, the JSON-RPC
request was rejected, the tool does not exist as far as the dispatcher is
concerned. A tool that ran and failed comes back as a perfectly ordinary
`CallToolResult` with `is_error=True` and the error text in `content`. A
`try/except` around `call_tool` does not see it. If you only catch exceptions
you will hand the model "Error executing tool find_process: 2 validation
errors" as though it were the answer, and the model will believe you.

**Two: text is not the only content block.** The wire type is a union of five:
text, image, audio, resource links, and embedded resources. Reading `.text`
off each block and dropping everything else silently discards images and
resources. Separately, a tool with an output schema returns
`structured_content` as well, which is the *machine-readable* form of the same
answer and is usually the one you want.

Everything here is pure: no I/O, no async. That is what makes it easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

# How much of a base64 payload to acknowledge in a text rendering. The bytes
# themselves must never be pasted into a model's context: a 2 MB screenshot
# becomes a few hundred thousand tokens of noise.
_BINARY_NOTE = "{kind} ({mime}, {size} bytes of base64, not inlined)"


@dataclass(frozen=True)
class Block:
    """One content block, flattened into something printable.

    `kind` is the wire discriminator. `text` is a rendering safe to put in
    front of a model. `data` keeps the details a host might act on: the mime
    type of an image it could display, the URI of a resource it could fetch.
    """

    kind: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


def describe_block(block: Any) -> Block:
    """Flatten any content block, including ones this SDK version predates.

    The `isinstance` chain is ordered by how common each type is. The final
    fallback matters: the union is versioned and a newer server may send a
    block type this client has never heard of. Falling through to a readable
    placeholder keeps the call usable instead of raising on an unknown `type`.
    """
    if isinstance(block, TextContent):
        return Block("text", block.text)

    if isinstance(block, ImageContent):
        return Block(
            "image",
            _BINARY_NOTE.format(
                kind="image", mime=block.mime_type, size=len(block.data or "")
            ),
            {"mime_type": block.mime_type, "data": block.data},
        )

    if isinstance(block, AudioContent):
        return Block(
            "audio",
            _BINARY_NOTE.format(
                kind="audio", mime=block.mime_type, size=len(block.data or "")
            ),
            {"mime_type": block.mime_type, "data": block.data},
        )

    if isinstance(block, ResourceLink):
        # A pointer, not the content. The host decides whether to follow it,
        # which is the point: a server can offer a 500 MB log without shipping
        # 500 MB through the model's context.
        label = block.title or block.name
        return Block(
            "resource_link",
            f"resource link: {label} <{block.uri}>",
            {
                "uri": str(block.uri),
                "name": block.name,
                "mime_type": block.mime_type,
                "size": block.size,
            },
        )

    if isinstance(block, EmbeddedResource):
        inner = block.resource
        uri = str(getattr(inner, "uri", ""))
        if isinstance(inner, TextResourceContents):
            return Block(
                "resource",
                f"resource {uri}:\n{inner.text}",
                {"uri": uri, "mime_type": inner.mime_type, "text": inner.text},
            )
        if isinstance(inner, BlobResourceContents):
            return Block(
                "resource",
                _BINARY_NOTE.format(
                    kind=f"resource {uri}",
                    mime=inner.mime_type,
                    size=len(inner.blob or ""),
                ),
                {"uri": uri, "mime_type": inner.mime_type, "blob": inner.blob},
            )
        return Block("resource", f"resource {uri}", {"uri": uri})

    kind = getattr(block, "type", None) or type(block).__name__
    return Block("unknown", f"unsupported content block: {kind}", {"type": kind})


@dataclass(frozen=True)
class ToolOutcome:
    """What actually happened when we called a tool.

    `ok` is the single flag a host branches on, and it is the union of two
    different failures that look nothing alike on the wire: the transport
    raised, or the result carried `is_error`.
    """

    tool: str
    ok: bool
    blocks: tuple[Block, ...] = ()
    # Any, not dict. SEP-2106 allows structuredContent to be any JSON value, and
    # the SDK types CallToolResult.structured_content as Any to match. A server
    # returning a top-level array is unusual but conformant.
    structured: Any | None = None
    failure: str | None = None

    @property
    def text(self) -> str:
        """Every block's rendering, joined. Empty if the tool returned nothing."""
        return "\n".join(b.text for b in self.blocks if b.text).strip()

    def for_model(self) -> str:
        """The string to send back as the tool result.

        A failure is labelled, not hidden. Models recover from a tool error
        they can read ("no such process") and cannot recover from one that
        arrives disguised as an answer.
        """
        if not self.ok:
            detail = self.text or self.failure or "the tool reported an error"
            return f"TOOL FAILED ({self.tool}): {detail}"
        body = self.text
        if self.structured is not None and not body:
            return repr(self.structured)
        return body or "(the tool returned no content)"

    @classmethod
    def failed(cls, tool: str, reason: str) -> ToolOutcome:
        """A failure that never reached the server: denied, unknown, crashed."""
        return cls(tool=tool, ok=False, failure=reason)


def read_result(tool: str, result: CallToolResult) -> ToolOutcome:
    """Turn a `CallToolResult` into something a host can branch on.

    `result.is_error` is checked explicitly, and it is the first thing
    checked. It is optional on the wire and defaults to False, so absent and
    false mean the same thing; and it is *not* an exception, so nothing
    upstream of here will have caught it.
    """
    blocks = tuple(describe_block(b) for b in (result.content or []))
    return ToolOutcome(
        tool=tool,
        ok=not bool(result.is_error),
        blocks=blocks,
        structured=result.structured_content,
        failure=None if not result.is_error else "server reported isError",
    )
