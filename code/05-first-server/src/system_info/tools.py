"""Tools.

Posts 05 and 06. Post 05 introduces the decorator and the fact that type hints
become the input schema. Post 06 uses these same tools to show output schemas,
structured content, and annotations.

Every tool here is read-only, and says so with an annotation. Annotations are
hints for the host, not enforcement: nothing stops a badly written tool from
lying about `read_only_hint`. The enforcement is that these functions genuinely
do not write anything.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import psutil
from mcp_types import ToolAnnotations

from .app import mcp

_GB = 1024**3

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


@dataclass
class SystemSnapshot:
    """A point-in-time reading of this machine.

    The class-body annotations below are load-bearing. A class that only sets
    attributes inside __init__ has no type hints for the SDK to read, and you
    get `outputSchema: null` with no warning at all. Post 06 covers this trap.
    """

    cpu_percent: float
    memory_percent: float
    memory_used_gb: int
    memory_total_gb: int
    disk_percent: float
    disk_used_gb: int
    disk_total_gb: int


@dataclass
class ProcessInfo:
    """One matching process."""

    pid: int
    name: str
    memory_mb: float


@dataclass
class ProcessMatches:
    """The result of a process search."""

    query: str
    total_matches: int
    returned: int
    processes: list[ProcessInfo]


@mcp.tool(
    title="Get system snapshot",
    annotations=READ_ONLY,
)
def get_system_info() -> SystemSnapshot:
    """Read current CPU, memory, and disk usage for this machine.

    Returns a single snapshot taken over a short sampling interval.
    """
    # A zero interval would compare against the previous call and return a
    # meaningless number on the first invocation.
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()

    # Path.home().anchor gives "C:\\" on Windows and "/" on Unix.
    disk = psutil.disk_usage(pathlib.Path.home().anchor or "/")

    return SystemSnapshot(
        cpu_percent=round(cpu, 1),
        memory_percent=round(mem.percent, 1),
        memory_used_gb=mem.used // _GB,
        memory_total_gb=mem.total // _GB,
        disk_percent=round(disk.percent, 1),
        disk_used_gb=disk.used // _GB,
        disk_total_gb=disk.total // _GB,
    )


@mcp.tool(
    title="Find processes by name",
    annotations=READ_ONLY,
)
def find_process(name: str, limit: int = 25) -> ProcessMatches:
    """Find running processes whose name contains `name`, case-insensitively.

    Args:
        name: Substring to match against the process name.
        limit: Maximum number of processes to return, between 1 and 50.
    """
    # Clamp rather than reject. A model that asks for 5000 results is not being
    # malicious, it just does not know the limit; silently doing the sensible
    # thing beats an error the model has to recover from.
    limit = max(1, min(limit, 50))
    needle = (name or "").strip().lower()

    matches: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            proc_name = proc.info["name"] or ""
            if needle and needle not in proc_name.lower():
                continue
            mem_info = proc.info["memory_info"]
            matches.append(
                ProcessInfo(
                    pid=proc.info["pid"],
                    name=proc_name,
                    memory_mb=round(mem_info.rss / (1024 * 1024), 1) if mem_info else 0.0,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Processes die between the iterator yielding them and us reading
            # them. On Windows, many system processes simply refuse inspection.
            continue

    matches.sort(key=lambda p: p.memory_mb, reverse=True)

    return ProcessMatches(
        query=name,
        total_matches=len(matches),
        returned=min(len(matches), limit),
        processes=matches[:limit],
    )
