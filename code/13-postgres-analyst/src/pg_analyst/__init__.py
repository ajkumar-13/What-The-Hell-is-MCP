"""A PostgreSQL analyst MCP server, built across posts 13 and 14.

Importing this package registers everything on the shared server instance.
Import order matters only in that `app` must come first; the rest hang their
registrations off the instance it creates.

    post 13, read path   ->  security.py, schema.py, query.py
    post 14, write path  ->  audit.py, writes.py
"""

from .app import mcp  # noqa: F401  (re-exported)

# Importing for the decorator side effects is the whole point here.
from . import schema  # noqa: F401,E402
from . import query  # noqa: F401,E402
from . import audit  # noqa: F401,E402
from . import writes  # noqa: F401,E402

__all__ = ["mcp"]
