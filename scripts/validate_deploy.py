#!/usr/bin/env python3
"""Static deployment contracts and isolated helper checks; NOT container tests.

Requires PyYAML (CI installs it separately from application dependencies).
No real credentials, external services, or application files are modified.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_yaml():
    paths = [ROOT / "compose.yaml", ROOT / ".github/workflows/ci.yml"]
    paths += sorted((ROOT / "deploy").rglob("*.yaml"))
    paths += sorted((ROOT / "deploy").rglob("*.yml"))
    for path in paths:
        documents = list(yaml.safe_load_all(path.read_text()))
        require(all(isinstance(item, dict) for item in documents), f"Invalid YAML document: {path}")
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = compose["services"]
    for name in ("postgres", "redis", "api"):
        require("ports" not in services[name], f"Unexpected public {name} ports")
    require("container_name" not in services["api"], "Fixed API name breaks scaling")
    require(services["api"]["read_only"], "API rootfs must be read-only")
    require(services["migrate"]["command"] == ["python", "-m", "app.cli", "migrate"], "Migration command drift")
    dependencies = services["api"]["depends_on"]
    require(dependencies["migrate"]["condition"] == "service_completed_successfully", "Missing migration gate")
    require(dependencies["redis"]["condition"] == "service_healthy", "Missing Redis readiness gate")
    require(services["prometheus"]["profiles"] == ["monitoring"], "Monitoring must be opt-in")
    scrape = yaml.safe_load((ROOT / "deploy/prometheus/prometheus.yml").read_text())["scrape_configs"][0]
    require(scrape["authorization"]["credentials_file"] == "/run/metrics/token", "Metrics must use token file")
    require("dns_sd_configs" in scrape, "Must discover each API replica, not scrape a load balancer")
    k8s = []
    for path in (ROOT / "deploy/k8s").glob("*.yaml"):
        k8s.extend(yaml.safe_load_all(path.read_text()))
    kinds = {item["kind"] for item in k8s}
    require({"Deployment", "Service", "HorizontalPodAutoscaler", "PodDisruptionBudget", "Ingress", "NetworkPolicy", "ConfigMap", "Job"} <= kinds, "Missing Kubernetes resource")
    require("Secret" not in kinds, "Generate secret at deployment time, never commit credentials")
    dockerfile = (ROOT / "Dockerfile").read_text()
    require("alembic.ini" in dockerfile and "alembic ./alembic" in dockerfile, "Container is missing migration assets")
    app_source = (ROOT / "app/main.py").read_text()
    require("/health/live" in app_source and any(value in app_source for value in ("'/health'", '\"/health\"')), "Health probe contract drift")
    require((ROOT / "requirements-dev.txt").is_file(), "CI test dependencies are missing")
    print(f"Parsed {len(paths)} YAML files; deployment contracts passed")


def run(*arguments, env=None):
    return subprocess.run([sys.executable, *map(str, arguments)], capture_output=True, text=True, env=env, check=False)


def check_helpers():
    with tempfile.TemporaryDirectory(prefix="aiqa-deploy-check-") as directory:
        temporary = Path(directory)
        (temporary / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/setup_env.py", temporary / "scripts/setup_env.py")
        shutil.copy(ROOT / ".env.example", temporary / ".env.example")
        result = run(temporary / "scripts/setup_env.py")
        require(result.returncode == 0, "Environment generation failed")
        generated = temporary / ".env"
        values = dict(line.split("=", 1) for line in generated.read_text().splitlines() if line and not line.startswith("#"))
        require(len(values["JWT_SECRET"]) >= 32, "JWT secret is too short")
        require(values["POSTGRES_PASSWORD"] != values["REDIS_PASSWORD"], "Credentials must be independent")
        require(stat.S_IMODE(generated.stat().st_mode) == 0o600, "Environment file permission drift")
        original = generated.read_bytes()
        require(run(temporary / "scripts/setup_env.py").returncode != 0, "Must refuse overwriting credentials")
        require(generated.read_bytes() == original, "Existing environment was overwritten")
        environment = dict(os.environ)
        for key in ("JWT_SECRET", "DATABASE_URL", "REDIS_URL", "LLM_API_KEY", "LLM_FALLBACK_API_KEY"):
            environment.pop(key, None)
        require(run(ROOT / "scripts/generate_k8s_secret.py", env=environment).returncode != 0, "Missing managed credentials must fail")
        environment.update(DATABASE_URL="postgresql+asyncpg://app:example@db/aiqa", REDIS_URL="rediss://:example@redis:6379/0", LLM_API_KEY="test-fixture-not-a-real-key")
        result = run(ROOT / "scripts/generate_k8s_secret.py", env=environment)
        require(result.returncode == 0, "Secret helper failed")
        secret = json.loads(result.stdout)
        require(secret["metadata"]["namespace"] == "aiqa", "Secret namespace mismatch")
        require(len(secret["stringData"]["JWT_SECRET"]) >= 32, "Generated K8s JWT key too short")
        check_token_helper(temporary, environment)
    print("Isolated env/bootstrap/secret/token helper checks passed")


def check_token_helper(temporary, environment):
    class Handler(BaseHTTPRequestHandler):
        allow_admin = True
        verified = False

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            require(self.path == "/auth/login", "Unexpected login route")
            require(body == {"username": "metrics-test", "password": "test-password"}, "Login payload drift")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"access_token":"test-admin-bearer"}')

        def do_GET(self):
            require(self.path == "/metrics", "Unexpected authorization verification route")
            require(self.headers.get("Authorization") == "Bearer test-admin-bearer", "Missing Bearer verification")
            type(self).verified = True
            self.send_response(200 if self.allow_admin else 403)
            self.end_headers()
            self.wfile.write(b"# test metrics\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        output = temporary / "metrics/token"
        environment.update(METRICS_USERNAME="metrics-test", METRICS_PASSWORD="test-password")
        arguments = [ROOT / "scripts/refresh_metrics_token.py", "--url", f"http://127.0.0.1:{server.server_port}", "--output", output]
        require(run(*arguments, env=environment).returncode == 0, "Token refresh helper failed")
        require(Handler.verified and output.read_text().strip() == "test-admin-bearer", "Unverified bearer file")
        Handler.allow_admin = False
        output.write_text("previous-token\n")
        require(run(*arguments, env=environment).returncode != 0, "Nonadmin token must fail")
        require(output.read_text() == "previous-token\n", "Failed refresh overwrote valid previous token")
        require(not list(output.parent.glob(".token-*")), "Temporary secret files leaked")
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    check_yaml()
    check_helpers()
