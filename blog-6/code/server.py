# src/server.py - Secure Database Analyst with Write Support
# Blog 6: Secure Database Analyst Part 2
# Adds write operations with human-in-the-loop approval

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from .database import db
from .security import validate_readonly, validate_write, SecurityError
from .schema import get_database_schema, get_foreign_keys
from .audit import ensure_audit_table, log_operation, get_recent_operations

# Configure logging to stderr (STDIO transport requires stdout to be clean)
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


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


# ============ RESOURCES ============

@mcp.resource("postgres://schema")
async def get_schema() -> str:
    """
    Returns the complete database schema structure.
    
    Use this resource to understand:
    - What tables exist in the database
    - What columns each table has
    - Column data types
    - Primary keys
    
    This is automatically provided as context to help you
    write accurate SQL queries.
    """
    return await get_database_schema()


@mcp.resource("postgres://relationships")
async def get_relationships() -> str:
    """
    Returns foreign key relationships between tables.
    
    Use this to understand how tables connect to each other,
    which helps when writing JOIN queries.
    """
    return await get_foreign_keys()


@mcp.resource("postgres://audit")
async def get_audit_log() -> str:
    """
    Returns the 20 most recent database operations from the audit log.
    
    Use this to understand what changes have been made recently.
    Each entry includes:
    - Timestamp
    - Operation type (INSERT, UPDATE)
    - SQL query executed
    - Number of affected rows
    - Success/failure status
    """
    operations = await get_recent_operations(20)
    
    if not operations:
        return "No operations recorded yet."
    
    output = "# Recent Database Operations\n\n"
    output += "| Time | Operation | Query | Rows | Status |\n"
    output += "|------|-----------|-------|------|--------|\n"
    
    for op in operations:
        time = op["timestamp"].strftime("%Y-%m-%d %H:%M")
        query_preview = (
            op["sql_query"][:50] + "..." 
            if len(op["sql_query"]) > 50 
            else op["sql_query"]
        )
        # Escape pipe characters in query for markdown table
        query_preview = query_preview.replace("|", "\\|")
        status = "✅" if op["success"] else "❌"
        rows = op["affected_rows"] if op["affected_rows"] is not None else "-"
        output += f"| {time} | {op['operation']} | `{query_preview}` | {rows} | {status} |\n"
    
    return output


# ============ TOOLS ============

@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
    }
)
async def query_database(sql: str) -> str:
    """
    Execute a read-only SQL query against the database.
    
    SECURITY: Only SELECT queries are allowed. Any attempt to
    INSERT, UPDATE, DELETE, DROP, or otherwise modify data
    will be blocked.
    
    For write operations, use the write_database tool instead.
    
    Args:
        sql: A valid SELECT statement. 
             Examples:
             - SELECT * FROM users LIMIT 10
             - SELECT name, email FROM users WHERE active = true
             - SELECT COUNT(*) FROM orders WHERE created_at > '2024-01-01'
    
    Returns:
        Query results as a formatted string, or an error message
        if the query fails or violates security rules.
    """
    # ========== SECURITY VALIDATION ==========
    try:
        validate_readonly(sql)
    except SecurityError as e:
        return f"❌ SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        return f"❌ Error parsing SQL: {str(e)}"

    # ========== EXECUTE QUERY ==========
    try:
        async with db.get_connection() as conn:
            rows = await conn.fetch(sql)
            
            if not rows:
                return "Query executed successfully. No results returned."
            
            results = [dict(row) for row in rows]
            
            # Limit output size to prevent context overflow
            if len(results) > 100:
                return (
                    f"Query returned {len(results)} rows. "
                    f"Showing first 100:\n\n"
                    f"{json.dumps(results[:100], default=str, ensure_ascii=False, indent=2)}"
                )
            
            return json.dumps(results, default=str, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return f"❌ Database Error: {str(e)}"


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
    
    ⚠️ REQUIRES USER APPROVAL - The host will ask for confirmation before executing.
    
    SECURITY:
    - Only INSERT and UPDATE are allowed
    - DELETE, DROP, TRUNCATE, and other destructive operations are permanently forbidden
    - All writes are logged to the audit table
    - Writes must be explicitly enabled via ENABLE_WRITES=true
    
    Args:
        sql: A valid INSERT or UPDATE statement.
             Examples:
             - INSERT INTO users (name, email) VALUES ('John', 'john@example.com')
             - UPDATE users SET role = 'admin' WHERE id = 5
             
        dry_run: If True, validates the query without executing it.
                 Use this to preview what would happen before committing.
                 Default is False.
    
    Returns:
        Success message with affected row count, or an error message.
    """
    # ========== ENVIRONMENT CHECK ==========
    if os.getenv("ENABLE_WRITES", "false").lower() != "true":
        return "❌ Writes are disabled on this server (ENABLE_WRITES=false)."
    
    # ========== VALIDATION ==========
    try:
        validation = validate_write(sql)
        operation = validation["operation"]
    except SecurityError as e:
        return f"❌ SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        return f"❌ Error parsing SQL: {str(e)}"
    
    # ========== DRY RUN MODE ==========
    if dry_run:
        return (
            f"✅ DRY RUN: Query is valid.\n\n"
            f"**Operation:** {operation}\n"
            f"**Query:** `{sql}`\n\n"
            f"To execute for real, set `dry_run=False`."
        )
    
    # ========== EXECUTION WITH ATOMIC TRANSACTION ==========
    try:
        async with db.transaction() as conn:
            # Execute and get status message
            result = await conn.execute(sql)
            
            # Parse affected row count from result string
            # PostgreSQL returns: "UPDATE 5" or "INSERT 0 3"
            parts = result.split()
            if operation == "UPDATE":
                affected_rows = int(parts[1]) if len(parts) > 1 else 0
            elif operation == "INSERT":
                # INSERT returns "INSERT oid count"
                affected_rows = int(parts[2]) if len(parts) > 2 else 0
            else:
                affected_rows = 0
            
            # Atomic audit log - same transaction as write
            audit_id = await log_operation(
                operation=operation,
                sql_query=sql,
                affected_rows=affected_rows,
                success=True,
                conn=conn  # Use same connection for atomicity
            )
        
        return (
            f"✅ SUCCESS: {operation} completed.\n\n"
            f"**Affected rows:** {affected_rows}\n"
            f"**Audit log ID:** {audit_id}"
        )
        
    except Exception as e:
        # Log the failure (separate transaction, since write failed)
        await log_operation(
            operation=operation,
            sql_query=sql,
            success=False,
            error_message=str(e)
        )
        return f"❌ Database Error: {str(e)}"


# ============ ENTRY POINT ============

if __name__ == "__main__":
    mcp.run(transport="stdio")
