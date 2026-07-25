"""Entry point.

    python -m mcp_host list                     # connect and show the catalog
    python -m mcp_host inspect system-info      # one server's full surface
    python -m mcp_host call get_system_info     # one tool, through the gate
    python -m mcp_host demo                     # the tool loop, no API key
    python -m mcp_host chat                     # the tool loop, interactive
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
