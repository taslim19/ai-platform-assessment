# Kubernetes example: prerequisites and deliberate deployment sequence

These are **adaptable manifests**, not a claimed provisioned environment. Replace image registry/release, domain, TLS secret, managed-service CIDRs, and cluster-specific labels before use. No live keys or encoded placeholder passwords are committed. Use a currently supported Kubernetes release, a NetworkPolicy-enforcing CNI, metrics-server, a maintained Traefik ingress controller (class `traefik`, namespace `traefik`, entrypoint `websecure`), DNS, and an actual TLS certificate. Adapt the NetworkPolicy controller labels to your installation. Configure controller HTTP-to-HTTPS redirection, request-body limits, and upstream timeouts of at least 60 seconds explicitly. Ingress/HPA/CNI controllers are external prerequisites, not created here.

The Kubernetes community ingress-nginx controller retired in March 2026; do not install it for this example. This is distinct from the ordinary nginx container used as the Compose load balancer. Official references: [Kubernetes retirement statement](https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/) and [Traefik Ingress routing configuration](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress/).

## Managed external data plane

Provision private, encrypted managed PostgreSQL 16+ with backup/PITR and failover; managed Redis 7-compatible with authentication, TLS and failover. Permit only cluster/application source security groups. NetworkPolicy cannot provision databases, configure security groups, or create NAT. Provide reachability and private DNS from pods. Use the application-tested asyncpg PostgreSQL URL scheme and Redis URL. PostgreSQL TLS must be configured through supported asyncpg connection options and validated against the managed provider; do not assume a libpq `sslmode` query works unchanged in every async driver. For Redis TLS use `rediss://` with provider certificate verification. Validate both with actual driver connections before release. Redis clusters with redirects require a cluster-capable client; this app's ordinary Redis URL is not automatically a Redis Cluster client.

`networkpolicy.yaml` deliberately uses RFC 5737 nonroutable example database/Redis IPs. Replace both with your actual private ranges (include failover addresses), or use CNI FQDN policy / an egress proxy. Adapt DNS policy for NodeLocal DNS if present. The public TCP/443 rule permits provider HTTPS because vanilla NetworkPolicy has no FQDN matching; constrain it with an audited egress gateway/FQDN-capable CNI in production. The namespace default-deny may also affect ad-hoc debugging pods: attach the approved app label only for authorized dependency checks. The monitoring namespace is allowed to reach the API, but still needs admin Bearer authentication. Do not add a public metrics exemption.

## Create configuration and secret

```sh
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml
# Export real DATABASE_URL, REDIS_URL, LLM_API_KEY, JWT_SECRET from a secret manager.
# The JWT secret must be stable across pods/releases and at least 32 characters.
# Without JWT_SECRET the helper generates a new key: use ONLY for initial creation;
# rerunning that way rotates JWT signing and invalidates all existing access tokens.
python3 scripts/generate_k8s_secret.py | kubectl apply -f -
kubectl apply -f deploy/k8s/networkpolicy.yaml
```

The helper's emitted Secret JSON is the secret template: it contains freshly generated/runtime values, never static repository credentials. Never run this under shell tracing or copy its stdout into logs. Prefer an external secret-store controller with workload identity and KMS encryption; Kubernetes Secrets are base64 encoding, not encryption by themselves. Restrict RBAC and enable encryption-at-rest. ConfigMap is exclusively nonsecret. Optional fallback model/base URL belong in ConfigMap, while fallback API key belongs in Secret. Add imagePullSecrets/workload-identity configuration required by your registry/platform.

## Migrate first, roll out second

Use the **same immutable image digest** in the Job and Deployment. Serialize release pipelines. Kubernetes Deployments do not depend on Jobs; the release process must enforce success:

```sh
# Replace image and infrastructure placeholders in reviewed manifests first.
# On later releases delete the completed prior Job, or use a unique release name.
kubectl -n aiqa delete job aiqa-migrate --ignore-not-found
kubectl apply -f deploy/k8s/migration-job.yaml
kubectl -n aiqa wait --for=condition=complete job/aiqa-migrate --timeout=300s
# STOP if migration fails; inspect its status/logs and do not proceed.
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/deployment.yaml
kubectl apply -f deploy/k8s/pdb.yaml
kubectl apply -f deploy/k8s/hpa.yaml
kubectl apply -f deploy/k8s/ingress.yaml
kubectl -n aiqa rollout status deployment/aiqa-api --timeout=300s
```

Do not use `kubectl apply -f deploy/k8s/` as the release workflow: it races migration and starts placeholder resources. The migration Job is bounded by deadline and retries, not used as an initContainer that runs once per replica. The CLI applies the image's reviewed versioned Alembic migrations to head; future schema changes must preserve expand/contract compatibility during rollouts. Bootstrap an administrator interactively with `kubectl -n aiqa exec -it deploy/aiqa-api -- python -m app.cli create-user --username operator --role admin`.

## Scaling and availability

Two initial replicas, one process each, requests 250m CPU/256Mi and limits 1 CPU/768Mi are **starting values**, not measured capacity. The PDB protects one available pod during voluntary disruptions; it does not protect against simultaneous failures or provide multi-AZ placement. Hostname spreading is best effort. Use multiple nodes/AZs and provider-specific topology constraints for production.

CPU HPA (60%, 2–10 replicas) is only a baseline and requires metrics-server. LLM inference requests are mostly network I/O; CPU often remains low while concurrency and queue pressure saturate. Install a custom/external metrics adapter and use measured inflight utilization/queue wait/latency (with bounds, stabilization, and quota/cost caps). A Prometheus server alone does not expose custom metrics to HPA. Reconcile provider quotas and total DB connections before raising max replicas or per-pod concurrency. Requests to LLM providers have billable retries; nginx does not replay non-idempotent calls.

For monitoring, deploy a private Prometheus in the allowed monitoring namespace and adapt DNS discovery to Service/EndpointSlice or Kubernetes pod discovery. The Compose DNS name `api` is not a Kubernetes scrape configuration. Provision RBAC only for discovery when needed. Mount an admin login-generated token via a secret volume and rotate it before expiry with a supervised credential-injected refresher. Keep both password and rotating tokens in a proper secret manager/controller; do not reuse a public metrics endpoint. Wire alerts to Alertmanager and verify actual delivery.

Before declaring production readiness, validate TLS, readiness/liveness, rolling shutdown under traffic, connection pool bounds, multi-AZ failover, PITR restore, quota handling, authenticated metrics rotation and representative load. `kubectl --dry-run=server` validates against your cluster before real apply. These manifests have not been applied to a live cluster here.
