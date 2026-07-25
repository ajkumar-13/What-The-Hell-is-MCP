# Runbooks

One runbook per thing that has actually woken somebody up. Each has the same
shape: what you will see, what to check, what to do, and when to escalate.

A runbook is a starting point, not a script. If the situation does not match the
symptom, stop following the runbook and say so in the incident channel.

## Elevated 5xx rate at the gateway

**Symptom.** The `gateway_5xx_ratio` alert fires. Customers see failed requests
and the status page has not been updated.

**Check.** Open the gateway dashboard and answer three questions in order.

1. Is the error rate on every route, or one route? One route means a single
   downstream service; every route means the gateway itself or the database.
2. Did a deployment land in the last thirty minutes? The deployment marker is
   drawn on the dashboard as a vertical line.
3. Is the database connection pool saturated? The `pool_in_use` metric sitting
   flat at the pool maximum is the single most common cause.

**Do.** If a deployment landed and the error rate started at the marker, roll
back first and diagnose afterwards:

    orchard release rollback --service gateway --to previous

Rollback takes about forty seconds and is always safe: every migration in this
codebase is required to be backward compatible with the previous release.

If the pool is saturated and no deployment landed, something is holding
connections. Find the long-running queries, and cancel the worst offender only
after you have recorded it:

    orchard db slow-queries --minutes 15
    orchard db cancel --pid <pid>

**Escalate.** If the error rate is above ten percent for more than ten minutes,
page the platform group secondary and update the status page. Do not wait until
you have a diagnosis to update the status page.

## Database failover

**Symptom.** Writes fail with a read-only transaction error while reads keep
working. The `db_primary_healthy` alert fires.

**Check.** Confirm which instance the cluster believes is primary:

    orchard db topology

A managed failover normally completes on its own in under sixty seconds. What
you are checking is whether it completed, not whether it happened.

**Do.** If the topology shows a primary and the application still cannot write,
the application is holding stale connections. Recycle the pools:

    orchard release restart --service ledger --service reconciler

Restarting the gateway is not necessary; the gateway does not write.

If the topology shows no primary after two minutes, promote the healthiest read
replica by hand. This is the one step in this document that loses data, because
replication is asynchronous. Record the replication lag before you promote:

    orchard db replica-lag
    orchard db promote --replica <name> --confirm

**Escalate.** Any manual promotion is a reportable incident. Page the payments
group immediately, because the reconciliation job has to be re-run for the
window covered by the lag.

## Expired TLS certificate

**Symptom.** Clients report certificate validation failures. Internal calls
between services start failing at the same moment for everybody, which is the
signature that distinguishes an expired certificate from a partial outage.

**Check.** Read the expiry from the edge and from the internal mesh separately.
They are issued by different authorities and they expire on different days.

    orchard tls expiry --edge
    orchard tls expiry --mesh

**Do.** Edge certificates renew automatically thirty days before expiry. If one
expired, the renewal job failed silently, so check its last run before you do
anything else. Then force a renewal and reload:

    orchard tls renew --edge
    orchard release reload --service gateway

Mesh certificates are issued by the internal authority with a ninety-day life
and are rotated by the same command with `--mesh`. Rotation is a rolling
restart, so it is safe during business hours.

**Escalate.** If renewal fails because the authority rejects the request, the
account credentials in the vault have probably expired too. That is a vault
problem, not a certificate problem, and it goes to the platform group.

## Reconciliation backlog

**Symptom.** The `reconciler_lag_minutes` alert fires. No customer sees anything
yet, which is exactly why this one gets ignored until it is expensive.

**Check.** The reconciler is a single consumer reading an ordered stream. Lag
means either the consumer is slow or the stream is unusually full.

    orchard queue depth --topic settlements
    orchard release logs --service reconciler --minutes 30

**Do.** A poison message is the usual cause: one record the consumer cannot
process, retried forever. The logs name it. Move it aside and let the stream
drain:

    orchard queue quarantine --topic settlements --offset <offset>

Never delete a settlement record. Quarantine copies it to a durable side topic
where the payments group can replay it after the fix.

**Escalate.** Lag above four hours crosses into the daily settlement window.
Page the payments group regardless of the hour.

## After any incident

Write the timeline while it is still fresh, ideally within the hour. The
timeline is five bullet points, not an essay: when it started, how it was
detected, what was tried, what fixed it, and what would have caught it sooner.

The review is blameless and scheduled within five working days. Its only output
is a list of actions with owners. An action nobody owns is a wish.
