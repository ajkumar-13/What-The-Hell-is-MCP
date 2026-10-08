"""Post 09, section 10: what the Python SDK ships for tasks, read from itself.

Run from the series root:
    uv run python posts/09-tasks/snippets/sdk_surface.py

Prints, from the installed mcp and mcp-types packages: the parameters of
MCPServer.tool, the task types mcp_types carries and the revision their own
docstrings assign them to, the fields of ClientCapabilities, the code of the
missing-capability error, how often the per-tool `taskSupport` field
appears in the wire models the SDK generates from each revision's schema, and
the docstrings of the extension seams that mention tasks. Nothing is started;
this only imports.
"""

from __future__ import annotations

import importlib
import inspect
from importlib.metadata import version

import mcp_types
from mcp.client.extension import advertise
from mcp.server.extension import MethodBinding
from mcp.server.mcpserver import MCPServer
from mcp_types.jsonrpc import MISSING_REQUIRED_CLIENT_CAPABILITY


def first_line(obj: object) -> str:
    return inspect.getdoc(obj).splitlines()[0]


print(f"mcp {version('mcp')}, mcp-types {version('mcp-types')}")

# The SDK generates one module of wire models per revision from that
# revision's schema; count the per-tool task field in each.
for revision in ("2025_11_25", "2026_07_28"):
    module = importlib.import_module(f"mcp_types._v{revision}")
    print(f"'taskSupport' in the {revision.replace('_', '-')} wire models:",
          inspect.getsource(module).count("taskSupport"))
print("MCPServer.tool parameters:", list(inspect.signature(MCPServer.tool).parameters))

print("\nTask types in mcp_types:")
for name in ("Task", "CreateTaskResult", "ToolExecution", "GetTaskRequest",
             "CancelTaskRequest", "GetTaskPayloadRequest", "ListTasksRequest",
             "UpdateTaskRequest"):
    cls = getattr(mcp_types, name, None)
    print(f"  {name:22} {first_line(cls) if cls else 'absent'}")
print("Task fields:", list(mcp_types.Task.model_fields))
print("CreateTaskResult fields:", list(mcp_types.CreateTaskResult.model_fields))
print("ClientCapabilities fields:", list(mcp_types.ClientCapabilities.model_fields))

print("\nMISSING_REQUIRED_CLIENT_CAPABILITY =", MISSING_REQUIRED_CLIENT_CAPABILITY)
print("mcp.server.extension:", first_line(inspect.getmodule(MethodBinding)))
print("MethodBinding:", first_line(MethodBinding))
name_param = inspect.getsource(mcp_types.Request).split("name_param", 1)[1]
print("Request.name_param:", name_param.split('"""')[1].strip().splitlines()[0])
print("advertise:", " ".join(inspect.getdoc(advertise).split("\n\n")[1].split()))
