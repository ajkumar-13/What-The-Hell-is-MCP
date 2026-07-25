"""Resources: the same corpus, addressed rather than searched.

A tool is called by the model when it decides it needs something. A resource is
read by the application, usually because a user attached it. The distinction
matters here because it decides which clients see this file at all: several of
the hosts in `clients/` never call `resources/list`, and on those hosts
everything below is invisible.

That is fine, and it is the point of the design. Nothing in this file is
load-bearing. Every URI here has a tool in `tools.py` that returns the same
text, so a tools-only client loses presentation, not capability. Adding a
resource is how you make a capable client nicer; it is not how you add a
capability.

## Three shapes, on purpose

- `knowledge://index` - one static resource, the table of contents.
- `knowledge://doc/<slug>` - one **concrete** resource per document, registered
  in a loop. Concrete resources are the ones that appear in `resources/list`,
  which is what lets a user pick "Runbooks" from a menu without typing a URI.
- `knowledge://doc/{slug}` and `knowledge://section/{slug}/{anchor}` - two
  **templates**, for a client that already knows the identifier it wants.

The first template deliberately overlaps the concrete URIs. The SDK resolves
concrete resources before templates, so `knowledge://doc/runbooks` is served
from the registered `TextResource` and only an unknown slug ever reaches the
template handler, where it can produce a decent error. Listing both is not
redundant: `resources/list` shows the five real documents, and
`resources/templates/list` advertises the shape, so both kinds of client are
served from one registration block.
"""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.server.mcpserver.resources import TextResource

from .app import documents, index, mcp


@mcp.resource(
    "knowledge://index",
    title="Handbook contents",
    description="Every document in the handbook, with its sections.",
    mime_type="text/markdown",
)
def handbook_index() -> str:
    """A Markdown table of contents for the whole corpus.

    The tool-only equivalent is `list_topics()`, which returns the same
    information as structured data.
    """
    if not documents:
        return "# Team handbook\n\nThe corpus is empty.\n"

    lines = ["# Team handbook", ""]
    for document in sorted(documents, key=lambda d: d.slug):
        lines.append(f"## {document.title}")
        lines.append("")
        lines.append(f"`knowledge://doc/{document.slug}`")
        lines.append("")
        if document.summary:
            lines.append(document.summary)
            lines.append("")
        for section in document.sections:
            lines.append(f"- {section.heading} (`{section.anchor}`)")
        lines.append("")
    return "\n".join(lines)


# One concrete resource per document. Registering these in a loop rather than
# writing five decorated functions keeps the corpus and the resource list from
# drifting apart: drop a new Markdown file into `knowledge/` and it shows up
# here, in search, and in `list_topics`, without an edit anywhere.
for _document in sorted(documents, key=lambda d: d.slug):
    mcp.add_resource(
        TextResource(
            uri=f"knowledge://doc/{_document.slug}",
            name=_document.slug,
            title=_document.title,
            description=_document.summary or f"The {_document.slug} document.",
            mime_type="text/markdown",
            text=_document.text,
        )
    )


@mcp.resource(
    "knowledge://doc/{slug}",
    title="Handbook document by slug",
    description="The full Markdown of one handbook document.",
    mime_type="text/markdown",
)
def document_by_slug(slug: str) -> str:
    """Serve one document by name.

    In practice the concrete registrations above answer every real slug, so this
    handler is reached only for a slug that does not exist. Its job is to fail
    usefully: the SDK turns `ResourceNotFoundError` into `-32602 Invalid params`
    with this message attached, which is a great deal more actionable than a
    bare "unknown resource".

    A template variable is untrusted input, and the lookup is the defense. There
    is no path built from `slug` anywhere in this function, so there is nothing
    for a `../` to traverse.
    """
    document = index.document(slug)
    if document is None:
        known = ", ".join(sorted(index.documents)) or "none"
        raise ResourceNotFoundError(f"Unknown document {slug!r}. Available: {known}.")
    return document.text


@mcp.resource(
    "knowledge://section/{slug}/{anchor}",
    title="Handbook section",
    description="One section of one handbook document, addressed by anchor.",
    mime_type="text/markdown",
)
def section_by_anchor(slug: str, anchor: str) -> str:
    """Serve one section, using the `slug` and `anchor` a search hit carries.

    This is the resource that makes a search result citable. A host that
    supports resources can turn a `search_docs` hit into a link the user can
    pin into the conversation, and the URI is stable because `slugify` is.
    """
    section = index.section(slug, anchor)
    if section is None:
        document = index.document(slug)
        if document is None:
            known = ", ".join(sorted(index.documents)) or "none"
            raise ResourceNotFoundError(
                f"Unknown document {slug!r}. Available: {known}."
            )
        anchors = ", ".join(s.anchor for s in document.sections) or "none"
        raise ResourceNotFoundError(
            f"Unknown section {anchor!r} in {slug!r}. Available: {anchors}."
        )
    return f"# {section.heading}\n\n{section.text}\n"
