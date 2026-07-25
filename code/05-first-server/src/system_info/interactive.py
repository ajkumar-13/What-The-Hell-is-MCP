"""A tool that asks the user a question before it acts.

Post 08. This is the file that demonstrates the single biggest change in the
2026-07-28 revision.

There is no channel for a server to call back into a client any more. So a
server that needs an answer does not *ask* and wait; it *returns* an
`InputRequiredResult` saying "I need this before I can continue", and the
client calls the same tool again with the answer attached.

The SDK hides that inversion behind `Resolve(...)`. You declare a parameter
that the model never sees, backed by a resolver function that returns an
`Elicit(...)`. The SDK handles the return-and-retry, and your tool body reads
the parameter as if it had been there all along.
"""

from __future__ import annotations

import os
import signal
from typing import Annotated

import psutil
from pydantic import BaseModel, Field
from mcp_types import ToolAnnotations
from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    Resolve,
)

from .app import mcp, log


class TerminateConfirmation(BaseModel):
    """What we ask the human before ending a process."""

    confirm: bool = Field(
        description="Confirm that this process should be terminated.",
    )
    reason: str = Field(
        default="",
        description="Optional note recorded in the server log.",
    )


def _confirm_terminate(pid: int) -> Elicit[TerminateConfirmation]:
    """Build the confirmation question for a specific pid.

    The question is derived *only* from the tool's arguments. That is not a
    style preference. The SDK matches a recorded answer against a digest of the
    exact rendered question, so if this string changed between rounds, for
    example because it embedded a timestamp or a live memory reading, the
    recorded answer would never match and the call would loop forever.
    """
    try:
        name = psutil.Process(pid).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        name = "unknown"

    return Elicit(
        f"Terminate process {pid} ({name})? This cannot be undone.",
        TerminateConfirmation,
    )


@mcp.tool(
    title="Terminate a process",
    annotations=ToolAnnotations(
        title="Terminate a process",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def terminate_process(
    pid: int,
    approval: Annotated[
        ElicitationResult[TerminateConfirmation],
        Resolve(_confirm_terminate),
    ],
) -> str:
    """Terminate a running process, after the user confirms.

    Args:
        pid: The process id to terminate.
    """
    # `approval` is invisible to the model. The published input schema for this
    # tool contains `pid` and nothing else, so the model cannot fabricate an
    # approval by passing one as an argument. That is the point.
    #
    # Annotating as ElicitationResult[...] rather than the bare model means we
    # get the full three-way outcome to branch on. Annotating it as the bare
    # TerminateConfirmation would instead raise a ToolError on decline, which is
    # fine when there is nothing useful to say about a refusal.
    if isinstance(approval, DeclinedElicitation):
        return f"Not terminated. The user declined to confirm ending process {pid}."
    if isinstance(approval, CancelledElicitation):
        return f"Not terminated. The confirmation was dismissed for process {pid}."

    assert isinstance(approval, AcceptedElicitation)
    if not approval.data.confirm:
        return f"Not terminated. The user answered no for process {pid}."

    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except psutil.NoSuchProcess:
        return f"No process with pid {pid} is running."
    except psutil.AccessDenied:
        return f"Access denied reading process {pid}."

    if pid == os.getpid():
        return "Refusing to terminate the MCP server's own process."

    try:
        proc.send_signal(signal.SIGTERM)
    except psutil.AccessDenied:
        return f"Access denied terminating process {pid} ({name})."
    except psutil.NoSuchProcess:
        return f"Process {pid} exited before it could be terminated."

    note = approval.data.reason.strip()
    log.info("terminated pid=%s name=%s reason=%s", pid, name, note or "(none)")
    return f"Sent SIGTERM to process {pid} ({name})." + (f" Reason: {note}" if note else "")
