# src/database.py - Connection Pool Manager with Transactions
# Blog 6: Secure Database Analyst Part 2
# Extended with transaction support for write operations

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
    """
    Manages pools of PostgreSQL connections.
    
    Blog 6 additions:
    - Separate read and write connection pools
    - Transaction context manager for atomic write operations
    - Auto-commit on success, auto-rollback on exception
    """
    
    def __init__(self):
        self.read_pool = None
        self.write_pool = None

    async def connect(self):
        """
        Initialize the connection pools.
        
        - read_pool: Uses DATABASE_URL (read-only role)
        - write_pool: Uses DATABASE_WRITE_URL (limited write role)
        
        Pool settings:
        - min_size: Minimum connections to keep open (prevents cold starts)
        - max_size: Maximum connections allowed (prevents DB overload)
        - command_timeout: Query timeout to prevent runaway queries
        """
        if not self.read_pool:
            url = os.getenv("DATABASE_URL")
            if not url:
                raise ValueError(
                    "DATABASE_URL environment variable not set. "
                    "Expected format: postgresql://user:password@host:port/database"
                )
            
            self.read_pool = await asyncpg.create_pool(
                url,
                min_size=2,         # Keep 2 connections warm
                max_size=10,        # Never exceed 10 connections
                command_timeout=30  # Kill queries after 30 seconds
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
        """
        Close both connection pools gracefully.
        Always call this on shutdown to prevent connection leaks.
        """
        if self.read_pool:
            await self.read_pool.close()
            self.read_pool = None
            logger.info("Read pool closed")
        if self.write_pool:
            await self.write_pool.close()
            self.write_pool = None
            logger.info("Write pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """
        Yield a connection from the READ pool for query operations.
        
        Usage:
            async with db.get_connection() as conn:
                rows = await conn.fetch("SELECT * FROM users")
        
        The connection is automatically returned to the pool
        when the context manager exits.
        """
        if not self.read_pool:
            await self.connect()
        
        async with self.read_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self):
        """
        Yield a connection with an explicit transaction from the WRITE pool.
        
        This ensures atomic execution:
        - All operations succeed together (COMMIT)
        - Or all operations fail together (ROLLBACK)
        
        Usage:
            async with db.transaction() as conn:
                await conn.execute("INSERT INTO users ...")
                await conn.execute("UPDATE accounts ...")
            # Auto-commits here if no exception
            # Auto-rolls back if any exception raised
        
        Why use transactions for writes?
        - Prevents partial updates (e.g., money debited but not credited)
        - Automatic cleanup on errors
        - Database integrity maintained
        """
        if not self.write_pool:
            await self.connect()
        
        async with self.write_pool.acquire() as conn:
            # Start explicit transaction block
            async with conn.transaction():
                yield conn
            # Commits automatically when exiting without exception
            # Rolls back automatically if exception raised


# Global instance - shared across all tool calls
db = DatabaseManager()
