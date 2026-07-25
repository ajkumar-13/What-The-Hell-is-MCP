"""A Kubernetes first-responder MCP server, built across posts 15 and 16.

Importing this package registers every tool on the shared server instance. The
import order below is the architecture written down:

- `app` first, because everything else hangs a registration off the instance
  it creates.
- `inspect` and `diagnose` are post 15. They read the cluster. Neither module
  imports anything capable of changing it.
- `remediate` is post 16 and is the only module that writes. Removing the line
  that imports it produces a genuinely read-only server, not a politely
  read-only one.
"""

from .app import mcp  # noqa: F401  (re-exported)

# Importing for the decorator side effects is the whole point here.
from . import inspect  # noqa: F401,E402
from . import diagnose  # noqa: F401,E402
from . import remediate  # noqa: F401,E402

__all__ = ["mcp"]
