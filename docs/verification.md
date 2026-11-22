# Verification evidence

Verified locally on **November 22, 2026**. This records observed execution, not predicted CI results or a production certification.

## Executed successfully

| Check | Observed result |
| --- | --- |
| Python 3.14.9 pytest suite with an actual isolated Redis 6.0.16 service | **30 passed in 9.04 seconds** |
| Python 3.12.16 pytest suite, matching the Docker Python minor version, with real Redis | **30 passed in 8.79 seconds** |
| HTTP end-to-end harness on both Python versions | All eight check groups passed |
| `python -m compileall -q app alembic scripts tests tools` | Exit 0 |
| Development environment `pip check` | No broken requirements |
| `python scripts/validate_deploy.py` | 13 YAML files parsed; deployment contracts and isolated environment/secret/token-helper checks passed |
| Docker Compose **5.7.0** CLI `config --quiet` | Default and monitoring profiles both passed, using generated temporary credentials |
| Prometheus **3.5.0** `promtool check config --syntax-only deploy/prometheus/prometheus.yml` | Valid configuration syntax |
| `promtool check rules deploy/prometheus/alerts.yml` | Five alert rules validated |
| Git secret/runtime exclusions | `.env`, `.secrets/metrics/token`, `.venv/`, and `.runtime/` excluded |
| Walkthrough media | H.264/AAC MP4, **246.816 seconds**, 4,223,458 bytes; complete ffmpeg decode exit 0 |

The standalone Compose and promtool downloads were SHA-256 checked against their upstream published checksum files. Downloading the Compose CLI does **not** provide a Docker daemon.

### What the API tests exercise

- Password hashing, signed JWT validation and rejection of a weak signing key.
- Authentication, admin/user/read-only authorization, disabled users and immediate server-side role changes.
- Versioned CLI migration and repeat-upgrade behavior on SQLite; secure user provisioning.
- Chat metadata persistence, opt-in raw-content persistence, default content privacy, user-isolated caching and validation.
- Redis failure, database failures, rate limits, capacity rejection and lease cleanup. A cleanup-outage regression verifies that release errors are sanitized/logged without replacing completed work, and that the lease expires.
- Transient upstream retry, fallback, permanent upstream rejection, malformed responses, per-attempt timeout and total gateway deadline. Regression tests verify that a stalled primary leaves reserved fallback time and that a connection failure can reach the fallback.
- Sanitized request logging, token counters and bounded route-label cardinality. Unexpected 500 failures remain visible in counters, latency and structured request events without logging exception text. Optional post-commit cache-population failures preserve successful chat responses and their single audit record.
- Actual Redis Lua atomic rate limiting, shared concurrency admission/release and crashed-lease expiry. The opt-in tests use unique namespaces and never flush Redis databases.

### What the separate HTTP harness exercises

`tools/e2e_local.py` starts actual Uvicorn and an explicitly labeled loopback HTTP provider fixture, uses actual Redis and temporary SQLite, then checks:

1. CLI migration and repeated upgrade.
2. Three provisioned account roles with generated credentials.
3. Dependency-aware HTTP readiness.
4. JWT login, anonymous rejection and RBAC over HTTP.
5. The upstream HTTP transport, usage accounting and per-user response-cache isolation.
6. Administrator-authorized Prometheus scraping.
7. Durable privacy-default audit records and role changes taking effect without replacing the JWT.
8. Absence of the generated password and test question from server logs.

The harness terminates its own subprocesses and removes its temporary database. The upstream returns a deterministic fixture answer: this is **transport and integration testing, not LLM inference**.

## Reproduce

In an isolated Python environment, install `requirements-dev.txt`. With a disposable Redis instance available:

```bash
REDIS_INTEGRATION_URL=redis://127.0.0.1:6379/15 python -m pytest -q
python tools/e2e_local.py --redis-url redis://127.0.0.1:6379/14
python scripts/validate_deploy.py
python -m compileall -q app alembic scripts tests tools
```

The regular suite does not require paid provider access. If `REDIS_INTEGRATION_URL` is absent, the explicit external-Redis check skips; that is not equivalent to the real-Redis run above. Keep test Redis separate from production.

For deployment CLI validation, generate `.env` using `scripts/setup_env.py`, then:

```bash
docker compose config --quiet
docker compose --profile monitoring config --quiet
promtool check config --syntax-only deploy/prometheus/prometheus.yml
promtool check rules deploy/prometheus/alerts.yml
```

## Not executed here

- **Docker image build or container stack startup.** No Docker daemon is available on this host. Compose schema/interpolation validation is not a container runtime test.
- **PostgreSQL runtime integration or migration.** Database-dependent application tests and the HTTP harness used SQLite. PostgreSQL/asyncpg and migration code are supplied but must be exercised against the target PostgreSQL service before deployment.
- **Kubernetes deployment, ingress/network-policy behavior, HPA operation or failover.** Manifests were parsed and statically checked only; managed-service, CNI, ingress-controller, image and environment-specific configuration are required.
- **Real provider inference.** No paid API credentials or open-model runtime were supplied. Provider HTTP behavior was tested with mocks and the loopback fixture.
- **100/500 RPS load, provider quota capacity, cost or availability claims.** Architecture numbers are illustrative sizing calculations, not measured throughput.
- **Hosted CI, production alert delivery, OIDC integration, restore drills or cross-region disaster recovery.** Workflow and operational plans are included; those outcomes are not claimed.

## Boundaries

The code is a substantial assessment implementation, not a claim that the target environment is production-certified. Before public deployment: run the full Compose stack and real PostgreSQL migration, verify a real provider, configure trusted proxy handling and edge login limits, complete TLS/secrets/egress configuration, exercise restore and rollback, validate the intended load against provider budgets, and review dependency/security updates.
