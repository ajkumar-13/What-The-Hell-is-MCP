"""A team knowledge base served over MCP, for post 23.

One server, six client configurations, no branching on which client is
connected. Importing this package registers everything on the shared server
instance. Import order matters only in that `app` must come first; the rest hang
their registrations off the instance it creates.

The three modules are stacked by how widely they are supported, most-supported
first, and that ordering is the design:

- `tools`     - every MCP client supports tools. Nothing else is assumed.
- `resources` - additive. A client with no resource support loses presentation.
- `prompts`   - additive. A client with no prompt support loses shortcuts.

`tools.py` carries the table mapping each resource and prompt back to the tool
that covers it, and a test asserts the mapping holds.
"""

from .app import documents, index, mcp  # noqa: F401  (re-exported)

# Importing for the decorator side effects is the whole point here.
from . import tools  # noqa: F401,E402
from . import resources  # noqa: F401,E402
from . import prompts  # noqa: F401,E402

__all__ = ["mcp", "index", "documents"]
