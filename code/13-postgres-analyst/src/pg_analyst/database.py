"""Connection pools, and the reason there are two of them.

Post 13 opens one pool against a role that can only read. Post 14 adds a second
pool against a role that can also write, and the split is the point: a bug in
the query path cannot reach a connection that is allowed to modify anything,
because that connection is on the other pool, behind a different login, with
different grants.

Nothing here reads the environment at import time. Every lookup is a call, so
`load_dotenv()` in app.py is guaranteed to have run first no matter what order
Python happens to import these modules in.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

log = logging.getLogger("postgres-analyst.database")

# Belt and braces with the role-level `statement_timeout` in
# sql/002-readonly-role.sql. The server-side setting is the one that matters,
# because it survives a client that has stopped listening; this one just makes
# the tool return a clear error instead of hanging the host's UI.
READ_TIMEOUT_SECONDS = 30.0
WRITE_TIMEOUT_SECONDS = 30.0


class DatabaseNotConfigured(RuntimeError):
    """Raised when a tool needs a pool that was never opened."""


def writes_enabled() -> bool:
    """Whether this deployment is allowed to modify anything at all.

    Read at call time, not at import time, so a test can flip it and so an
    operator can flip it without editing code. This is the outermost of the
    write-path gates and the cheapest to reason about: if it is off, no amount
    of clever SQL gets anywhere near the write pool.
    """
    return os.getenv("ENABLE_WRITES", "false").strip().lower() == "true"


class Database:
    """Two pools, one process.

    - `read_pool` is opened against `DATABASE_URL`, which should name a role
      that holds nothing but `SELECT`.
    - `write_pool` is opened against `DATABASE_WRITE_URL`, which should name a
      separate role holding `INSERT`, `UPDATE`, and `DELETE` on exactly the
      tables you meant.

    There is deliberately **no fallback** from `DATABASE_WRITE_URL` to
    `DATABASE_URL`. An earlier version had one, and it quietly undid the whole
    design: on a machine where only `DATABASE_URL` was set, the "write pool"
    was the read-only role, and the first write failed at the database with a
    confusing permissions error instead of at startup with a clear one.
    """

    def __init__(self) -> None:
        self._read_pool: asyncpg.Pool | None = None
        self._write_pool: asyncpg.Pool | None = None

    # -- configuration ----------------------------------------------------

    @property
    def read_url(self) -> str | None:
        return os.getenv("DATABASE_URL") or None

    @property
    def write_url(self) -> str | None:
        return os.getenv("DATABASE_WRITE_URL") or None

    @property
    def reads_configured(self) -> bool:
        return self._read_pool is not None

    @property
    def writes_configured(self) -> bool:
        return self._write_pool is not None

    # -- lifecycle --------------------------------------------------------

    async def connect(self) -> None:
        """Open whichever pools this deployment is configured for.

        A missing `DATABASE_URL` is a warning rather than an exception, so the
        server still starts and every tool can explain what is wrong. A URL
        that is present but wrong is a different matter: that exception
        propagates and the server refuses to start, which is what you want.
        """
        if self._read_pool is None:
            url = self.read_url
            if url is None:
                log.warning(
                    "DATABASE_URL is not set. The server is starting without a "
                    "read pool and every database tool will refuse to run."
                )
            else:
                self._read_pool = await asyncpg.create_pool(
                    url,
                    min_size=1,
                    max_size=10,
                    command_timeout=READ_TIMEOUT_SECONDS,
                )
                log.info("read pool open (max_size=10)")

        if self._write_pool is None and writes_enabled():
            url = self.write_url
            if url is None:
                log.warning(
                    "ENABLE_WRITES is true but DATABASE_WRITE_URL is not set. "
                    "The write tool will refuse to run."
                )
            else:
                # Writes are rarer and more expensive than reads, and a small
                # ceiling here bounds how much damage a runaway loop can do
                # before the pool starts queueing.
                self._write_pool = await asyncpg.create_pool(
                    url,
                    min_size=1,
                    max_size=4,
                    command_timeout=WRITE_TIMEOUT_SECONDS,
                )
                log.info("write pool open (max_size=4)")

    async def close(self) -> None:
        if self._read_pool is not None:
            await self._read_pool.close()
            self._read_pool = None
            log.info("read pool closed")
        if self._write_pool is not None:
            await self._write_pool.close()
            self._write_pool = None
            log.info("write pool closed")

    # -- acquiring --------------------------------------------------------

    @asynccontextmanager
    async def read_connection(self) -> AsyncIterator[asyncpg.Connection]:
        """A connection from the read-only pool."""
        if self._read_pool is None:
            raise DatabaseNotConfigured(
                "DATABASE_URL is not set, so this server has no read pool. "
                "Point it at a PostgreSQL role that holds SELECT and restart."
            )
        async with self._read_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def write_connection(self) -> AsyncIterator[asyncpg.Connection]:
        """A write-pool connection with **no** transaction wrapped around it.

        Two callers need this, and both of them need it to be the write pool:

        - `ensure_audit_table()`, which issues `CREATE TABLE IF NOT EXISTS`.
        - the failure path in `write_database`, which has to record a rolled
          back attempt on a fresh connection, because the connection the
          attempt was made on has already rolled back and taken any audit row
          written inside it along with it.
        """
        if self._write_pool is None:
            raise DatabaseNotConfigured(
                "DATABASE_WRITE_URL is not set (or ENABLE_WRITES is false), so "
                "this server has no write pool."
            )
        async with self._write_pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """A write-pool connection inside an explicit transaction.

        Commits on a clean exit, rolls back on any exception. The audit insert
        for a successful write happens on this same connection, inside this
        same transaction, so the row and the change it describes either both
        land or neither does.
        """
        if self._write_pool is None:
            raise DatabaseNotConfigured(
                "DATABASE_WRITE_URL is not set (or ENABLE_WRITES is false), so "
                "this server has no write pool."
            )
        async with self._write_pool.acquire() as conn:
            async with conn.transaction():
                yield conn


# One instance, shared by every tool and resource in the package.
db = Database()
