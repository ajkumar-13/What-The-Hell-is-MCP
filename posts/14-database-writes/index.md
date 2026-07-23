# Blog 6: The Secure Database Analyst (Part 2)
## Adding Write Operations with Human-in-the-Loop Approval


> *"Reading is safe. But what if the CEO actually wants to update a customer's subscription tier? Or fix a typo in someone's name? Read-only isn't enough."*

In Blog 5, we built a **read-only firewall** for database access. That was the foundation. Now we're adding write operations, but with a critical constraint: **no mutation happens without explicit human approval.**

This is the **human-in-the-loop pattern**, and it's how you safely give AI real power.

---

## What We're Adding

| Feature | Why It Matters |
|---------|----------------|
| **Write Operations** | INSERT, UPDATE for real data changes |
| **Human Approval** | User must confirm before any mutation |
| **Transactions** | All-or-nothing execution with rollback |
| **Audit Logging** | Every write recorded for compliance |

We're extending our existing `mcp-db-analyst` project, not starting from scratch.

---

## 1A. Credential Strategy for Writes

In Blog 5, we recommended a **read-only database role**. Now we need write access, but we should be careful:

### Update Your `.env`

```text
# Read-only connection (used for query_database tool)
DATABASE_URL=postgresql://mcp_readonly:readonly_pass@localhost:5432/mydb

# Write-enabled connection (used for write_database tool)
DATABASE_WRITE_URL=postgresql://mcp_writer:writer_pass@localhost:5432/mydb

# Explicit opt-in for writes (production-safe by default)
ENABLE_WRITES=true
```

### Create a Limited Write Role

```sql
-- The write role can INSERT/UPDATE but NOT DELETE/DROP
CREATE ROLE mcp_writer WITH LOGIN PASSWORD 'writer_pass';
GRANT USAGE ON SCHEMA public TO mcp_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO mcp_writer;
-- Note: No DELETE, no DROP, no ALTER
```

> **Production Default:** The `ENABLE_WRITES` flag defaults to `false`. Writes are disabled unless explicitly enabled. This ensures a misconfigured deployment stays safe.

---

## 1. The Human-in-the-Loop Pattern

### The Problem with Auto-Execution

If we let the LLM run `UPDATE users SET role = 'admin' WHERE id = 5` automatically, we have a serious problem:

- **Prompt injection** could trick the model into running malicious updates
- **Hallucination** could produce wrong WHERE clauses (updating wrong rows)
- **Misunderstanding** could lead to unintended bulk changes

### The Solution

Some MCP hosts (like Claude Desktop) can display approval dialogs to users. When available, the flow becomes:

```
┌─────────────────────────────────────────────────────────────┐
│              HUMAN-IN-THE-LOOP FLOW (When Supported)        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. User: "Update John's email to john@new.com"             │
│                                                             │
│  2. LLM generates: UPDATE users SET email='john@new.com'    │
│                    WHERE id = 42                            │
│                                                             │
│  3. Tool marked as "destructive" → Host shows dialog:       │
│     ┌─────────────────────────────────────────────────┐     │
│     │  Database Write Requested                       │     │
│     │                                                 │     │
│     │  UPDATE users SET email='john@new.com'          │     │
│     │  WHERE id = 42                                  │     │
│     │                                                 │     │
│     │  [Cancel]                    [Allow]            │     │
│     └─────────────────────────────────────────────────┘     │
│                                                             │
│  4. User clicks "Allow" → Query executes                    │
│                                                             │
│  5. Audit log records: who, what, when                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Key insight:** The AI proposes, the human disposes. The model never has autonomous write access, **but only if your security layer enforces this regardless of host UI behavior**.

> **Critical:** Never rely solely on the host showing an approval dialog. Your security.py validation and database-level permissions are the real enforcement. The host UI is a usability feature, not a security boundary.

---

## 2. Extending the Security Layer

We need a second validation mode: one that allows writes but only specific, safe patterns.

Update `src/security.py`:

```python
# src/security.py - Extended SQL Validation Firewall
import logging
import sys
import sqlparse
from sqlparse.tokens import Keyword

