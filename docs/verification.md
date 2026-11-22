# Verification evidence

Verified locally on **November 22, 2026**. These are observed executions, not predicted CI outcomes or a production certification. No user credentials or API keys are included in this report or its companion JSON.

## Executed successfully

| Check | Observed result |
| --- | --- |
| Python 3.14.9 pytest with a dedicated real Redis 7 container | **38 passed in 13.16 seconds** |
| Python 3.12.16 pytest, matching the Docker Python minor version, with real Redis | **38 passed in 12.83 seconds** |
| HTTP end-to-end fixture harness | All eight check groups passed; provider explicitly a fixture, SQLite database |
| Docker image build | Success with Python 3.12-slim; resulting API executed in Docker |
| Full Docker Compose stack | API, PostgreSQL **16.16**, Redis **7.4.12**, Nginx **1.28.3**, and optional Prometheus **3.5.0** running |
| PostgreSQL release migration | One-shot migration exit 0; database at Alembic revision `0001` |
| Real HTTP authentication and RBAC through Nginx | Three roles logged in; bad password/anonymous 401, read-only chat 403, user metrics 403, administrator metrics 200, invalid chat 422 |
| Real Gemini inference through the complete stack | **200**, answer `42`, actual model `gemini-3.5-flash`, uncached, request `f51eb8e6-a41c-4e06-b6de-37aa9d9cec39` |
| Real Redis response-cache hit | Same user/question returned cached answer in **0.0048 seconds**; no additional provider calls or consumed-token increments |
| Real PostgreSQL chat audit | Both original and cache-hit request persisted with **107 total tokens**; raw question and answer columns NULL |
| Real provider token metrics | Prompt 29, completion 2, total 107; preserves provider-reported reasoning-inclusive total |
| Controlled Redis pause | Readiness 503 in **2.004 seconds**, liveness 200, chat rejected 503; recovered after unpause |
| Controlled PostgreSQL pause on final rebuilt image | Readiness 503 in **3.002 seconds**, repeat 503 in **0.002 seconds**, liveness 200 in **0.0013 seconds**; readiness recovered after unpause |
| Final rebuilt-image inference/cache/audit regression | Actual Gemini 3.5 answer `42`, **94 provider-total tokens**, both audit rows private; cache hit **0.005 seconds**, no extra provider consumption |
| Redis security and policy | Unauthenticated command rejected `NOAUTH`; authenticated `PONG`; `maxmemory-policy=noeviction` |
| API container constraints | Non-root uid/gid 10001, read-only root filesystem, all Linux capabilities dropped |
| Live authenticated Prometheus scrape | Target `aiqa` reported **up**, including after API recreation and dependency recovery |
| `python -m compileall -q app alembic scripts tests tools` | Exit 0 |
| Development environment `pip check` | No broken requirements |
| `python scripts/validate_deploy.py` | 13 YAML files parsed; deployment contracts and isolated environment/secret/token-helper checks passed |
| Docker Compose **5.7.0** CLI `config --quiet` | Default and monitoring profiles passed |
| Prometheus `promtool check config --syntax-only deploy/prometheus/prometheus.yml` | Valid configuration syntax |
| `promtool check rules deploy/prometheus/alerts.yml` | Five alert rules validated |
| Git exclusions | `.env`, `.secrets/`, `.venv/`, and `.runtime/` excluded |
| Walkthrough media | H.264/AAC MP4, **246.816 seconds**, 4,223,458 bytes; complete ffmpeg decode exit 0 |

## Important provider result

The user-selected primary **Gemini 3.8 Flash** returned `503 UNAVAILABLE`, with Google's high-demand message. The same key successfully accessed model metadata (200); both the OpenAI-compatible generation endpoint and Google's native `generateContent` endpoint returned the capacity error. This is not a claim that 3.8 inference succeeded.

Keeping 3.8 as the primary, the configured **Gemini 3.5 Flash fallback** returned genuine generated text through Nginx -> API -> the external Google API, with Redis admission and PostgreSQL audit persistence. The measured response was **23.457 seconds wall time**, including the bounded primary attempt and fallback. It is not a standalone model latency benchmark. One generation consumed 107 provider-reported total tokens, of which 29 were reported as prompt and 2 as visible completion tokens; provider totals can include additional reasoning usage. The API, database and `type="total_tokens"` Prometheus series preserve that total rather than deriving it by adding visible components.

Caching is disabled by default. Only the synthetic cache verification used `CACHE_TTL_SECONDS=120`. A cache hit reuses the response's usage metadata, but does **not** increment actual consumed-token counters. Do not sum all token metric types together: select `type="total_tokens"` for aggregate consumption.

The sanitized request/usage and database evidence is in [local-stack-results.json](local-stack-results.json). It does not contain JWTs, account passwords, raw personal prompts or provider credentials.

