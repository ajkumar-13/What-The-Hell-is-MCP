# src/database.py - Connection Pool Manager
# Blog 5: Secure Database Analyst
# Manages a pool of PostgreSQL connections for efficient access

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
    Manages a pool of PostgreSQL connections.
    
    Why pooling?
    - Opening a new connection per query is slow (~50-100ms overhead)
    - Too many connections can crash your database
    - Pool reuses connections efficiently (~1ms to acquire)
    """
    
    def __init__(self):
        self.pool = None

    async def connect(self):
        """
        Initialize the connection pool.
        
        Pool settings:
        - min_size: Minimum connections to keep open (prevents cold starts)
        - max_size: Maximum connections allowed (prevents DB overload)
        - command_timeout: Query timeout to prevent runaway queries
        """
        if not self.pool:
            url = os.getenv("DATABASE_URL")
            if not url:
                raise ValueError(
                    "DATABASE_URL environment variable not set. "
                    "Expected format: postgresql://user:password@host:port/database"
                )
            
            self.pool = await asyncpg.create_pool(
                url,
                min_size=2,         # Keep 2 connections warm
                max_size=10,        # Never exceed 10 connections
                command_timeout=30  # Kill queries after 30 seconds
            )
            logger.info("Database pool initialized")

    async def close(self):
        """
        Close the connection pool gracefully.
        Call this on server shutdown to release database resources.
        """
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Database pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """
        Yield a connection from the pool.
        
        Usage:
            async with db.get_connection() as conn:
                rows = await conn.fetch("SELECT * FROM users")
        
        The connection is automatically returned to the pool
        when the context manager exits.
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            yield conn


# Global instance - shared across all tool calls
# This ensures we have one pool for the entire server lifetime
db = DatabaseManager()
