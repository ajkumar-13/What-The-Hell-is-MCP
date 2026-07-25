"""Work that takes a while, and how to report on it.

Post 09. A tool that samples CPU over several seconds is the smallest honest
example of slow work: it cannot be made fast, and the caller deserves to know
it is still alive.

Progress reporting is the part of this that works everywhere today. The tasks
extension, which turns long work into a pollable object with its own lifecycle,
is negotiated per request and is not exposed through the high-level server API
in this SDK build; post 09 covers what that means in practice.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import psutil
from mcp_types import ToolAnnotations
from mcp.server.mcpserver import Context

from .app import mcp


@dataclass
class CpuSample:
    """One reading in a series."""

    second: int
    percent: float


@dataclass
class CpuWatch:
    """The result of watching CPU for a while."""

    seconds: int
    average_percent: float
    peak_percent: float
    samples: list[CpuSample]


@mcp.tool(
    title="Watch CPU over time",
    annotations=ToolAnnotations(
        title="Watch CPU over time",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=False,  # two runs sample different moments
        open_world_hint=False,
    ),
)
async def watch_cpu(seconds: int = 5, ctx: Context = None) -> CpuWatch:  # type: ignore[assignment]
    """Sample CPU usage once a second and report the series.

    Args:
        seconds: How long to watch, between 1 and 30.
    """
    seconds = max(1, min(seconds, 30))

    samples: list[CpuSample] = []
    for i in range(seconds):
        # interval=None returns usage since the previous call, which is what we
        # want for a series. asyncio.sleep, not time.sleep: blocking the event
        # loop would stall the transport reader alongside it.
        percent = psutil.cpu_percent(interval=None)
        await asyncio.sleep(1)
        percent = psutil.cpu_percent(interval=None)
        samples.append(CpuSample(second=i + 1, percent=round(percent, 1)))

        if ctx is not None:
            await ctx.report_progress(
                progress=i + 1,
                total=seconds,
                message=f"sampled {i + 1} of {seconds} seconds",
            )

    values = [s.percent for s in samples]
    return CpuWatch(
        seconds=seconds,
        average_percent=round(sum(values) / len(values), 1),
        peak_percent=max(values),
        samples=samples,
    )
