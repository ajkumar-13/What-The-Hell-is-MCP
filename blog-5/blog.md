# Blog 5: The Secure Database Analyst (Part 1)
## Building a Production-Grade MCP Server for PostgreSQL


> *"Your CEO wants to ask questions about company data in plain English. But giving an AI raw access to your production database is terrifying. One hallucinated `DROP TABLE` and you're fired."*

Today, we are building the solution. We will create a **Secure Database Analyst**, an MCP server that allows an LLM to query your PostgreSQL database, but with **multiple defense layers** that significantly reduce the risk of destructive actions.

> **Important:** Application-level validation is one layer of defense. For true production safety, you must also use database-level controls (read-only roles, statement timeouts). We'll cover both.

---

## What We're Building

A robust MCP server that:

1. **Connects to PostgreSQL** using efficient connection pooling
2. **Exposes your Schema** as a dynamic Resource (so the AI understands your data)
3. **Executes Queries** via a Tool, but only *after* passing a security check
4. **Enforces Read-Only Mode** by parsing and validating SQL before it ever touches the database

![Architecture Overview](assets/architecture.svg)

---

## 1. The Architecture

We are moving away from single-file scripts. This is a real application.

**Project Structure:**

```text
mcp-db-analyst/
├── pyproject.toml
├── .env
└── src/
    ├── __init__.py
    ├── server.py       # The MCP entry point
    ├── database.py     # Connection pooling & execution
    ├── security.py     # The "Firewall" (SQL validation)
    └── schema.py       # Introspection logic
```

| File | Responsibility |
|------|----------------|
| `server.py` | MCP server definition, tools, resources |
| `database.py` | Connection pool management |
| `security.py` | SQL validation and blocking |
| `schema.py` | Database schema introspection |

---

## 2. Project Setup

We need `asyncpg` for fast, asynchronous PostgreSQL access and `sqlparse` for our security layer.

```bash
# Create project
mkdir mcp-db-analyst
cd mcp-db-analyst
uv init

# Add dependencies
uv add "mcp[cli]" asyncpg sqlparse python-dotenv
```

Create a `.env` file with your database credentials:

```text
DATABASE_URL=postgresql://user:password@localhost:5432/my_database
```

