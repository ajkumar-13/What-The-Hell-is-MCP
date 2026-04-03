# src/security.py - SQL Validation Firewall
# Blog 5: Secure Database Analyst
# Parses and validates SQL to ensure only safe queries execute

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


# ============ SECURITY HELPERS ============

DANGEROUS_FUNCTIONS = {
    "pg_sleep",             # DoS via delay
    "pg_terminate_backend", # Kill connections
    "pg_cancel_backend",
    "lo_import", "lo_export",  # Large object file access
    "pg_read_file",         # File system access
    "pg_ls_dir",
}


def check_dangerous_functions(sql: str) -> None:
    """Block queries containing dangerous function calls."""
    sql_lower = sql.lower()
    for func in DANGEROUS_FUNCTIONS:
        if func in sql_lower:
            raise SecurityError(f"Function '{func}' is not allowed")


def enforce_limit(sql: str, max_limit: int = 1000) -> None:
    """Ensure query has a reasonable LIMIT clause."""
    sql_upper = sql.upper()
    
    # Skip if it's a COUNT query
    if "COUNT(" in sql_upper or "COUNT (" in sql_upper:
        return
    
    if "LIMIT" not in sql_upper:
        raise SecurityError(
            f"Query must include LIMIT clause (max {max_limit} rows). "
            "Add 'LIMIT n' to your query."
        )


def validate_query(sql: str) -> bool:
    """
    Analyzes SQL to ensure it is strictly READ-ONLY.
    
    This is the "firewall" for your database. Every query must pass
    through this function before execution.
    
    Args:
        sql: The SQL query string to validate
        
    Raises:
        SecurityError: If dangerous patterns are detected
        
    Returns:
        True if query is safe to execute
    
    Security Checks:
        1. Parse SQL into statements (catches multi-statement attacks)
        2. Check statement type (only SELECT allowed)
        3. Deep token inspection (catches hidden mutations)
    """
    # 1. Parse the SQL into an AST (Abstract Syntax Tree)
    parsed = sqlparse.parse(sql)
    
    if not parsed:
        raise SecurityError("Empty query")
    
    # 1.5. Check for dangerous functions and enforce limits FIRST
    check_dangerous_functions(sql)
    enforce_limit(sql, max_limit=1000)

    # 2. Check EVERY statement
    # This catches attacks like: "SELECT 1; DROP TABLE users;"
    # The semicolon creates two statements, and we check both
    for statement in parsed:
        # Skip empty statements (trailing semicolons)
        if not statement.tokens:
            continue
        
        # Get the statement type
        stmt_type = statement.get_type()
        
        if stmt_type is None:
            # Could be a comment-only statement or malformed SQL
            raise SecurityError(
                "Could not determine query type. "
                "Ensure your query starts with SELECT."
            )
        
        stmt_type = stmt_type.upper()
        
        # 3. WHITELIST: Only SELECT is allowed
        if stmt_type != "SELECT":
            raise SecurityError(
                f"Operation '{stmt_type}' is forbidden. "
                f"Only SELECT queries are allowed."
            )

        # 4. DEEP INSPECTION: Scan all tokens for hidden dangers
        # Even in a "SELECT", someone might try subquery attacks:
        # SELECT * FROM (DELETE FROM users RETURNING *) x
        # We catch these by scanning the entire token tree
        
        forbidden_keywords = {
            # DML - Data Manipulation
            "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT",
            # DDL - Data Definition  
            "ALTER", "CREATE",
            # DCL - Data Control
            "GRANT", "REVOKE",
            # Dangerous execution
            "EXEC", "EXECUTE",
            # PostgreSQL specific
            "COPY",  # Can read/write files
            "INTO",  # SELECT INTO creates tables (bypasses read-only)
        }
        
        # Check for SELECT INTO pattern (creates a new table)
        sql_upper = sql.upper()
        if "SELECT" in sql_upper and " INTO " in sql_upper:
            # Make sure it's not INSERT INTO (already caught above)
            # This catches: SELECT * INTO newtable FROM ...
            raise SecurityError(
                "SELECT INTO is forbidden. "
                "It creates new tables, violating read-only mode."
            )
        
        # Flatten the token tree to search inside subqueries
        for token in statement.flatten():
            token_value = token.value.upper()
            
            # Check DML keywords (INSERT, UPDATE, DELETE, etc.)
            if token.ttype is Keyword.DML:
                if token_value in forbidden_keywords:
                    raise SecurityError(
                        f"Dangerous DML keyword detected: {token_value}"
                    )
            
            # Check DDL keywords (CREATE, DROP, ALTER, etc.)
            if token.ttype is Keyword.DDL:
                raise SecurityError(
                    f"DDL statement detected: {token_value}. "
                    f"Schema modifications are not allowed."
                )
            
            # Check general keywords
            if token.ttype is Keyword:
                if token_value in forbidden_keywords:
                    raise SecurityError(
                        f"Forbidden keyword: {token_value}"
                    )

    # All checks passed
    return True


# ============ ADDITIONAL SECURITY HELPERS ============

def sanitize_identifier(identifier: str) -> str:
    """
    Sanitize a table or column name to prevent SQL injection.
    
    Use this when you need to dynamically build queries with
    user-provided identifiers.
    
    Args:
        identifier: Table or column name
        
    Returns:
        Sanitized identifier safe for SQL
        
    Raises:
        ValueError: If identifier contains dangerous characters
    """
    # Only allow alphanumeric and underscore
    if not identifier.replace('_', '').isalnum():
        raise ValueError(
            f"Invalid identifier: {identifier}. "
            f"Only letters, numbers, and underscores allowed."
        )
    
    # Prevent SQL keywords as identifiers
    sql_keywords = {"SELECT", "FROM", "WHERE", "DROP", "DELETE", "INSERT"}
    if identifier.upper() in sql_keywords:
        raise ValueError(
            f"Cannot use SQL keyword as identifier: {identifier}"
        )
    
    return identifier


def log_blocked_query(sql: str, reason: str):
    """
    Log blocked queries for security auditing.
    
    In production, you'd want to:
    - Send to a logging service
    - Include timestamp and user context
    - Alert on suspicious patterns
    """
    logger.warning(f"BLOCKED QUERY: {reason}")
    logger.warning(f"Query: {sql[:100]}{'...' if len(sql) > 100 else ''}")
