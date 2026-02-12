from mcp.server.fastmcp import FastMCP
import psutil
import pathlib
import logging
import sys

# Safe logging setup (NEVER use print() - it corrupts STDIO transport)
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("system-info")

mcp = FastMCP("system-info")


# ============================================================================
# TOOLS - Actions the AI can perform
# ============================================================================

@mcp.tool()
def get_system_info() -> str:
    """Get current CPU, memory, and disk usage of this computer."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    # Cross-platform disk root: C:\\ on Windows, / on Unix
    disk_root = pathlib.Path.home().anchor or "/"
    disk = psutil.disk_usage(disk_root)
    
    gb = 1024**3
    return (
        "System Information:\n"
        f"- CPU Usage: {cpu:.1f}%\n"
        f"- Memory: {mem.percent:.1f}% used ({mem.used // gb}GB / {mem.total // gb}GB)\n"
        f"- Disk: {disk.percent:.1f}% used ({disk.used // gb}GB / {disk.total // gb}GB)"
    )


@mcp.tool()
def find_process(name: str, limit: int = 25) -> str:
    """Find running processes by name (case-insensitive)."""
    limit = max(1, min(limit, 50))  # Cap limit to prevent context flooding
    name = (name or "").strip().lower()
    if not name:
        return "Please provide a non-empty process name (e.g., 'chrome', 'python')."
    
    found = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_percent']):
        try:
            pname = (proc.info.get('name') or '').lower()
            if name in pname:
                found.append(
                    f"PID {proc.info['pid']}: {proc.info.get('name', '?')} "
                    f"(Memory: {(proc.info.get('memory_percent') or 0):.1f}%)"
                )
                if len(found) >= limit:
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    if not found:
        return f"No processes found matching '{name}'."
    return "Found processes:\n" + "\n".join(found)


# ============================================================================
# RESOURCES - Read-only data sources
# ============================================================================

@mcp.resource("system://top-processes")
def top_processes() -> str:
    """Top 10 memory-consuming processes on this machine."""
    procs = []
    for p in psutil.process_iter(['name', 'memory_percent']):
        try:
            procs.append((p.info.get('name') or '?', p.info.get('memory_percent') or 0.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    
    procs.sort(key=lambda x: x[1], reverse=True)
    lines = [f"- {name}: {mem:.1f}%" for name, mem in procs[:10]]
    return "Top 10 by Memory:\n" + "\n".join(lines)


# ============================================================================
# PROMPTS - Pre-built conversation starters
# ============================================================================

@mcp.prompt(title="Diagnose Performance")
def diagnose_performance(symptom: str = "general slowness") -> str:
    """Prompt that includes live CPU/memory snapshot."""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    return (
        f"I'm experiencing: {symptom}\n\n"
        "Current System State:\n"
        f"- CPU: {cpu:.1f}%\n"
        f"- Memory: {mem.percent:.1f}%\n\n"
        "Please analyze this and suggest fixes."
    )


# ============================================================================
# MAIN - Entry point
# ============================================================================

if __name__ == "__main__":
    mcp.run()
