"""The server instance, the logger, and the one loaded copy of the corpus.

Everything else in this package imports from here and hangs a tool, a resource,
or a prompt off `mcp`. Keeping the instance in its own module is what lets the
registrations live in separate files without a circular import.

## Instructions are the only thing every client reads

`instructions` is the closest thing MCP has to a README aimed at the model. Some
hosts show it to the user, some feed it to the model, some ignore it. What none
of them do is guess: if the ordering of your tools matters, say so here, because
the tool descriptions alone cannot express "start with this one".

Note what the text below does *not* claim. It does not mention resources or
prompts, because a client that supports neither must still be able to read these
instructions and use this server correctly. That is the whole degradation
strategy in one paragraph, and `tools.py` explains the rest.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.mcpserver import MCPServer

from .index import KnowledgeIndex, load_corpus

# Under the stdio transport, stdout IS the protocol channel. Anything printed
# there is parsed as a JSON-RPC frame, and a stray print() breaks the connection
# in a way that is genuinely hard to diagnose from the host's error message.
# Logging goes to stderr, which every host in `clients/` collects for you.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

log = logging.getLogger("knowledge-base")

# Read and index the corpus once, at import time. The server is read-only and
# the corpus is a few tens of kilobytes, so the whole thing lives in memory and
# every request is answered without touching the disk.
documents = load_corpus()
index = KnowledgeIndex(documents)

log.info(
    "indexed %d document(s), %d section(s), %d distinct term(s)",
    len(documents),
    index.section_count,
    index.term_count,
)

if not documents:
    # Worth saying loudly. A server that starts cleanly and answers every search
    # with "no results" looks like a ranking bug and is actually a missing
    # directory, and that mistake costs an afternoon.
    log.warning(
        "no documents found; searches will return nothing. "
        "Set KNOWLEDGE_DIR, or run from a checkout that has a knowledge/ directory."
    )

mcp = MCPServer(
    "knowledge-base",
    title="Team Knowledge Base",
    instructions=(
        "A searchable copy of one team's engineering handbook: onboarding, "
        "runbooks, architecture notes, frequently asked questions, and code "
        "snippets.\n"
        "\n"
        "Three tools, and they are meant to be used in this order:\n"
        "1. search_docs(query) ranks handbook sections against a query and "
        "returns short excerpts. Start here for anything open-ended.\n"
        "2. get_doc(slug, section) returns the full text of a document, or of "
        "one section of it. Use it after search_docs to read a promising hit in "
        "full; the slug and section come straight from the search result.\n"
        "3. list_topics() names every document. Use it when you need to know "
        "what the handbook covers before you can form a query.\n"
        "\n"
        "The handbook is the only source of truth here. If a search returns "
        "nothing, say the handbook does not cover it rather than answering from "
        "general knowledge."
    ),
)
