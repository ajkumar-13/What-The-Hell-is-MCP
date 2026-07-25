"""Resources, prompts, and completions.

Post 07. The distinction this file exists to demonstrate:

- A **tool** is called by the model, when the model decides it needs something.
- A **resource** is read by the application, usually because a user attached it.
- A **prompt** is chosen by the user, and expands into a pre-filled message.

The same underlying data can legitimately appear as more than one of these.
`system://processes/top` below is a resource because a user might want to pin
it into the conversation as context, not because the model asked for it.
"""

from __future__ import annotations

import pathlib

import psutil
from mcp_types import Completion, PromptReference, ResourceTemplateReference
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from mcp.server.mcpserver.prompts.base import Message, UserMessage

from .app import mcp

_GB = 1024**3

# The disks we are willing to describe through the templated resource below.
# An allowlist, rather than accepting any path the caller sends, is the whole
# defense here: a resource template variable is untrusted input.
_ALLOWED_DISKS = {
    "root": pathlib.Path.home().anchor or "/",
}


@mcp.resource(
    "system://processes/top",
    title="Top processes by memory",
    mime_type="text/markdown",
)
def top_processes() -> str:
    """The ten processes currently using the most memory, as a Markdown table."""
    rows = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            mem = info["memory_info"]
            rows.append((info["pid"], info["name"] or "", mem.rss if mem else 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    rows.sort(key=lambda r: r[2], reverse=True)

    lines = ["| PID | Name | Memory (MB) |", "| --- | --- | --- |"]
    for pid, name, rss in rows[:10]:
        # Escape pipes so a process with a pipe in its name cannot break out of
        # the table and forge extra rows. Untrusted text going into a formatted
        # document is exactly where injection starts.
        safe_name = name.replace("|", "\\|")
        lines.append(f"| {pid} | {safe_name} | {rss / (1024 * 1024):.1f} |")

    return "\n".join(lines)


@mcp.resource(
    "system://disk/{disk}",
    title="Disk usage",
    mime_type="text/plain",
)
def disk_usage(disk: str) -> str:
    """Usage for one named disk. `disk` is a key from the server's allowlist."""
    path = _ALLOWED_DISKS.get(disk)
    if path is None:
        # The SDK turns this into -32602 Invalid params for the client.
        known = ", ".join(sorted(_ALLOWED_DISKS))
        raise ResourceNotFoundError(f"Unknown disk {disk!r}. Known disks: {known}.")

    usage = psutil.disk_usage(path)
    return (
        f"disk: {disk}\n"
        f"path: {path}\n"
        f"used: {usage.used // _GB} GB\n"
        f"total: {usage.total // _GB} GB\n"
        f"percent: {usage.percent:.1f}\n"
    )


@mcp.prompt(
    title="Diagnose slow machine",
    description="Start an investigation into why this machine feels slow.",
)
def diagnose_performance(symptom: str = "general slowness") -> list[Message]:
    """Build an opening message with a live snapshot already attached."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()

    return [
        UserMessage(
            f"This machine is showing {symptom}.\n\n"
            f"Current readings: CPU {cpu:.1f} percent, "
            f"memory {mem.percent:.1f} percent used.\n\n"
            "Investigate the likely cause. Use the process tools if you need "
            "to see what is running, and tell me what you would check next."
        )
    ]


@mcp.completion()
async def complete(ref, argument, context):
    """Suggest values while a user is filling in a prompt or a resource template.

    The SDK does not filter for you. Whatever this returns is what the user
    sees, so the prefix match has to be written by hand.
    """
    if isinstance(ref, ResourceTemplateReference) and argument.name == "disk":
        prefix = (argument.value or "").lower()
        return Completion(
            values=[d for d in sorted(_ALLOWED_DISKS) if d.startswith(prefix)]
        )

    if isinstance(ref, PromptReference) and argument.name == "symptom":
        prefix = (argument.value or "").lower()
        suggestions = [
            "general slowness",
            "high fan noise",
            "slow disk access",
            "unresponsive windows",
        ]
        return Completion(values=[s for s in suggestions if s.startswith(prefix)])

    return None