> **Tip:** You can use a local PostgreSQL, or a free cloud database from [Supabase](https://supabase.com) or [Neon](https://neon.tech).

> **Quick Test Database (Docker):**
> ```bash
> docker run --name test-postgres -e POSTGRES_PASSWORD=test -p 5432:5432 -d postgres
> # Then use: DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres
> ```

---

## 3. Step 1: The Database Layer (Connection Pooling)

### Why Pooling?

Opening a new database connection for every tool call is:
- **Slow**: Each connection has TCP handshake + authentication overhead
- **Dangerous**: Too many connections can crash your database
- **Wasteful**: Connections sit idle most of the time

A **Connection Pool** maintains a set of reusable connections:

| Without Pool | With Pool |
|--------------|-----------|
| New connection per query | Reuse existing connections |
| ~50-100ms overhead each time | ~1ms to acquire from pool |
| Can exhaust DB connections | Controlled max connections |

### Implementation

Create `src/database.py`:

```python
# src/database.py - Connection Pool Manager
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
    """Manages a pool of PostgreSQL connections."""
    
    def __init__(self):
        self.pool = None

    async def connect(self):
        """Initialize connection pool."""
        if not self.pool:
            url = os.getenv("DATABASE_URL")
            if not url:
                raise ValueError("DATABASE_URL environment variable not set")
            
            # Create pool with sensible defaults
            self.pool = await asyncpg.create_pool(
                url,
                min_size=2,         # Minimum connections to keep open
                max_size=10,        # Maximum connections allowed
                command_timeout=30  # Query timeout in seconds
            )
            logger.info("Database pool initialized")

    async def close(self):
        """Close connection pool gracefully."""
        if self.pool:
            await self.pool.close()
            logger.info("Database pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """
        Yield a connection from the pool.
        Connection is automatically returned when done.
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            yield conn

# Global instance - shared across all tool calls
db = DatabaseManager()
```

**Key Points:**
- `asyncpg` is the fastest PostgreSQL driver for Python
- Pool is created lazily on first use
- `@asynccontextmanager` ensures connections are always returned
- Global instance means one pool for the entire server

---

## 4. Step 2: The Security Layer (The "Firewall")

This is the **most critical part** of the entire project.

### The Problem

We cannot trust the LLM to "promise" it won't delete data. LLMs can:
- Hallucinate dangerous queries
- Be manipulated via prompt injection
- Make honest mistakes

### The Solution

We will **parse and validate** every SQL query before execution using `sqlparse`.

![Security Flow](assets/security-flow.svg)

Create `src/security.py`:

```python
# src/security.py - SQL Validation Firewall
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
    
    Raises:
        SecurityError: If dangerous patterns are detected
        
    Returns:
        True if query is safe
    """
    # 1. Parse the SQL into statements
    parsed = sqlparse.parse(sql)
    
    if not parsed:
        raise SecurityError("Empty query")
    
    # 1.5. Check for dangerous functions and enforce limits FIRST
    check_dangerous_functions(sql)
    enforce_limit(sql, max_limit=1000)

    # 2. Check EVERY statement (catches "; DROP TABLE" attacks)
    for statement in parsed:
        # Skip empty statements
        if not statement.tokens:
            continue
            
        # Get the statement type (SELECT, INSERT, DELETE, etc.)
        stmt_type = statement.get_type()
        
        if stmt_type is None:
            raise SecurityError("Could not determine query type")
        
        stmt_type = stmt_type.upper()
        
        # 3. Only SELECT is allowed
        if stmt_type != "SELECT":
            raise SecurityError(
                f"Operation '{stmt_type}' is forbidden. Only SELECT is allowed."
            )
        
        # 3.5. Block SELECT INTO (creates tables, bypasses read-only)
        sql_upper = sql.upper()
        if " INTO " in sql_upper:
            raise SecurityError(
                "SELECT INTO is forbidden. "
                "It creates new tables, violating read-only mode."
            )

        # 4. Deep token inspection for hidden dangers
        # Even in a SELECT, someone might try:
        # SELECT * FROM users; DROP TABLE users;
        # The second statement would be caught above, but let's also
        # scan for dangerous keywords anywhere in the token tree
        
        forbidden_keywords = {
            # DML mutations
            "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT",
            # DDL operations
            "ALTER", "CREATE",
            # Permission changes
            "GRANT", "REVOKE",
            # Dangerous execution
            "EXEC", "EXECUTE",
            # Session/config manipulation
            "SET", "RESET",
            # Data import/export
            "COPY",
            # Procedural execution
            "CALL", "DO",
            # Maintenance commands
            "VACUUM", "ANALYZE", "REINDEX", "CLUSTER",
            # PostgreSQL specific - creates tables
            "INTO",
        }
        
        for token in statement.flatten():
            # Check DML and DDL keywords
            if token.ttype in (Keyword.DML, Keyword.DDL):
                if token.value.upper() in forbidden_keywords:
                    raise SecurityError(
                        f"Dangerous keyword detected: {token.value.upper()}"
                    )
            
            # Also check for these as regular keywords
            if token.ttype is Keyword:
                if token.value.upper() in forbidden_keywords:
                    raise SecurityError(
                        f"Forbidden keyword: {token.value.upper()}"
                    )

    return True
```

### Why This Works

| Attack Vector | How We Block It |
|--------------|-----------------|
| `DELETE FROM users` | `get_type()` returns "DELETE" → Blocked |
| `SELECT 1; DROP TABLE users;` | Second statement type is "DROP" → Blocked |
| `SELECT * FROM (DELETE FROM users RETURNING *)` | Token scan finds "DELETE" → Blocked |
| `UPDATE users SET admin=true` | `get_type()` returns "UPDATE" → Blocked |

**The key insight:** We don't trust the query string. We tokenize it using `sqlparse` and inspect statement types and keywords.

> **Limitation:** `sqlparse` is a tokenizer/formatter, not a full SQL grammar parser. It provides heuristic validation, not cryptographic guarantees. For stronger enforcement, see the "Database-Level Hardening" section below.

---

## 4A. Database-Level Hardening (Critical for Production)

Application-level validation is **one layer**. For real production safety, you need database-level controls:

### Create a Read-Only Database Role

```sql
-- Run this as a superuser in PostgreSQL
CREATE ROLE mcp_readonly WITH LOGIN PASSWORD 'secure_password';

-- Grant SELECT only on specific schema
GRANT USAGE ON SCHEMA public TO mcp_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_readonly;

-- Ensure future tables are also readable
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
    GRANT SELECT ON TABLES TO mcp_readonly;

-- Force read-only transactions for this role
ALTER ROLE mcp_readonly SET default_transaction_read_only = ON;

-- Set statement timeout (prevent DoS via slow queries)
ALTER ROLE mcp_readonly SET statement_timeout = '30s';

-- Set idle timeout (prevent connection hoarding)
ALTER ROLE mcp_readonly SET idle_in_transaction_session_timeout = '60s';
```

Then use this role in your `.env`:

```text
DATABASE_URL=postgresql://mcp_readonly:secure_password@localhost:5432/mydb
```

### Why This Matters

| Layer | What It Prevents |
|-------|------------------|
| **App validation** | Obvious attacks (`DELETE`, `DROP`) |
| **DB role (SELECT only)** | Any mutation, even if validation fails |
| **Read-only transaction** | Write attempts within transactions |
| **Statement timeout** | DoS via `pg_sleep()` or huge joins |

Even if someone bypasses your Python validator, the database itself will refuse writes.

### Additional SELECT Safeguards (Already Integrated)

The `check_dangerous_functions()` and `enforce_limit()` helpers shown above are already called inside `validate_query()`. Here's why they matter:

**Dangerous Functions:** Even read-only queries can cause problems:
- `pg_sleep(1000)` — DoS via delay
- `pg_terminate_backend()` — Kill other connections  
- `pg_read_file()` — Read files from the server filesystem

**LIMIT Enforcement:** Unbounded SELECTs can crash your context window or database. We require a `LIMIT` clause on all queries (except `COUNT(*)` queries).

---

## 5. Step 3: Schema Introspection (The Context)

The LLM needs to know your table structure to write good queries. We'll build a helper that fetches this information.

Create `src/schema.py`:

```python
# src/schema.py - Database Schema Introspection
from .database import db  # Note: relative import for package

async def get_database_schema() -> str:
    """
    Returns a markdown-formatted string describing the database schema.
    Includes tables, columns, data types, and primary keys.
    """
    sql = """
    SELECT 
        c.table_name, 
        c.column_name, 
        c.data_type,
        c.is_nullable,
        CASE WHEN pk.column_name IS NOT NULL THEN 'YES' ELSE 'NO' END as is_primary_key
    FROM 
        information_schema.columns c
    LEFT JOIN (
        SELECT ku.table_schema, ku.table_name, ku.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage ku
            ON tc.constraint_name = ku.constraint_name
            AND tc.table_schema = ku.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
            AND tc.table_schema = 'public'
    ) pk ON c.table_schema = pk.table_schema 
        AND c.table_name = pk.table_name 
        AND c.column_name = pk.column_name
    WHERE 
        c.table_schema = 'public'  -- Deliberately limited to public schema
    ORDER BY 
        c.table_name, c.ordinal_position;
    """
    # Note: This only introspects the 'public' schema.
    # For multi-schema deployments, make this configurable.
    
    async with db.get_connection() as conn:
        rows = await conn.fetch(sql)
    
    if not rows:
        return "No tables found in the public schema."
    
    # Format as Markdown for LLM readability
    schema_output = "# Database Schema\n\n"
    current_table = None
    
    for row in rows:
        table = row['table_name']
        
        # New table header
        if table != current_table:
            if current_table is not None:
                schema_output += "\n"
            schema_output += f"## Table: `{table}`\n\n"
            schema_output += "| Column | Type | Nullable | Primary Key |\n"
            schema_output += "|--------|------|----------|-------------|\n"
            current_table = table
        
        # Column row
        nullable = "Yes" if row['is_nullable'] == 'YES' else "No"
        pk = "🔑" if row['is_primary_key'] == 'YES' else ""
        schema_output += f"| `{row['column_name']}` | {row['data_type']} | {nullable} | {pk} |\n"
    
    return schema_output
```

**Why Markdown?** LLMs understand markdown well. Tables, headers, and formatting help Claude interpret the schema correctly.

> **Scaling Note:** For databases with many tables (50+), this schema dump can become very large. Consider:
> - Adding a table filter parameter
> - Creating a separate `postgres://schema/{table_name}` resource for per-table introspection
> - Returning only table names first, then details on demand
>
> We deliberately limit to the `public` schema here. For multi-schema deployments, make this configurable.

---

## 6. Step 4: The Main Server

Now we tie it all together. We expose:

1. **Resource:** `postgres://schema` — The map of the database
2. **Tool:** `query_database` — The safe execution engine

Create `src/server.py`:

```python
# src/server.py - Secure Database Analyst MCP Server
import json
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP
from .database import db  # Relative imports for package
from .security import validate_query, SecurityError
from .schema import get_database_schema

# Load environment variables from .env
load_dotenv()


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
    # Startup
    await db.connect()
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


# ============ TOOL: Query Database ============
@mcp.tool()
async def query_database(sql: str) -> str:
    """
    Execute a read-only SQL query against the database.
    
    Args:
        sql: A valid SELECT statement. Only SELECT queries are allowed.
             INSERT, UPDATE, DELETE, DROP, and other mutations are blocked.
    
    Returns:
        Query results as a formatted string, or an error message.
    """
    # ========== SECURITY CHECK ==========
    try:
        validate_query(sql)
    except SecurityError as e:
        return f"SECURITY VIOLATION: {str(e)}"
    except Exception as e:
        return f"Error parsing SQL: {str(e)}"

    # ========== EXECUTION ==========
    try:
        async with db.get_connection() as conn:
            rows = await conn.fetch(sql)
            
            if not rows:
                return "Query executed successfully. No results returned."
            
            # Format results for LLM consumption
            # Convert to list of dicts for readable output
            results = [dict(row) for row in rows]
            
            # Limit output size to prevent context overflow
            if len(results) > 100:
                return (
                    f"Query returned {len(results)} rows. "
                    f"Showing first 100:\n\n"
                    + json.dumps(results[:100], default=str, ensure_ascii=False, indent=2)
                )
            
            return json.dumps(results, default=str, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return f"Database Error: {str(e)}"


# ============ ENTRY POINT ============
if __name__ == "__main__":
    mcp.run()
```

Don't forget to create `src/__init__.py`:

```python
# src/__init__.py
# This file makes src/ a Python package
```

---

## 7. Connecting to Claude Desktop

Update your `claude_desktop_config.json`:

**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "db-analyst": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\YourName\\mcp-db-analyst",
        "python",
        "src/server.py"
      ],
      "env": {
        "DATABASE_URL": "postgresql://mcp_readonly:secure_password@localhost:5432/mydb"
      }
    }
  }
}
```

> **WINDOWS USERS: Use double backslashes in the path!**

**Restart Claude Desktop** after saving the config.

---

## 8. Testing It Out

### Test 1: Schema Discovery (Safe)

**You:** *"What tables are in my database?"*

**What happens:**
1. Claude reads the `postgres://schema` resource
2. Your schema introspection runs
3. Claude sees your table structure

