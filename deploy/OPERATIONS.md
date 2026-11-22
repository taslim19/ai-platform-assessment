# Deployment and operations

## Local Compose (development / single-host assessment)

Prerequisites: Docker Engine with current Compose v2, Python 3.12+, and an LLM provider key. This stack is not an HA production database deployment. Images are version-tagged examples; production must pin reviewed digests and scan them.

```sh
python3 scripts/setup_env.py
# Edit .env: set LLM_API_KEY (and any desired model/provider settings).
docker compose up -d --build --scale api=2
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health
# Secure interactive password prompt; no default account/password exists.
docker compose exec api python -m app.cli create-user --username operator --role admin
```

The database migration runs once before API startup. Redis and PostgreSQL require passwords, persist data to named volumes, and have **no published host ports**. API replicas have no host port or fixed container name; nginx publishes only loopback `127.0.0.1:8080`. Use an SSH tunnel remotely. Put a hardened TLS edge in front before exposing to untrusted networks. Nonroot nginx uses Docker DNS and resolves `api` every five seconds; scale without editing upstream addresses:

```sh
docker compose up -d --scale api=4
# Rerun migration for a NEW release (serialize release jobs, back up first):
docker compose build
docker compose run --rm migrate
docker compose up -d --scale api=4
```

Compose is not a rolling deployment orchestrator; expect possible interruption during recreation. Run the migration exactly once per release before introducing new replicas. The CLI applies versioned Alembic migrations to head, with the initial schema revision included in the image. Review ordered migrations before schema evolution, using expand/contract changes so old/new versions coexist. Do not run destructive schema changes with old code serving.

Configuration is runtime `.env` input, not baked into images. `scripts/setup_env.py` creates a mode-0600 file with independent cryptographically random Redis, PostgreSQL, and JWT credentials and refuses to overwrite an existing file. `.env` is deliberately excluded from Docker context. Changing the environment PostgreSQL password does **not** change the password in an initialized data volume: rotate in PostgreSQL first, then update consumers. Keep `.env` and `.secrets/` out of git, screenshots, PDFs, CI artifacts, and support bundles.

API containers run as uid/gid 10001 with read-only root filesystem, no Linux capabilities, an isolated writable `/tmp`, SIGTERM shutdown, and a 55-second graceful request drain inside a 60-second Compose stop window. Uvicorn does not trust caller-supplied proxy headers by default; JWT authentication is not dependent on source IP. Configure only explicit trusted proxy CIDRs if client IP forwarding is needed. The default login limit is keyed by the direct peer: without trusted forwarding, users behind the same proxy share a login bucket. See the README before an externally exposed deployment. Liveness does not depend on the LLM; readiness should check PostgreSQL/Redis. Scale limits are provider quota, connection pools, cost, and concurrency—not just CPU.

## Authenticated metrics (optional monitoring profile)

`/metrics` stays admin-Bearer protected; there is no unauthenticated metrics exception. Create a **dedicated** admin account because the application currently has no narrower metrics-only role. Keep that password private and restrict network access. This account can perform admin operations: treat its token as privileged.

```sh
docker compose exec api python -m app.cli create-user --username metrics-reader --role admin
# Login through the real /auth/login endpoint and write the bearer file.
# Prompts for a password; never put it in shell command arguments.
python3 scripts/refresh_metrics_token.py --username metrics-reader
# Start optional monitoring only after the bearer file exists.
docker compose --profile monitoring up -d prometheus
# In another supervised host process, keep rotating before access-token expiry.
python3 scripts/refresh_metrics_token.py --username metrics-reader --watch
```

Prometheus is available only at `127.0.0.1:9090`. The script supports `METRICS_USERNAME` / `METRICS_PASSWORD` for a secret-injected supervisor, but interactive input is preferable for a local demonstration. Default refresh is every 120 seconds; keep it below `JWT_EXPIRY_MINUTES * 60`, allowing retry margin. Tokens have the application's normal expiration. A failed refresh retains the old token, reports only the exception class, and retries in watch mode. Without a healthy refresh process, scrape authentication eventually fails. Monitor that process separately; the included scrape alert cannot notify anyone until an Alertmanager / receiver is configured.