# Configure logging to stderr (STDIO transport requires stdout to be clean)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """Raised when a query violates security rules."""
    pass


# ============ ALLOWLISTS ============
READONLY_OPERATIONS = {"SELECT"}

WRITE_OPERATIONS = {"INSERT", "UPDATE"}

ALWAYS_FORBIDDEN = {
    "DROP", "TRUNCATE", "ALTER", "GRANT", "REVOKE", 
    "CREATE", "EXEC", "EXECUTE", "DELETE",
    "COPY",  # Can read/write files
    "INTO",  # SELECT INTO creates tables
}


def validate_query(sql: str, allow_writes: bool = False) -> dict:
    """
    Analyzes SQL to ensure it meets security requirements.
    
    Args:
        sql: The SQL statement to validate
        allow_writes: If True, allows INSERT/UPDATE (still blocks DELETE/DROP)
    
    Returns:
        dict with 'operation' and 'valid' keys
        
    Raises:
        SecurityError: If dangerous patterns are detected
    """
    parsed = sqlparse.parse(sql)
    
    if not parsed:
        raise SecurityError("Empty query")

    # Only allow single statements for writes (no chaining)
    if allow_writes and len([s for s in parsed if s.get_type()]) > 1:
        raise SecurityError(
            "Multiple statements not allowed for write operations. "
            "Submit one statement at a time."
        )

    operations_found = []
    
    for statement in parsed:
        if not statement.tokens:
            continue
            
        stmt_type = statement.get_type()
        
        if stmt_type is None:
            raise SecurityError("Could not determine query type")
        
        stmt_type = stmt_type.upper()
        operations_found.append(stmt_type)
        
        # Check against forbidden list first (always blocked)
        if stmt_type in ALWAYS_FORBIDDEN:
            raise SecurityError(
                f"Operation '{stmt_type}' is permanently forbidden."
            )
        
        # Check if operation is allowed given current mode
        allowed = READONLY_OPERATIONS if not allow_writes else (READONLY_OPERATIONS | WRITE_OPERATIONS)
        
        if stmt_type not in allowed:
            if stmt_type in WRITE_OPERATIONS:
                raise SecurityError(
                    f"Operation '{stmt_type}' requires write mode. "
                    "Use the write_database tool instead."
                )
            else:
                raise SecurityError(
                    f"Operation '{stmt_type}' is not allowed."
                )
        
        # Block SELECT INTO (creates tables, bypasses read-only)
        if stmt_type == "SELECT":
            sql_upper = sql.upper()
            if " INTO " in sql_upper:
                raise SecurityError(
                    "SELECT INTO is forbidden. "
                    "It creates new tables, violating read-only mode."
                )

        # Deep token scan for hidden dangers
        for token in statement.flatten():
            if token.ttype in (Keyword.DML, Keyword.DDL, Keyword):
                if token.value.upper() in ALWAYS_FORBIDDEN:
                    raise SecurityError(
                        f"Forbidden keyword detected: {token.value.upper()}"
                    )

    return {
        "valid": True,
        "operation": operations_found[0] if operations_found else None
    }


def validate_readonly(sql: str) -> bool:
    """Convenience wrapper for read-only validation."""
    result = validate_query(sql, allow_writes=False)
    return result["valid"]


def validate_write(sql: str) -> dict:
    """Validate a write operation. Returns operation type."""
    result = validate_query(sql, allow_writes=True)
    
    sql_upper = sql.upper()
    
    # Block INSERT...SELECT (often unintended bulk writes)
    if result["operation"] == "INSERT":
        if "VALUES" not in sql_upper:
            raise SecurityError(
                "Only INSERT ... VALUES is allowed. "
                "INSERT ... SELECT is forbidden to prevent unintended bulk writes."
            )
    
    # Additional safety: UPDATE must have WHERE clause
    if result["operation"] == "UPDATE":
        if "WHERE" not in sql_upper:
            raise SecurityError(
                "UPDATE without WHERE clause is forbidden. "
                "Please specify which rows to update."
            )
    
    return result
