# Blog 6: The Secure Database Analyst (Part 2)

## Adding Write Operations with Human-in-the-Loop Approval

This blog extends the read-only database analyst from Blog 5 with:

- **Write Operations** (INSERT, UPDATE) with human approval
- **Transaction Support** for atomic operations
- **Audit Logging** for compliance
- **Layered Security Model** with defense in depth

## Prerequisites

- Completed Blog 5 (mcp-db-analyst project)
- PostgreSQL database access
- Python 3.10+

## Key Files Modified

| File | Changes |
|------|---------|
| `security.py` | Added write validation mode, tiered permissions |
| `database.py` | Added transaction context manager |
| `audit.py` | New file for operation logging |
| `server.py` | Added `write_database` tool with annotations |

## New Concepts

- **Tool Annotations** - Mark tools as destructive for host approval dialogs
- **Dry Run Pattern** - Preview changes before execution
- **Defense in Depth** - Multiple security layers
- **Audit Tables** - Compliance and debugging trail

## Testing

1. Try a dry run: "Update user 5 to admin role, dry run first"
2. Execute with approval: "Run it for real"
3. Attempt DELETE (blocked): "Delete inactive users"
4. Check audit: "What changes happened recently?"