The **directory** `.secrets/metrics` is bind-mounted read-only, not the token file, so atomic token replacement is visible inside the container. The token is readable by Prometheus uid 65534; its directory is traversable inside the mount while `.secrets` is private to the host owner. Docker daemon operators inherently have access. Prometheus discovers all API replica IPs through DNS, not through nginx; scraping the load balancer would miss per-worker metrics. Dashboards aggregate across instances. Alerts are loaded, but Alertmanager/contact routing must be supplied before claiming on-call delivery.

## Capacity and operational gates

- Start with two API replicas. `LLM_MAX_CONCURRENCY=20` is a **Redis-coordinated global limit** shared across all replicas using the same Redis database/namespace. Four replicas still admit at most 20 simultaneous gateway operations, each of which may include sequential retries/fallback. Keep this setting identical across replicas. It does not implement provider RPM/TPM reservations, account for unrelated applications, or coordinate separate Redis deployments.
- At 500 RPS and two seconds average upstream latency, Little's Law implies about 1,000 concurrent LLM requests without cache/offloading. Default 20 global concurrency across two replicas is intentionally not a promise of 500 LLM RPS. Obtain provider request/token quotas and measure p95/p99, 429/5xx, inflight, DB pool wait, Redis RTT, token usage and cost before raising limits.
- Request-rate limits are user-scoped and Redis-backed; repeat tests with representative user counts and authenticated requests. Cache defaults to disabled (`CACHE_TTL_SECONDS=0`), avoiding accidental correctness/privacy assumptions. Cache keys are HMAC-derived and user-isolated in the application; enabling cache still requires a data classification and retention review.
- Bound DB connections: `replicas * workers * pool_size` plus overflow, migrations, monitoring, and admin headroom must remain below the managed database limit. Use a compatible pooling tier when needed. Keep one uvicorn worker per container and scale containers.
- Watch `/health`, token refresh failures, dependency errors and provider quotas. Provider outages should not trigger process restart storms. Review PII retention; logs must not contain bearer tokens, API keys, user passwords or full prompts.

## Backups, restore, upgrade and rollback

For the assessment stack, make an encrypted off-host logical backup with a restricted destination:

```sh
umask 077
mkdir -p .secrets/backups
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > .secrets/backups/aiqa.dump
# Restore ONLY to an isolated recovery database first. This does not destroy the active DB.
# After creating a separate recovery DB, pipe the dump to pg_restore using its URL.
```

Verify dump completion, checksum it, apply retention, and rehearse restore into isolated infrastructure. Never imply that a dump alone proves recovery. For production managed PostgreSQL, enable encrypted PITR and Multi-AZ; specify/test RPO/RTO and retention. Redis AOF is useful for local resilience but not an HA design; managed Redis replication/failover is required in production. Losing cache/rate-limit state has different correctness/security implications from losing persistent user/conversation records.

Rollback to the previous immutable application image only if its schema compatibility is preserved. Database restore is a controlled recovery event with explicit data-loss impact, not a routine release rollback. Back up before migration, validate in staging, canary, and observe error/latency/cost gates. Compose `down` retains volumes; `docker compose down -v` irreversibly deletes local PostgreSQL/Redis/Prometheus volumes—do not use it for ordinary restart.

## Validation boundaries

Run `python3 -m pip install PyYAML==6.0.2` in an isolated tooling environment, then `python3 scripts/validate_deploy.py` for YAML/contract and isolated helper checks. This deliberately keeps deployment tooling out of application runtime requirements. The helper tests use a local fixture HTTP server to test login payload, admin metrics verification, atomic rotation and failed-refresh preservation; they do not claim a real application/container integration test. `make check` additionally requires GNU Make and the Docker Compose CLI.

Static YAML parsing, script checks, application unit tests, Docker builds, and real multi-service integration are separate evidence levels. The implementation environment has no Docker daemon/CLI or Kubernetes cluster; do not label the stack runtime-tested on that basis. CI defines Linux/Docker build and syntax validation. Real deployment acceptance must include migrations, account creation/login, two-replica routing, token refresh, PostgreSQL/Redis failure/recovery, TLS ingress and graceful rolling updates. CI success is not a 500-RPS benchmark.