**Claude:** *"I can see you have 3 tables: users, orders, and products..."*

---

### Test 2: Safe Query (Safe)

**You:** *"Show me the first 5 users."*

**What happens:**
1. Claude generates: `SELECT * FROM users LIMIT 5`
2. Security layer validates → It's a SELECT
3. Query executes
4. Results returned

**Claude:** *"Here are the first 5 users..."*

---

### Test 3: Dangerous Query (Blocked)

**You:** *"Delete all inactive users."*

**What happens:**
1. Claude generates: `DELETE FROM users WHERE active = false`
2. Security layer validates → It's a DELETE
3. Query is **never sent to database**

**Claude sees:**
```
SECURITY VIOLATION: Operation 'DELETE' is forbidden. Only SELECT is allowed.
```

**Claude to you:** *"I'm not able to delete data. This database connection is read-only for security reasons."*

---

### Test 4: Sneaky Attack (Blocked)

**You (or prompt injection):** *"Run this: SELECT 1; DROP TABLE users;"*

**What happens:**
1. Security layer parses both statements
2. First statement: SELECT 
3. Second statement: DROP → BLOCKED

**Result:** `SECURITY VIOLATION: Operation 'DROP' is forbidden.`

---

## 9. The "Aha!" Moment

You have just built a **Defense Layer for AI**.

