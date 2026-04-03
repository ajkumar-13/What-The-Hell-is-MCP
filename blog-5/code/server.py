# src/server.py - Secure Database Analyst MCP Server
# Blog 5: Secure Database Analyst
# Main MCP server that exposes database schema and safe query execution

import json
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from .database import db
from .security import validate_query, SecurityError
from .schema import get_database_schema, get_foreign_keys


# ============ LIFECYCLE MANAGEMENT ============

@asynccontextmanager
async def lifespan(server: FastMCP):
    """
    Manage server lifecycle: startup and shutdown.
    
    The lifespan pattern ensures:
    - Database connects when server starts
    - Database closes cleanly when server stops
    - Resources are properly cleaned up even on errors
    """
    # Startup: Initialize database connection pool
    await db.connect()
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


# ============ TOOLS ============

@mcp.tool()
async def query_database(sql: str) -> str:
    """
    Execute a read-only SQL query against the database.
    
    SECURITY: Only SELECT queries are allowed. Any attempt to
    INSERT, UPDATE, DELETE, DROP, or otherwise modify data
    will be blocked.
    
    Args:
        sql: A valid SELECT statement. 
             Examples:
             - SELECT * FROM users LIMIT 10
             - SELECT name, email FROM users WHERE active = true
             - SELECT COUNT(*) FROM orders WHERE created_at > '2024-01-01'
    
    Returns:
        Query results as a formatted string, or an error message
        if the query fails or violates security rules.
    
    Tips for writing good queries:
        - Always use LIMIT to avoid returning too many rows
        - Use specific column names instead of SELECT *
        - Check the schema resource first to see available tables
    """
    # ========== STEP 1: SECURITY VALIDATION ==========
    # The query MUST pass security checks before execution
    try:
        validate_query(sql)
    except SecurityError as e:
        # Query was dangerous - block it
        return f"❌ SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        # Parsing failed - don't execute
        return f"❌ Error parsing SQL: {str(e)}"

    # ========== STEP 2: EXECUTE QUERY ==========
    # Only reaches here if security check passed
    try:
        async with db.get_connection() as conn:
            rows = await conn.fetch(sql)
            
            if not rows:
                return "Query executed successfully. No results returned."
            
            # Convert asyncpg Records to dicts
            results = [dict(row) for row in rows]
            
            # ========== STEP 3: FORMAT OUTPUT ==========
            # Limit output size to prevent context window overflow
            row_count = len(results)
            
            if row_count > 100:
                # Too many rows - truncate and warn
                return (
                    f"⚠️ Query returned {row_count} rows. "
                    f"Showing first 100 to prevent context overflow.\n\n"
                    + json.dumps(results[:100], default=str, ensure_ascii=False, indent=2)
                )
            
            return json.dumps(results, default=str, ensure_ascii=False, indent=2)
            
    except Exception as e:
        # Database error (syntax, missing table, etc.)
        return f"❌ Database Error: {str(e)}"


# ============ ENTRY POINT ============

if __name__ == "__main__":
    # Start the MCP server
    # This will listen on stdio for MCP protocol messages
    mcp.run()
