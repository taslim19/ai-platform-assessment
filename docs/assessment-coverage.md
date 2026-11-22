# Assessment coverage

This maps each requirement in **AI/LLM Platform & DevOps Engineer — Technical Assessment** to the submission. See `verification.md` for what was actually executed.

| Assessment item | Evidence / implementation |
| --- | --- |
| Python + FastAPI | `app/main.py`, application modules, pinned requirements |
| External or open-source LLM API | OpenAI-compatible HTTP gateway; configurable endpoint/key/model |
| `POST /auth/login` | Local password authentication and signed JWT |
| `POST /chat` | Validated authenticated question → LLM answer, usage and timing |
| `GET /health` | Readiness dependency checks; separate `/health/live` |
| `GET /metrics` | Admin-protected Prometheus metrics |
| Authenticate chat users | JWT verification plus persisted identity and RBAC checks |
| Timeouts / appropriate errors | Per-provider timeout, total deadline, sanitized application errors |
| Retry / fallback | Bounded transient retries, optional fallback configuration |
| Record latency / token usage | Metrics and persisted request metadata |
| JWT authentication | Issuer, audience, expiry, signature and identity checks |
| SSO / OIDC production extension | `architecture.md`, SSO/OIDC and RBAC section |
| Admin / user / read-only distinctions | Implemented route permission checks; future administrative/report endpoints explicitly distinguished |
| Dockerfile | Root `Dockerfile`, non-root API image |
| Compose or Kubernetes | `compose.yaml`; optional `deploy/k8s/` examples |
| Environment configuration | `.env.example`, validated application settings |
| No hard-coded keys | Runtime configuration, generated local credentials, ignored `.env` |
| Setup instructions | Root `README.md` |
| Users → LB → API → Redis / gateway → LLM | `architecture.svg`, Compose, logical gateway |
| Persistent database | PostgreSQL users and chat audit metadata; schema migration |
| 100 RPS / 500 RPS discussion | `architecture.md`, concurrency/token calculations and explicit quota limits |
| Horizontal replicas and balancing | Stateless API, Nginx Compose front end, Kubernetes deployment/service |
| HPA | Example manifest and limitations of CPU-only autoscaling |
| Redis cache / distributed limits | Shared admission controls; optional per-user cache |
| Background queues | Documented job/worker/DLQ extension, **not implemented** |
| Provider RPM / TPM / concurrency | Concurrency implemented; predictive RPM/TPM reservations documented as future extension |
| Concurrent LLM calls | Async HTTP and bounded shared concurrency |
| Failure recovery / degradation | Retry/fallback, fail-closed admission, health checks, runbook |
| Simple architecture diagram | SVG image and Mermaid source in architecture document |
| Single EC2 10 → 10,000 users | Staged migration section, state inventory, managed services, canary and rollback |
| Secrets / configuration management | Runtime env locally; secret-manager/workload-identity production design |
| Explain decisions and trade-offs | Architecture trade-offs section |
| Basic tests | `tests/`, CI workflow, verification report |
| Git repository | Local initialized Git repository and portable Git bundle in delivered archive |
| Loom or voice-over video ≤ 5 minutes | `walkthrough.mp4`, AI-narrated architectural presentation and editable `walkthrough.md`; not a personal voice recording |
| Submission link | The user controls publication and form submission; no third-party form submitted automatically |

## Deliberate limits

The assessment asks for a small production-oriented implementation and architectural explanation, not proof of a live 500 RPS environment. RAG, streaming, a full administration UI, enterprise identity provisioning, durable asynchronous jobs, and exactly-once provider billing are outside the implemented scope. The archive has no paid provider credentials. The included presentation must be reviewed by the submitter before use.
