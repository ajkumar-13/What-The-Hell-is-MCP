# Frequently asked questions

Questions that have been asked at least three times. If you had to ask, add the
answer here rather than answering it privately a fourth time.

## How do I get a database connection string?

From the vault, under `orchard/postgres/<environment>`. Never from a colleague,
never from a dashboard screenshot, and never from a previous project's `.env`
file, which is almost certainly stale.

Locally you do not need one at all. `orchard dev up` exports the local value
into the shell it starts, and the services read it from the environment.

## Why is my pull request not deploying to staging?

Deployment to staging is triggered by a merge to `main`, not by opening a pull
request. If your change is merged and staging still shows the old build, check
the pipeline: the most common cause is a failed image push, which shows as a
green test run and a red publish step.

## Can I run the tests without Docker?

Partially. The unit suite runs anywhere. The tests marked `integration` need
Postgres and the message broker, and they skip themselves automatically when
those are not reachable, so `uv run pytest` will pass on a machine with no
Docker while quietly testing much less.

Before you open a pull request, run the full suite with the stack up. A test
that was silently skipped is not a test that passed.

## What is an idempotency key and when do I need one?

Any write that a client might retry needs one. The client generates a unique
key per logical operation and sends it as a header; the ledger stores the key
with the result and returns the stored result on any repeat.

Without a key, a network timeout on a transfer is indistinguishable from a
failure, and the safe client behavior of retrying creates a double payment. The
snippets document has the exact decorator we use.

## Who approves a production release?

Anybody on the engineering team can approve a promotion, including the person
who wrote the change. The control is not the approver, it is the one hour of
staging soak and the automated checks that run before the promote button is
enabled.

Releases that touch the ledger are the exception and need an approval from
somebody in the payments group.

## How long is data kept?

Ledger entries are permanent. Settlement stream records are retained fourteen
days and are not the record of truth. Application logs are kept thirty days.
Traces are sampled and kept seven days.

If you need something older than the retention window, it has to come from the
ledger, which is why the ledger invariants matter.

## What do I do if I think I leaked a secret?

Rotate first, investigate second, and do both in the open. Say what you think
leaked in the incident channel, rotate the credential through the vault, and
then work out the exposure window. Nobody has ever been in trouble for the
rotation. The runbooks document has the rotation steps for each credential type.

## Why does the reconciler run behind?

It is designed to. The reconciler is asynchronous and lag of a few minutes is
normal and harmless. It only becomes a problem when the lag approaches the
daily settlement window, which is what the alert threshold is calibrated
against. The runbooks document covers what to do when it fires.

## Is there a staging pager?

No. Staging breaking is not an emergency, and paging on it trains people to
ignore pages. Staging failures raise a ticket in the team queue and are picked
up during the working day.
