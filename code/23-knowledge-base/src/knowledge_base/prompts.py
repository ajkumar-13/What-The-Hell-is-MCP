"""Prompts: user-invocable, and never the only way to do anything.

A prompt is chosen by a person, not by a model. In most hosts that means a slash
command or a menu item, and the prompt's job is to expand into a well-formed
opening message with real context already attached.

Like `resources.py`, this file is additive. A client with no prompt support
loses three shortcuts and no capability: everything a prompt below does, a model
can do by calling `search_docs` and `get_doc` itself. That is why each prompt
ends by naming the tools it expects to be used next, rather than assuming the
model can see how the text was assembled.

## Prompts are pre-filled, not pre-answered

Each prompt runs a real search when it is expanded, and pastes the top hits into
the message. The model still has to read them and still has to follow up with
`get_doc`, but it starts from the right part of the handbook instead of guessing
a query. That is most of the value of a prompt: it removes the first, worst
search.

Completions are wired up here too, so a host that supports argument completion
offers the real document slugs while the user is typing rather than making them
remember five identifiers.
"""

from __future__ import annotations

from mcp.server.mcpserver.prompts.base import Message, UserMessage
from mcp_types import Completion, PromptReference

from .app import index, mcp


def _cite(query: str, limit: int = 3) -> str:
    """Render the top hits for `query` as a Markdown block for a prompt body."""
    hits = index.search(query, limit=limit)
    if not hits:
        return "The handbook has nothing matching that. Say so rather than guessing."

    lines = []
    for hit in hits:
        lines.append(f"- **{hit.title} / {hit.heading}**")
        lines.append(f"  (`get_doc(slug=\"{hit.slug}\", section=\"{hit.anchor}\")`)")
        lines.append(f"  {hit.excerpt}")
    return "\n".join(lines)


@mcp.prompt(
    title="Onboard a new engineer",
    description="Draft a first-week plan for a new joiner, from the handbook.",
)
def onboard_engineer(area: str = "platform") -> list[Message]:
    """Open an onboarding conversation with the relevant handbook sections attached."""
    return [
        UserMessage(
            f"A new engineer is joining the {area} team on Monday. Draft their "
            "first week from the team handbook, not from general advice.\n"
            "\n"
            "The handbook already offers these sections:\n"
            f"{_cite(f'onboarding first week {area} setup access')}\n"
            "\n"
            "Read the promising ones in full with get_doc, then write the plan "
            "day by day. Where the handbook is silent, say so explicitly instead "
            "of filling the gap."
        )
    ]


@mcp.prompt(
    title="Work an incident",
    description="Pull the runbook for a symptom and walk through it.",
)
def incident_playbook(symptom: str) -> list[Message]:
    """Open an incident conversation with the matching runbook attached."""
    return [
        UserMessage(
            f"I am on call and seeing: {symptom}\n"
            "\n"
            "Handbook sections that match:\n"
            f"{_cite(symptom)}\n"
            "\n"
            "Read the closest runbook with get_doc and walk me through it in "
            "order: what to check, what to run, and when to escalate. Quote the "
            "exact commands from the handbook rather than inventing them, and "
            "flag any step the runbook marks as lossy before I run it."
        )
    ]


@mcp.prompt(
    title="Explain a handbook topic",
    description="Summarize one handbook document for someone new to it.",
)
def explain_topic(slug: str = "architecture") -> list[Message]:
    """Open a conversation about one named document."""
    document = index.document(slug)
    if document is None:
        known = ", ".join(sorted(index.documents)) or "none"
        return [
            UserMessage(
                f"I asked about a handbook document called {slug!r}, which does "
                f"not exist. The handbook contains: {known}. Call list_topics to "
                "confirm, then ask me which one I meant."
            )
        ]

    headings = "\n".join(f"- {s.heading}" for s in document.sections)
    return [
        UserMessage(
            f"Explain the handbook document `{slug}` ({document.title}) to "
            "somebody who has never read it.\n"
            "\n"
            "Its sections are:\n"
            f"{headings}\n"
            "\n"
            f'Read it with `get_doc(slug="{slug}")`, then give me the shape of '
            "it in a few paragraphs: what it covers, which parts I need on day "
            "one, and which parts I can leave until I need them."
        )
    ]


@mcp.completion()
async def complete(ref, argument, context):
    """Offer real document slugs while a user fills in `explain_topic`.

    The SDK does not filter for you. Whatever this returns is what the user
    sees, so the prefix match is written by hand.
    """
    if isinstance(ref, PromptReference) and argument.name == "slug":
        prefix = (argument.value or "").lower()
        return Completion(
            values=[s for s in sorted(index.documents) if s.startswith(prefix)]
        )
    return None
