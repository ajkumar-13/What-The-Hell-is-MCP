# Code snippets

Patterns this team uses often enough that they should be copied rather than
reinvented. Each one is here because a review comment kept asking for it.

## Database pool

One pool per process, created at startup and closed at shutdown. Creating a
pool per request is the single most common cause of a saturated connection pool
on the gateway dashboard.

```python
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=2,
            max_size=10,
            command_timeout=5,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
```

`command_timeout` is not optional. A query with no timeout holds a connection
until the network notices, which can be minutes.

## Idempotency key

Wrap any handler that a client is allowed to retry. The key is supplied by the
caller and scoped per merchant, so two merchants can use the same key without
colliding.

```python
import functools
import hashlib


def idempotent(scope: str):
    """Return the stored result for a repeated key instead of re-running."""

    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(request, *args, **kwargs):
            key = request.headers.get("Idempotency-Key")
            if not key:
                return await handler(request, *args, **kwargs)

            fingerprint = hashlib.sha256(
                f"{scope}:{request.merchant_id}:{key}".encode()
            ).hexdigest()

            stored = await load_result(fingerprint)
            if stored is not None:
                return stored

            result = await handler(request, *args, **kwargs)
            await store_result(fingerprint, result)
            return result

        return wrapper

    return decorator
```

Store the result, not just the key. A stored key with no result turns a retry
into a silent no-op, which is worse than the double payment it was meant to
prevent.

## Retry with backoff

For calls out to a third party. Never wrap a database write in this: retrying a
write that may have succeeded is what the idempotency key is for.

```python
import asyncio
import random


async def with_backoff(call, attempts: int = 4, base: float = 0.25):
    """Retry `call` with exponential backoff and full jitter."""
    for attempt in range(attempts):
        try:
            return await call()
        except TransientError:
            if attempt == attempts - 1:
                raise
            delay = base * (2**attempt)
            await asyncio.sleep(random.uniform(0, delay))
```

Full jitter, not fixed backoff. Fixed backoff synchronizes every client that
failed at the same moment and reproduces the outage on the retry.

## Outbox write

The pattern that keeps the settlement stream consistent with the books. The
ledger entry and the outbox row are written in one transaction; a separate
publisher drains the outbox.

```python
async def record_transfer(conn, transfer) -> None:
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO entries (account, amount, transfer_id) VALUES ($1, $2, $3)",
            transfer.debit_account,
            -transfer.amount,
            transfer.id,
        )
        await conn.execute(
            "INSERT INTO entries (account, amount, transfer_id) VALUES ($1, $2, $3)",
            transfer.credit_account,
            transfer.amount,
            transfer.id,
        )
        await conn.execute(
            "INSERT INTO outbox (topic, payload) VALUES ($1, $2)",
            "settlements",
            transfer.to_json(),
        )
```

There is no publish call in that function, and that is the point. Publishing
inside the transaction would let the message escape a rolled back transfer.

## Structured log line

Logs go to stderr as JSON, one object per line. The fields below are the ones
the dashboards query, so spell them exactly this way.

```python
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname.lower(),
            "service": record.name,
            "message": record.getMessage(),
            "merchant_id": getattr(record, "merchant_id", None),
            "request_id": getattr(record, "request_id", None),
        }
        return json.dumps({k: v for k, v in payload.items() if v is not None})


handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(JsonFormatter())
logging.basicConfig(handlers=[handler], level=logging.INFO)
```

Never log a full request body. It carries card data often enough that the rule
has to be absolute rather than conditional.
