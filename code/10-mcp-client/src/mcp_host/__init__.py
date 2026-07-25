"""An MCP client and host, built across posts 10 and 11.

Post 10 is the client: connecting over a transport, discovering what a server
offers, calling a tool and reading the result *properly*, and answering a
server that asks a question mid-call.

Post 11 is the host: several servers behind one catalog, a permission gate,
and the tool-execution loop that a model drives.

The split by module:

    connection.py    transports and lifetimes            (post 10)
    results.py       every content block, and isError     (post 10)
    interactive.py   answering an input_required request  (post 10)
    catalog.py       many servers, one namespace          (post 11)
    permissions.py   the gate                             (post 11)
    providers.py     the LLMProvider protocol             (post 11)
    loop.py          the tool-execution loop              (post 11)
"""

from __future__ import annotations

from .catalog import CatalogEntry, ServerPool, ToolCatalog, UnknownTool, open_pool
from .connection import ServerConnection, ServerSpec, load_config, open_connection
from .interactive import (
    ConsoleResponder,
    ScriptedResponder,
    as_elicitation_callback,
    call_with_input,
)
from .loop import ToolLoop, Turn
from .permissions import (
    Decision,
    PermissionGate,
    PermissionPolicy,
    PermissionRequest,
    Verdict,
    always_allow,
    always_deny,
    console_prompter,
    request_for,
    scripted_prompter,
)
from .providers import (
    AnthropicProvider,
    LLMProvider,
    LLMReply,
    ProviderUnavailable,
    ScriptedProvider,
    ToolCall,
    ToolResult,
    get_provider,
)
from .results import Block, ToolOutcome, describe_block, read_result

__all__ = [
    "AnthropicProvider",
    "Block",
    "CatalogEntry",
    "ConsoleResponder",
    "Decision",
    "LLMProvider",
    "LLMReply",
    "PermissionGate",
    "PermissionPolicy",
    "PermissionRequest",
    "ProviderUnavailable",
    "ScriptedProvider",
    "ScriptedResponder",
    "ServerConnection",
    "ServerPool",
    "ServerSpec",
    "ToolCall",
    "ToolCatalog",
    "ToolLoop",
    "ToolOutcome",
    "ToolResult",
    "Turn",
    "UnknownTool",
    "Verdict",
    "always_allow",
    "always_deny",
    "as_elicitation_callback",
    "call_with_input",
    "console_prompter",
    "describe_block",
    "get_provider",
    "load_config",
    "open_connection",
    "open_pool",
    "read_result",
    "request_for",
    "scripted_prompter",
]
