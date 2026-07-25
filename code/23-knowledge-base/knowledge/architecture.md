# Architecture

What the Orchard platform is made of, why it is shaped that way, and which
boundaries are load-bearing.

## The four services

Orchard is deliberately small. Four services, one relational database, one
ordered message stream. Everything else is a library.

| Service | Owns | Talks to |
|---|---|---|
| `gateway` | Public HTTP, authentication, rate limits | `ledger`, `catalog` |
| `ledger` | Balances, transfers, the double-entry books | Postgres primary |
| `catalog` | Products, prices, merchant records | Postgres replica |
| `reconciler` | Matching settlements against the books | Stream, Postgres primary |

The rule that keeps this honest: only `ledger` and `reconciler` write to the
primary. `gateway` and `catalog` are readers. When somebody proposes a fifth
service, the first question is which of those two roles it takes.

## The request path

A customer request crosses four hops and no more. Anything longer is a design
mistake and is caught in review.

    client -> edge load balancer -> gateway -> ledger -> Postgres primary

The edge terminates TLS and does nothing else interesting. The gateway
authenticates, applies the per-merchant rate limit, and forwards. The ledger
does the actual work inside one database transaction. There is no queue on the
synchronous path, on purpose: a customer waiting for a payment result should
never be waiting on a consumer that might be lagging.

Asynchronous work hangs off the side. When the ledger commits a transfer it
also writes a settlement record to the `settlements` stream in the same
transaction, using the outbox pattern. The reconciler consumes that stream.
Because the write and the outbox row share a transaction, a settlement can
never exist without its ledger entry.

## Data stores

**Postgres** is the only durable store for anything that matters. One primary,
two read replicas, managed failover. The primary is sized for write throughput
and is deliberately not used for reporting.

**The stream** holds settlements and audit events. It is ordered per merchant
and retains fourteen days. It is not a database, and nothing reads it to answer
a customer question.

**Redis** holds rate-limit counters and nothing else. It is treated as
disposable. If Redis is empty after a restart, the worst outcome is that a
merchant briefly gets a more generous rate limit than they paid for.

## Where the boundaries are

Three boundaries are load-bearing, meaning a change that crosses them needs a
design note rather than a pull request.

1. **The public schema.** The gateway's HTTP contract is versioned and public.
   Removing a field is a breaking change even if you believe nobody reads it.
2. **The ledger invariants.** Every transfer is double entry and every account
   balance is derivable by replaying entries. No code outside the ledger writes
   to the entries table, ever.
3. **The migration compatibility window.** Every migration must work against
   both the previous and the current application release, because deployment is
   rolling and rollback must stay possible. This is what makes the rollback step
   in the runbooks safe.

## Deployment topology

Three environments, all built from the same images.

- **Local**, started by `orchard dev up`. Containers on your laptop.
- **Staging**, deployed automatically on merge to `main`. Shared, real data
  shapes, synthetic money.
- **Production**, promoted by hand from a staging build that has been running
  for at least one hour.

Promotion never rebuilds. The artifact that ran in staging is the artifact that
runs in production, identified by digest. If you find yourself wanting to
rebuild for production, something in the pipeline is wrong.

## What we deliberately do not have

Written down because these questions come up in every design review.

- **No service mesh sidecar per pod.** Mesh certificates are issued and rotated
  centrally; the routing is done at the edge and by the client libraries.
- **No microservice per table.** The four services are drawn around ownership of
  invariants, not around nouns.
- **No shared database access across service boundaries.** `catalog` reading the
  ledger's tables directly would be faster and would also make the ledger
  invariants unenforceable.
- **No cross-service distributed transactions.** The outbox pattern plus an
  idempotency key is the whole consistency story, and it is enough.