## Readiness deadline regression

Pausing the actual PostgreSQL container exposed a problem hidden by immediate-error mocks: the original three-second `asyncio.timeout` could wait beyond the client's 60-second timeout because database cancellation cleanup had not completed. This was reproduced with a deterministic stalled-cleanup regression before fixing it.

Readiness now uses one tracked, single-flight dependency probe per worker. An independent timer requests cancellation once; HTTP callers wait only until the deadline and never await stalled cleanup. Subsequent callers fail ready immediately while that probe drains instead of spawning new probes. Seven regressions cover the production deadline, bounded single-flight behavior/recovery, sanitized dependency errors, disconnects and bounded probe shutdown, including cancellation-resistant cleanup. Python cannot force-stop an arbitrary coroutine; any stalled cleanup remains limited to the one tracked probe until it completes or the process exits. Liveness stays independent.

The final rebuilt container passed the actual pause/unpause test with the timings in the table above. A final genuine provider/cache/PostgreSQL roundtrip also passed after this fix; its exact request, usage and image ID are recorded under `final_build` in the JSON evidence.

## Test coverage

- Password hashing; signed JWT expiry/issuer/audience checks; rejection of weak signing keys.
- User/admin/read-only RBAC, disabled users and immediate server-side role changes.
- Versioned CLI migration/repeated upgrade, secure user provisioning and private audit metadata.
- Redis Lua atomic rate limiting, shared concurrency admission/release and crashed-lease expiry.
- Optional user-isolated caching, malformed cache rejection and request validation.
- Sanitized Redis/database failure responses and fail-closed admission.
- Transient upstream retry, fallback, permanent rejection, malformed output, per-attempt timeout and total provider deadline.
- A stalled primary reserves time for fallback; connection failures can reach the fallback.
- Lease-release errors and post-commit optional cache-write failures do not replace completed work.
- Unexpected application errors remain visible in bounded-route metrics and sanitized request events.
- Provider-reported total tokens remain correct when greater than prompt plus visible completion; cache hits do not double-count consumption.

The separate `tools/e2e_local.py` harness starts real Uvicorn, actual Redis, temporary SQLite and the explicitly labeled loopback HTTP provider fixture. It checks CLI migrations, three account roles, readiness, HTTP JWT/RBAC, provider transport and cache isolation, authenticated metrics, audit privacy/role changes and absence of the generated password/test question from logs. It terminates its subprocesses and removes its temporary database. **That harness is not LLM inference or PostgreSQL testing**; those were independently exercised in the full stack above.

## Reproduce

Install `requirements-dev.txt` in an isolated environment. Point these checks only at a disposable Redis:

```bash
REDIS_INTEGRATION_URL=redis://127.0.0.1:6379/15 python -m pytest -q
python tools/e2e_local.py --redis-url redis://127.0.0.1:6379/14
python scripts/validate_deploy.py
python -m compileall -q app alembic scripts tests tools
```

Without `REDIS_INTEGRATION_URL`, the external-Redis check skips; that is not equivalent to a real-Redis run. The normal suite uses mocks and does not spend provider credits.

For the actual stack, follow the README: generate private `.env` secrets, configure a real provider, start Compose, verify migration exit/health, provision accounts, then log in and call `/chat` with the returned JWT. Inspect the actual response model/usage and audit rows; repeat the same user/question with an explicitly enabled cache. The private local verification used port 18080 for Nginx and 19090 for Prometheus because the host already had a service on 8080; that service was preserved.

The build host used an isolated Docker **29.10.0** daemon with its own socket/data directory. Its intentionally disabled default bridge required `docker build --network=host` to download dependencies. Runtime services used the Compose private network and actual verified outbound TLS. This host-specific setup is not a public-hosting requirement or a change to the default project deployment.

The standalone Compose, Buildx and Prometheus validation tools were checked against their upstream SHA-256 checksums. No hosted CI run is claimed.

## Not executed here

- Kubernetes deployment, ingress/network-policy behavior, HPA scaling or multi-node failover; manifests were statically checked only.
- 100/500 RPS load, sustained provider capacity, cost forecasts or quota guarantees. Architecture numbers are illustrative sizing calculations.
- Hosted CI, public TLS/ingress, production alert delivery, enterprise OIDC, restore drills, cross-region recovery or a production rollout.
- Successful Gemini 3.8 generation; it was unavailable during these checks. Fallback was genuinely tested, not substituted in the evidence as 3.8 success.

## Boundaries

This is a tested local assessment implementation, not a claim that an arbitrary target environment is production-certified. Before public deployment: configure trusted proxies and edge login limits, complete TLS/secrets/egress and managed-service setup, exercise restore/rollback, validate actual load against provider budgets, and review dependency/security updates. No repository has been published and no assessment form has been submitted.