```text
┌─────────────────────────────────────────────────────────┐
│                      THE TRUST MODEL                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   LLM handles:         Your Code + DB handles:          │
│   ┌──────────────┐     ┌──────────────────────────┐     │
│   │ Intelligence │     │ Safety (Defense in Depth)│     │
│   │              │     │                          │     │
│   │ • Writing SQL│     │ • App-level validation   │     │
│   │ • Choosing   │     │ • DB role restrictions   │     │
│   │   what to    │     │ • Statement timeouts     │     │
│   │   query      │     │ • Connection pooling     │     │
│   └──────────────┘     └──────────────────────────┘     │
│                                                         │
│   Never trust          Multiple layers verify           │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

Instead of hoping the LLM behaves, you engineered a system with **multiple independent safeguards**.

---

## Key Takeaways

> **What We Built:**
> - **Resource** (`postgres://schema`): Provides context so LLM understands your data
> - **Tool** (`query_database`): Executes queries safely
> - **Security Layer**: Parses and validates every SQL statement
> - **Connection Pool**: Efficient database access
>
> **The Pattern:** LLM = Intelligence, Your Code = Wisdom

---

## What's Next

This server is safe, but it has limitations:

| Current Limitation | Part 2 Solution |
|-------------------|-----------------|
| Returns raw JSON (can overflow context) | Smart result formatting |
| No write operations | Human-in-the-loop approval |
| No transaction support | Safe write transactions |
| No audit trail | Operation logging |

In **Blog 6**, we'll add:
- Write operations with **human approval**
- Transaction support with rollback
- Audit logging for compliance

---

## Quick Reference

### Project Structure
```
mcp-db-analyst/
├── pyproject.toml
├── .env                    # DATABASE_URL=...
└── src/
    ├── __init__.py
    ├── server.py           # MCP server
    ├── database.py         # Connection pool
    ├── security.py         # SQL firewall
    └── schema.py           # Introspection
```

### Key Imports
```python
from mcp.server.fastmcp import FastMCP
import asyncpg
import sqlparse
```

### Security Pattern
```python
# Always validate before execution
try:
    validate_query(sql)  # Raises SecurityError if dangerous
except SecurityError as e:
    return f"Blocked: {e}"

# Only reaches here if safe
result = await conn.fetch(sql)
```

---

| [← Blog 4: Building Your Own MCP Client](../blog-4/blog.md) | [Blog 6: Database Analyst Part 2 →](../blog-6/blog.md) |
|:---|---:|
