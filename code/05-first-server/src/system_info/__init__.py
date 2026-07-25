"""A system-information MCP server, built across posts 05 to 09.

Importing this package registers everything on the shared server instance.
Import order matters only in that `app` must come first; the rest hang their
registrations off the instance it creates.
"""

from .app import mcp  # noqa: F401  (re-exported)

# Importing for the decorator side effects is the whole point here.
from . import tools  # noqa: F401,E402
from . import resources  # noqa: F401,E402
from . import interactive  # noqa: F401,E402
from . import progress  # noqa: F401,E402

__all__ = ["mcp"]
