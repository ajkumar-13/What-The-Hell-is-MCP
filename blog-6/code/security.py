# src/security.py - Extended SQL Validation Firewall
# Blog 6: Secure Database Analyst Part 2
# Now supports tiered permissions: read-only vs write mode

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


# ============ PERMISSION TIERS ============

# Operations allowed in read-only mode
READONLY_OPERATIONS = {"SELECT"}

# Operations allowed in write mode (in addition to READONLY)
WRITE_OPERATIONS = {"INSERT", "UPDATE"}

# Operations that are NEVER allowed, regardless of mode
# These are too dangerous for LLM-assisted workflows
ALWAYS_FORBIDDEN = {
    # Destructive DML
    "DELETE",       # Use soft deletes instead
    "TRUNCATE",     # Wipes entire table
    
    # DDL - Schema changes
    "DROP",         # Destroys objects
    "ALTER",        # Modifies schema
    "CREATE",       # Creates objects
    
    # DCL - Permission changes
    "GRANT",        # Gives permissions
    "REVOKE",       # Removes permissions
    
    # Dangerous execution
    "EXEC",
    "EXECUTE",
    
    # PostgreSQL specific
    "COPY",         # Can read/write files
    "INTO",         # SELECT INTO creates tables
}


def validate_query(sql: str, allow_writes: bool = False) -> dict:
    """
    Analyzes SQL to ensure it meets security requirements.
    
    This is the core security function. Every query must pass through
    here before touching the database.
    
    Args:
        sql: The SQL statement to validate
        allow_writes: If True, allows INSERT/UPDATE operations
                     If False, only SELECT is allowed
    
    Returns:
        dict with keys:
        - 'valid': bool - Whether query passed validation
        - 'operation': str - The detected operation type (SELECT, INSERT, etc.)
        
    Raises:
        SecurityError: If query violates security rules
    
    Security Model:
        - Read mode: Only SELECT allowed
        - Write mode: SELECT, INSERT, UPDATE allowed
        - Never allowed: DELETE, DROP, TRUNCATE, ALTER, etc.
    """
    # Parse SQL into tokens (note: sqlparse is a tokenizer, not a full AST parser)
    parsed = sqlparse.parse(sql)
    
    if not parsed:
        raise SecurityError("Empty query")

    # For write operations, only allow single statements
    # This prevents chained attacks like: "INSERT ...; DROP TABLE ..."
    if allow_writes:
        non_empty_statements = [s for s in parsed if s.get_type()]
        if len(non_empty_statements) > 1:
            raise SecurityError(
                "Multiple statements not allowed for write operations. "
                "Submit one statement at a time for safety."
            )

    operations_found = []
    
    for statement in parsed:
        # Skip empty statements (trailing semicolons, whitespace)
        if not statement.tokens:
            continue
        
        # Determine statement type
        stmt_type = statement.get_type()
        
        if stmt_type is None:
            raise SecurityError(
                "Could not determine query type. "
                "Ensure your query starts with a valid SQL keyword."
            )
        
        stmt_type = stmt_type.upper()
        operations_found.append(stmt_type)
        
        # ========== CHECK 1: Always Forbidden ==========
        if stmt_type in ALWAYS_FORBIDDEN:
            raise SecurityError(
                f"Operation '{stmt_type}' is permanently forbidden. "
                f"This operation is too dangerous for AI-assisted workflows."
            )
        
        # ========== CHECK 2: Mode-Based Permission ==========
        if allow_writes:
            allowed = READONLY_OPERATIONS | WRITE_OPERATIONS
        else:
            allowed = READONLY_OPERATIONS
        
        if stmt_type not in allowed:
            if stmt_type in WRITE_OPERATIONS:
                raise SecurityError(
                    f"Operation '{stmt_type}' requires write mode. "
                    "Use the write_database tool instead of query_database."
                )
            else:
                raise SecurityError(
                    f"Operation '{stmt_type}' is not allowed."
                )
        
        # ========== CHECK 2.5: SELECT INTO Detection ==========
        # SELECT INTO creates a new table (bypasses read-only intent)
        if stmt_type == "SELECT":
            sql_upper = sql.upper()
            if " INTO " in sql_upper:
                raise SecurityError(
                    "SELECT INTO is forbidden. "
                    "It creates new tables, violating read-only mode."
                )

        # ========== CHECK 3: UPDATE Must Have WHERE ==========
        # Prevent accidental mass updates
        if stmt_type == "UPDATE":
            sql_upper = sql.upper()
            if " WHERE " not in sql_upper:
                raise SecurityError(
                    "UPDATE without WHERE clause is forbidden. "
                    "Mass updates risk data corruption. Add a WHERE clause."
                )
        
        # ========== CHECK 3.5: INSERT Must Use VALUES ==========
        # Block INSERT...SELECT which can bulk-insert from arbitrary queries
        if stmt_type == "INSERT":
            sql_upper = sql.upper()
            if "VALUES" not in sql_upper:
                raise SecurityError(
                    "Only INSERT ... VALUES is allowed. "
                    "INSERT ... SELECT can bulk-insert from arbitrary queries."
                )
        
        # ========== CHECK 4: Deep Token Inspection ==========
        # Even in allowed statements, scan for hidden dangerous keywords
        # This catches attacks like:
        # SELECT * FROM (DELETE FROM users RETURNING *) x
        for token in statement.flatten():
            if token.ttype in (Keyword.DML, Keyword.DDL, Keyword):
                if token.value.upper() in ALWAYS_FORBIDDEN:
                    raise SecurityError(
                        f"Forbidden keyword detected: {token.value.upper()}. "
                        "Subqueries and CTEs cannot contain dangerous operations."
                    )

    return {
        "valid": True,
        "operation": operations_found[0] if operations_found else None
    }


def validate_readonly(sql: str) -> bool:
    """
    Convenience wrapper for read-only validation.
    
    Use this for the query_database tool.
    
    Args:
        sql: SELECT statement to validate
        
    Returns:
        True if query is safe
        
    Raises:
        SecurityError: If query is not a safe SELECT
    """
    result = validate_query(sql, allow_writes=False)
    return result["valid"]


def validate_write(sql: str) -> dict:
    """
    Validate a write operation (INSERT/UPDATE).
    
    Use this for the write_database tool.
    
    Args:
        sql: INSERT or UPDATE statement to validate
        
    Returns:
        dict with 'valid' and 'operation' keys
        
    Raises:
        SecurityError: If query violates security rules
    """
    return validate_query(sql, allow_writes=True)
