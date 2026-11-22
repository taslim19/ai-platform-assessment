# Operations and incident runbook

## Before exposing the service

1. Set unique generated database/Redis/JWT secrets and a real provider key. Never commit `.env`, a metrics token file, a local database, or provider responses.
2. Terminate TLS at a trusted ingress, restrict network access, set allowed ingress policy, and use a provider endpoint approved for the data classification. The Compose HTTP listener is a local development front end, not an internet-ready TLS configuration.
3. Run migrations once, create the first administrator through the CLI, and provision ordinary users separately. Disable/remove unused accounts promptly. Use OIDC for production lifecycle management.
4. Confirm `/health/live` and dependency-aware `/health`, an authenticated chat, denied anonymous chat, and denied non-admin metrics.
5. Confirm Prometheus can scrape `/metrics` with its protected admin token. Refresh the token before expiry; automate renewal or replace with a scoped workload identity before production.
6. Set per-user admission limits, global concurrency, total deadline, retry count, provider quotas and budget alerts together. Do not tune one without considering the others.
7. Configure backup retention/PITR, restore drills, dashboard ownership, alert routing, and pager escalation. The example alert rules do not wire up a paging receiver by themselves.
8. Keep the database and Redis on private networks; require TLS for managed data services. Never print configuration containing credentials during incident collection.

## Useful commands

```bash
curl --fail http://localhost:8080/health/live
curl --fail http://localhost:8080/health
docker compose ps
docker compose logs --tail=100 api nginx
docker compose up -d --scale api=3
```

Only use generated configuration and commands from the README. Logs are intentionally content-minimal. Keep a failure's returned request ID, UTC timestamp, route, and status code; do not paste a bearer token or prompt into a ticket.

## Elevated latency / upstream errors

- Separate total API latency from provider latency, admission rejection, DB latency, and pending requests. Compare primary and fallback behavior.
- Provider 429: lower admission, inspect account RPM/TPM/concurrency and retries, and check spend/usage. Adding replicas may worsen the problem. Preserve Retry-After semantics rather than retrying immediately.
- Provider 401/403: verify runtime secret delivery, endpoint, model access, and rotation history. Do not retry a permanently invalid credential.
- Timeouts/5xx: inspect provider status and bounded fallback. Repeated retries consume extra quota and may double bill a generation whose response was lost.
- If a deployment correlates with the failure, roll back to the prior immutable image only while the schema/data remains compatible. Do not roll back a database by deleting or recreating its volume.

## Redis incident

- New generation admission fails closed if shared controls cannot be checked. This trades availability for budget/provider protection.
- Inspect Redis health, connection limits, authentication, memory/evictions, latency, and network path. Cache eviction is acceptable; rate-limit/lease state eviction has a different risk profile. For a large system isolate controls and cache into different instances/policies.
- Never issue `FLUSHALL` as a casual remediation. Restart/failover can lose recent counters and leases; restore cautiously with a temporarily lower provider cap.
- Leases expire to recover capacity after crashes. A total deadline longer than lease TTL invalidates concurrency protection: treat those settings as a linked invariant.

## Database incident

- Readiness must fail when the app cannot query PostgreSQL. Verify the DB host, secret, pool limits, slow queries, locks, migration state, storage and managed-service events.
- Aggregate pool budget is approximately instances × processes × (pool size + overflow), plus migrations/admin connections. Preserve DB capacity for recovery tasks.
- A provider call may have succeeded before an audit write failed. Do not promise the client that an immediate retry is free or exactly-once. Use durable idempotency for workflows requiring that guarantee.
- Recover using the managed backup/PITR runbook and verify correctness before reopening writes. Record recovery time and data loss against defined RTO/RPO.

## Metrics unavailable

- Check whether the scrape admin JWT has expired; an authentication failure is not necessarily an API outage.
- Refresh credentials through the supplied helper, verify file permissions, and watch the next scrape. Never make `/metrics` public as a workaround.
- Alert on the Prometheus `up` signal and check that alerting itself is delivering. Production should use a dedicated, narrowly scoped service principal rather than a human administrator.

## Release / rollback

Use expand-and-contract schema changes. Build/test/scan the image, pin a release tag or digest, back up state, run the release migration job, deploy a small canary, evaluate latency/errors/spend/data, then increase traffic. Set termination grace above the request deadline and drain old instances. Keep old images, compatible schema, and a verified data rollback plan. Destructive schema contraction is a separate later release after the rollback window closes.

## Recovery exercises to perform before launch

- Stop one API instance under in-flight traffic; confirm lease recovery, client errors, and graceful shutdown.
- Cut Redis connectivity; confirm fail-closed behavior and controlled recovery.
- Force provider 429, 500, timeout, invalid credentials, malformed payload, and a slow fallback.
- Exhaust DB connections; confirm readiness and sanitized errors.
- Restore PostgreSQL to a disposable environment and verify users/audit counts.
- Rotate provider/JWT/DB/Redis secrets with a documented overlap/re-login plan.
- Load test the real deployment within approved quotas. The supplied tests and deployment manifests do not certify 500 RPS.
