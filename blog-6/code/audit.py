# src/audit.py - Audit Logging for Database Operations
# Blog 6: Secure Database Analyst Part 2
# Records every write operation for compliance and debugging

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
    
    Call this at server startup to ensure the table is ready.
    Uses IF NOT EXISTS so it's safe to call multiple times.
    
    Note: Split into separate execute calls because asyncpg
    doesn't reliably execute multiple statements in one call.
    """
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
    
    create_operation_index_sql = """
    CREATE INDEX IF NOT EXISTS idx_audit_operation
    ON mcp_audit_log(operation);
    """
    
    async with db.get_connection() as conn:
        await conn.execute(create_table_sql)
        await conn.execute(create_index_sql)
        await conn.execute(create_operation_index_sql)
        logger.info("Audit table ready")


async def log_operation(
    operation: str,
    sql_query: str,
    affected_rows: int = None,
    success: bool = True,
    error_message: str = None,
    metadata: dict = None,
    conn=None  # Optional: pass existing connection for atomic transactions
) -> int:
    """
    Records a database operation to the audit log.
    
    Call this after every write operation (successful or failed).
    
    Args:
        operation: The type of operation (INSERT, UPDATE, etc.)
        sql_query: The SQL statement that was executed
        affected_rows: Number of rows modified (None if unknown)
        success: Whether the operation completed successfully
        error_message: Error details if success=False
        metadata: Optional dict with extra context (stored as JSONB)
        conn: Optional database connection. If provided, logs within
              the same transaction (atomic with the write operation).
              If None, creates a new connection.
    
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
    
    async def do_insert(c):
        row = await c.fetchrow(
            insert_sql,
            operation,
            sql_query,
            affected_rows,
            success,
            error_message,
            session_id,
            host_name,
            json.dumps(metadata) if metadata else None
        )
        return row["id"]
    
    # Use provided connection or get a new one
    if conn is not None:
        return await do_insert(conn)
    else:
        async with db.get_connection() as c:
            return await do_insert(c)


async def get_recent_operations(limit: int = 20) -> list:
    """
    Retrieves recent audit log entries.
    
    Useful for:
    - Showing the LLM what changes have been made
    - Debugging what happened
    - Compliance reporting
    
    Args:
        limit: Maximum number of entries to return (default 20)
    
    Returns:
        List of dicts, each representing an audit log entry
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


async def get_operations_by_date(
    start_date: datetime,
    end_date: datetime = None
) -> list:
    """
    Retrieves audit log entries within a date range.
    
    Args:
        start_date: Start of the range (inclusive)
        end_date: End of the range (inclusive), defaults to now
    
    Returns:
        List of audit log entries
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc)
    
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
    WHERE timestamp >= $1 AND timestamp <= $2
    ORDER BY timestamp DESC;
    """
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql, start_date, end_date)
        return [dict(row) for row in rows]