```

### What Changed?

| Before (Blog 5) | After (Blog 6) |
|-----------------|----------------|
| Binary: SELECT allowed, everything else blocked | Tiered: SELECT always, INSERT/UPDATE with flag, DELETE/DROP never |
| No operation type returned | Returns what operation was detected |
| Multi-statement allowed | Single statement enforced for writes |
| No WHERE enforcement | UPDATE requires WHERE clause |
| SELECT INTO not blocked | SELECT INTO explicitly blocked (creates tables) |

**Why no DELETE?** Delete is too dangerous for LLM-assisted workflows. If you need to remove data, do it directly in your database admin tool. This is an intentional design constraint.

---

## 3. The Audit Logger

Every write operation must be logged. This is essential for:

- **Debugging:** What changed and when?
- **Compliance:** Who approved what?
- **Rollback planning:** What needs to be undone?

Create `src/audit.py`:

```python
# src/audit.py - Audit Logging for Database Operations
import json
import logging
import os
import sys
from datetime import datetime, timezone
from .database import db  # Relative import for package

# Configure logging to stderr (STDIO transport requires stdout to be clean)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def ensure_audit_table():
    """
    Creates the audit log table if it doesn't exist.
    Call this at server startup.
    """
    # Split into separate statements - asyncpg doesn't reliably
    # execute multiple statements in one call
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS mcp_audit_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        operation VARCHAR(20) NOT NULL,
        sql_query TEXT NOT NULL,
        affected_rows INTEGER,
        success BOOLEAN NOT NULL,
        error_message TEXT,
        session_id VARCHAR(100),
        host_name VARCHAR(100),
        metadata JSONB
    );
    """
    
    create_index_sql = """
    CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
    ON mcp_audit_log(timestamp DESC);
    """
    
    async with db.get_connection() as conn:
        await conn.execute(create_table_sql)
        await conn.execute(create_index_sql)
        logger.info("Audit table ready")


async def log_operation(
    operation: str,
    sql_query: str,
    affected_rows: int = None,
    success: bool = True,
    error_message: str = None,
    metadata: dict = None,
    conn = None  # Optional: pass connection for atomic transactions
) -> int:
    """
    Records a database operation to the audit log.
    
    Args:
        conn: Optional connection. If provided, uses it (for atomic commits).
              If None, acquires a new connection.
    
    Returns:
        The audit log entry ID
    """
    # Capture identity for compliance
    session_id = os.getenv("MCP_SESSION_ID", "unknown")
    host_name = os.getenv("MCP_HOST_NAME", "unknown")
    
    insert_sql = """
    INSERT INTO mcp_audit_log 
        (operation, sql_query, affected_rows, success, error_message, 
         session_id, host_name, metadata)
    VALUES 
        ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id;
    """
    
    params = (
        operation,
        sql_query,
        affected_rows,
        success,
        error_message,
        session_id,
        host_name,
        json.dumps(metadata) if metadata else None
    )
    
    # Use provided connection or acquire new one
    if conn is not None:
        row = await conn.fetchrow(insert_sql, *params)
        return row["id"]
    else:
        async with db.get_connection() as new_conn:
            row = await new_conn.fetchrow(insert_sql, *params)
            return row["id"]


async def get_recent_operations(limit: int = 20) -> list:
    """
    Retrieves recent audit log entries.
    Useful for the LLM to understand recent activity.
    """
    sql = """
    SELECT 
        id, 
        timestamp, 
        operation, 
        sql_query,
        affected_rows,
        success,
        error_message
    FROM mcp_audit_log
    ORDER BY timestamp DESC
    LIMIT $1;
    """
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql, limit)
        return [dict(row) for row in rows]
```

### The Audit Table Schema

```sql
┌─────────────────────────────────────────────────────────────┐
│                     mcp_audit_log                           │
├──────────────┬──────────────┬───────────────────────────────┤
│ Column       │ Type         │ Purpose                       │
├──────────────┼──────────────┼───────────────────────────────┤
│ id           │ SERIAL PK    │ Unique identifier             │
│ timestamp    │ TIMESTAMPTZ  │ When it happened (UTC)        │
│ operation    │ VARCHAR(20)  │ INSERT, UPDATE, SELECT, etc.  │
│ sql_query    │ TEXT         │ The actual SQL executed       │
│ affected_rows│ INTEGER      │ How many rows changed         │
│ success      │ BOOLEAN      │ Did it complete successfully? │
│ error_message│ TEXT         │ Error details if failed       │
│ session_id   │ VARCHAR(100) │ MCP session identifier        │
│ host_name    │ VARCHAR(100) │ Which host made the request   │
│ metadata     │ JSONB        │ Extra context (flexible)      │
└──────────────┴──────────────┴───────────────────────────────┘
```

> **Compliance Note:** For production, populate `MCP_SESSION_ID` and `MCP_HOST_NAME` environment variables from your host application to track who approved each operation.

---

## 4. Transaction-Wrapped Writes

Write operations must be atomic. If something fails mid-execution, we need to rollback completely.

Update `src/database.py` to add a transaction helper:

```python
# Add this to src/database.py

from contextlib import asynccontextmanager

# ... existing DatabaseManager class ...

@asynccontextmanager
async def transaction(self):
    """
    Provides a connection with an explicit transaction.
    Commits on success, rolls back on any exception.
    
    Usage:
        async with db.transaction() as conn:
            await conn.execute("INSERT ...")
            await conn.execute("UPDATE ...")
        # Auto-commits here if no exception
    """
    if not self.pool:
        await self.connect()
    
    async with self.pool.acquire() as conn:
        # Start explicit transaction
        async with conn.transaction():
            yield conn
        # Auto-commits when exiting the inner context
        # Rolls back if exception raised
```

The full updated `database.py`:

```python
# src/database.py - Connection Pool Manager with Transactions
import asyncpg
import logging
import os
import sys
from contextlib import asynccontextmanager

# Configure logging to stderr (STDIO transport requires stdout to be clean)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages separate read and write connection pools."""
    
    def __init__(self):
        self.read_pool = None
        self.write_pool = None

    async def connect(self):
        """Initialize connection pools."""
        if not self.read_pool:
            url = os.getenv("DATABASE_URL")
            if not url:
                raise ValueError("DATABASE_URL environment variable not set")
            
            self.read_pool = await asyncpg.create_pool(
                url,
                min_size=2,
                max_size=10,
                command_timeout=30
            )
            logger.info("Read pool initialized")
        
        if not self.write_pool:
            # Write pool uses separate credentials with limited permissions
            write_url = os.getenv("DATABASE_WRITE_URL", os.getenv("DATABASE_URL"))
            self.write_pool = await asyncpg.create_pool(
                write_url,
                min_size=1,         # Writes are less frequent
                max_size=5,         # Limit concurrent writes
                command_timeout=60  # Allow longer for write operations
            )
            logger.info("Write pool initialized")

    async def close(self):
        """Close connection pools gracefully."""
        if self.read_pool:
            await self.read_pool.close()
            logger.info("Read pool closed")
        if self.write_pool:
            await self.write_pool.close()
            logger.info("Write pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """Yield a connection from the READ pool for query operations."""
        if not self.read_pool:
            await self.connect()
        
        async with self.read_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        """
        Yield a connection with an explicit transaction from the WRITE pool.
        Auto-commits on success, rolls back on exception.
        """
        if not self.write_pool:
            await self.connect()
        
        async with self.write_pool.acquire() as conn:
            async with conn.transaction():
                yield conn


# Global instance
db = DatabaseManager()
```

---

## 5. The Write Tool with Approval

Now we add the write tool to `src/server.py`. The key is marking it as **destructive** so the host displays an approval dialog.

Update `src/server.py`:

```python
# src/server.py - Secure Database Analyst with Write Support
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP
from .database import db
from .security import validate_readonly, validate_write, SecurityError
from .schema import get_database_schema
from .audit import ensure_audit_table, log_operation, get_recent_operations

# Configure logging to stderr (STDIO transport requires stdout to be clean)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()


# ============ LIFECYCLE MANAGEMENT ============
@asynccontextmanager
async def lifespan(server: FastMCP):
    """
    Manage server lifecycle: startup and shutdown.
    
    The lifespan pattern ensures:
    - Database connects when server starts
    - Audit table is created/verified
    - Database closes cleanly when server stops
    - Resources are properly cleaned up even on errors
    """
    # Startup
    await db.connect()
    await ensure_audit_table()
    logger.info("Secure DB Analyst ready (read + write mode)")
    try:
        yield  # Server runs here
    finally:
        # Shutdown: Always close connections, even on error
        await db.close()


# Initialize the MCP Server with lifespan
mcp = FastMCP("Secure DB Analyst", lifespan=lifespan)


# ============ RESOURCE: Database Schema ============
@mcp.resource("postgres://schema")
async def get_schema() -> str:
    """
    Returns the current database schema structure.
    Use this to understand what tables and columns are available.
    """
    return await get_database_schema()


# ============ RESOURCE: Recent Operations ============
@mcp.resource("postgres://audit")
async def get_audit_log() -> str:
    """
    Returns the 20 most recent database operations from the audit log.
    Use this to understand what changes have been made recently.
    """
    operations = await get_recent_operations(20)
    
    if not operations:
        return "No operations recorded yet."
    
    output = "# Recent Database Operations\n\n"
    output += "| Time | Operation | Query | Rows | Status |\n"
    output += "|------|-----------|-------|------|--------|\n"
    
    for op in operations:
        time = op["timestamp"].strftime("%Y-%m-%d %H:%M")
        query_preview = op["sql_query"][:50] + "..." if len(op["sql_query"]) > 50 else op["sql_query"]
        status = "✅" if op["success"] else "❌"
        rows = op["affected_rows"] if op["affected_rows"] is not None else "-"
        output += f"| {time} | {op['operation']} | `{query_preview}` | {rows} | {status} |\n"
    
    return output


# ============ TOOL: Read-Only Query ============
@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def query_database(sql: str) -> str:
    """
    Execute a read-only SQL query against the database.
    
    Args:
        sql: A valid SELECT statement. Only SELECT queries are allowed.
             For write operations, use write_database instead.
    
    Returns:
        Query results as a formatted string, or an error message.
    """
    try:
        validate_readonly(sql)
    except SecurityError as e:
        return f"SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        return f"Error parsing SQL: {str(e)}"

    try:
        async with db.get_connection() as conn:
            rows = await conn.fetch(sql)
            
            if not rows:
                return "Query executed successfully. No results returned."
            
            results = [dict(row) for row in rows]
            
            if len(results) > 100:
                return (
                    f"Query returned {len(results)} rows. "
                    f"Showing first 100:\n\n"
                    f"{json.dumps(results[:100], default=str, ensure_ascii=False, indent=2)}"
                )
            
            return json.dumps(results, default=str, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return f"Database Error: {str(e)}"


# ============ TOOL: Write with Approval ============
@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def write_database(sql: str, dry_run: bool = False) -> str:
    """
    Execute a write operation (INSERT or UPDATE) against the database.
    
    This tool is marked as potentially destructive using MCP annotations.
    Some hosts may show a confirmation UI, but **do not rely on host UX for
    security**—server-side validation + DB permissions are the enforcement boundary.
    
    Args:
        sql: A valid INSERT or UPDATE statement. 
             DELETE, DROP, and other destructive operations are forbidden.
        dry_run: If True, validates the query without executing it.
                 Use this to preview what would happen.
    
    Returns:
        Success message with affected row count, or an error message.
    """
    # ========== WRITES ENABLED CHECK ==========
    if os.getenv("ENABLE_WRITES", "false").lower() != "true":
        return "Writes are disabled on this server (ENABLE_WRITES=false)."
    
    # ========== VALIDATION ==========
    try:
        validation = validate_write(sql)
        operation = validation["operation"]
    except SecurityError as e:
        return f"SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        return f"Error parsing SQL: {str(e)}"
    
    # ========== DRY RUN MODE ==========
    if dry_run:
        return (
            f"DRY RUN: Query is valid.\n\n"
            f"**Operation:** {operation}\n"
            f"**Query:** `{sql}`\n\n"
            f"Set dry_run=False to execute."
        )
    
    # ========== EXECUTION WITH TRANSACTION ==========
    try:
        async with db.transaction() as conn:
            # Execute and get affected row count
            result = await conn.execute(sql)
            
            # Parse "UPDATE 5" or "INSERT 0 3" format
            parts = result.split()
            if operation == "UPDATE":
                affected_rows = int(parts[1]) if len(parts) > 1 else 0
            elif operation == "INSERT":
                affected_rows = int(parts[2]) if len(parts) > 2 else 0
            else:
                affected_rows = 0
            
            # ========== AUDIT LOG (same transaction) ==========
            audit_id = await log_operation(
                operation=operation,
                sql_query=sql,
                affected_rows=affected_rows,
                success=True,
                conn=conn  # Pass connection for atomic commit
            )
        
        return (
            f"SUCCESS: {operation} completed.\n\n"
            f"**Affected rows:** {affected_rows}\n"
            f"**Audit log ID:** {audit_id}"
        )
        
    except Exception as e:
        # Log the failure (outside transaction since write failed)
        await log_operation(
            operation=operation,
            sql_query=sql,
            success=False,
            error_message=str(e)
        )
        return f"Database Error: {str(e)}"


# ============ ENTRY POINT ============
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## 6. Understanding Tool Annotations

The key to human-in-the-loop is the `annotations` parameter:

```python
@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def write_database(sql: str, dry_run: bool = False) -> str:
    ...
```

### How Hosts Handle Annotations

| Annotation | What Hosts May Do |
|------------|-------------------|
| `destructiveHint: True` | Display a confirmation dialog before execution |
| `readOnlyHint: True` | Indicate the tool doesn't modify state |
| `idempotentHint: True` | Indicate safe to retry on failure |
| `openWorldHint: True` | Tool interacts with external entities |

**Important:** Annotation support varies by host. Claude Desktop may use these hints to show approval dialogs or adjust behavior. Other hosts may handle them differently or ignore them. Always assume annotations are **hints**, not guarantees, your security layer is the real enforcement.

### The Dry Run Pattern

The `dry_run` parameter is a safety feature:

```
User: "Update John's email to john@newco.com"

Claude: Let me first validate this query with a dry run...

→ write_database(sql="UPDATE users SET email='john@newco.com' WHERE id=42", dry_run=True)

← "DRY RUN: Query is valid. Operation: UPDATE. Set dry_run=False to execute."

Claude: The query looks correct. Should I proceed with the actual update?

User: "Yes, go ahead"

→ write_database(sql="UPDATE users SET email='john@newco.com' WHERE id=42", dry_run=False)

[Host shows approval dialog]

User clicks "Allow"

← "SUCCESS: UPDATE completed. Affected rows: 1. Audit log ID: 47"
```

This gives the user two checkpoints:
1. **Dry run validation** — See what will happen
2. **Host approval dialog** — Final confirmation

---

## 7. Testing the Write Flow

### Test 1: Dry Run First (Recommended)

**You:** *"Update the user with ID 5 to have admin role, but do a dry run first."*

**Claude generates:**
```python
write_database(
    sql="UPDATE users SET role = 'admin' WHERE id = 5",
    dry_run=True
)
```

**Result:**
```
DRY RUN: Query is valid.

**Operation:** UPDATE
**Query:** `UPDATE users SET role = 'admin' WHERE id = 5`

Set dry_run=False to execute.
```

---

### Test 2: Execute with Approval

**You:** *"Looks good, run it for real."*

**Claude generates:**
```python
write_database(
    sql="UPDATE users SET role = 'admin' WHERE id = 5",
    dry_run=False
)
```

**Host shows dialog:**
```
┌──────────────────────────────────────────────┐
│  Database Write Requested                    │
│                                              │
│ This operation will modify data in the       │
│ database.                                    │
│                                              │
│ [Cancel]                      [Allow]        │
└──────────────────────────────────────────────┘
```

**User clicks "Allow"**

**Result:**
```
SUCCESS: UPDATE completed.

**Affected rows:** 1
**Audit log ID:** 47
```

---

### Test 3: Attempting DELETE (Blocked)

**You:** *"Delete all inactive users."*

**Claude generates:**
```python
write_database(sql="DELETE FROM users WHERE active = false")
```

**Result:**
```
SECURITY VIOLATION: Operation 'DELETE' is permanently forbidden.
```

The query never reaches the approval stage, it's blocked at the security layer.

---

### Test 4: Checking the Audit Log

**You:** *"What database changes happened recently?"*

**Claude reads resource:** `postgres://audit`

**Result:**
```markdown
# Recent Database Operations

| Time | Operation | Query | Rows | Status |
|------|-----------|-------|------|--------|
| 2026-01-17 14:32 | UPDATE | `UPDATE users SET role = 'admin' WHERE...` | 1 | ✅ |
| 2026-01-17 14:28 | INSERT | `INSERT INTO products (name, price) VA...` | 1 | ✅ |
| 2026-01-17 13:15 | UPDATE | `UPDATE orders SET status = 'shipped' ...` | 5 | ✅ |
```

---

## 8. The Complete Trust Model

```
┌─────────────────────────────────────────────────────────────┐
│                   LAYERED SECURITY MODEL                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Layer 1: SECURITY.PY (Code)                                │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • DELETE/DROP/TRUNCATE permanently blocked          │    │
│  │ • Only INSERT/UPDATE allowed for writes             │    │
│  │ • Single statement enforced (no chaining)           │    │
│  │ • Token-level scanning for hidden keywords          │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Layer 2: DRY RUN (User)                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • Preview what will happen                          │    │
│  │ • Validate before committing                        │    │
│  │ • User can abort if query looks wrong               │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Layer 3: HOST APPROVAL (UI)                                │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • Destructive annotation triggers dialog            │    │
│  │ • User must click "Allow"                           │    │
│  │ • Final checkpoint before execution                 │    │ 
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Layer 4: TRANSACTION (Database)                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • All-or-nothing execution                          │    │
│  │ • Automatic rollback on error                       │    │
│  │ • No partial updates                                │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  Layer 5: AUDIT LOG (Compliance)                            │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ • Every operation recorded                          │    │
│  │ • Includes success/failure                          │    │
│  │ • Enables investigation and rollback                │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**This is defense in depth.** No single layer is responsible for safety. If one layer fails, the others catch it.

---

## 9. Production Considerations

### Rate Limiting

For production, you should add rate limiting to prevent abuse:

```python
from datetime import datetime, timedelta
from collections import defaultdict

# Simple in-memory rate limiter (DEMO ONLY)
# For production: use Redis with per-user/tenant tracking
write_counts = defaultdict(list)
MAX_WRITES_PER_MINUTE = 10

def check_rate_limit() -> bool:
    """
    Returns True if within rate limit.
    
    WARNING: This is demo-only. In production use:
    - Redis-based token bucket
    - Per-user/tenant limits
    - Distributed rate limiting
    """
    now = datetime.now()
    minute_ago = now - timedelta(minutes=1)
    
    # Clean old entries
    write_counts["global"] = [
        t for t in write_counts["global"] if t > minute_ago
    ]
    
    if len(write_counts["global"]) >= MAX_WRITES_PER_MINUTE:
        return False
    
    write_counts["global"].append(now)
    return True
```

> **Production Note:** This in-memory rate limiter resets on restart and doesn't work across multiple processes. Use Redis or a proper rate limiting service for production.

### Row Limit Enforcement

Prevent mass updates:

```python
# In validate_write(), add:
async def check_affected_rows_preview(conn, sql: str, max_rows: int = 100):
    """
    For UPDATE statements, preview how many rows would be affected.
    Block if too many.
    """
    if not sql.strip().upper().startswith("UPDATE"):
        return True
    
    # Convert UPDATE to COUNT query
    # This is simplified - production would need proper SQL parsing
    where_idx = sql.upper().find("WHERE")
    if where_idx == -1:
        raise SecurityError("UPDATE without WHERE clause is forbidden")
    
    where_clause = sql[where_idx:]
    table_match = sql.upper().split("UPDATE")[1].split("SET")[0].strip()
    
    count_sql = f"SELECT COUNT(*) FROM {table_match} {where_clause}"
    count = await conn.fetchval(count_sql)
    
    if count > max_rows:
        raise SecurityError(
            f"This would affect {count} rows (max: {max_rows}). "
            "Please narrow your WHERE clause."
        )
    
    return True
```

### Environment-Based Permissions

```python
import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

def is_write_allowed() -> bool:
    """Only allow writes in non-production environments."""
    if ENVIRONMENT == "production":
        return False
    return True
```

---

## Key Takeaways

> **What We Built:**
> - **Write Tool** with human-in-the-loop approval
> - **Dry Run Mode** for previewing changes
> - **Transaction Support** for atomic operations
> - **Audit Logging** for compliance and debugging
>
> **The Pattern:** AI proposes → Human approves → System executes → Audit records

---

## Complete Project Structure

```
mcp-db-analyst/
├── pyproject.toml
├── .env                    # DATABASE_URL, DATABASE_WRITE_URL, ENABLE_WRITES
└── src/
    ├── __init__.py
    ├── server.py           # MCP server with read + write tools
    ├── database.py         # Separate read + write pools + transactions
    ├── security.py         # Read + write validation
    ├── schema.py           # Introspection (unchanged)
    └── audit.py            # Operation logging (atomic)
```

---

## What's Next

We've mastered database access. Now let's tackle a different domain: **infrastructure.**

In **Blog 7: DevOps First Responder (Part 1)**, we'll build an MCP server that:
- Connects to your Kubernetes cluster
- Lists pods, deployments, and services
- Retrieves logs from any container
- Diagnoses crash loops automatically

It's 3 AM and your cluster is failing. Wouldn't it be nice to just ask "What's wrong?" and get an answer?

---

## Quick Reference

### Tool Annotations for Approval
```python
@mcp.tool(
    annotations={
        "destructiveHint": True,
        "readOnlyHint": False,
        "idempotentHint": False,
    }
)
async def dangerous_tool():
    ...
```

### Transaction Pattern
```python
async with db.transaction() as conn:
    await conn.execute("INSERT ...")
    await conn.execute("UPDATE ...")
    # Auto-commits if no exception
    # Auto-rolls back if exception raised
```

### Audit Log Query
```sql
SELECT * FROM mcp_audit_log 
ORDER BY timestamp DESC 
LIMIT 20;
```

---

| [← Blog 5: Database Analyst Part 1](../blog-5/blog.md) | [Blog 7: DevOps First Responder →](../blog-7/blog.md) |
|:---|---:|
