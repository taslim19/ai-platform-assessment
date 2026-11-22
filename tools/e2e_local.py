"""Real local HTTP + Redis + SQLite integration check (no paid LLM calls).

Run from the repository with test dependencies installed:
  python tools/e2e_local.py --redis-url redis://127.0.0.1:16379/14
Use a DISPOSABLE local Redis database. Unique users/cache keys and rate/lease
keys are created and expired, without flushing Redis. PostgreSQL is not tested.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-url", required=True)
    args = parser.parse_args()
    checks = []
    processes = []
    with tempfile.TemporaryDirectory(prefix="aiqa-e2e-") as directory:
        temp = Path(directory)
        db = temp / "audit.db"
        provider_port, api_port = port(), port()
        password = secrets.token_urlsafe(24)
        env = dict(os.environ, APP_ENV="development", DATABASE_URL=f"sqlite+aiosqlite:///{db}", REDIS_URL=args.redis_url,
                   JWT_SECRET=secrets.token_urlsafe(48), LLM_BASE_URL=f"http://127.0.0.1:{provider_port}/v1",
                   LLM_API_KEY="fixture-not-a-real-provider-key", LLM_MODEL="fixture-model",
                   CACHE_TTL_SECONDS="30", LOGIN_RATE_LIMIT_REQUESTS="100", BOOTSTRAP_PASSWORD=password)
        env.pop("PYTHONPATH", None)
        for name in ("LLM_FALLBACK_MODEL", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_API_KEY"):
            env.pop(name, None)
        try:
            subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=ROOT, env=env, check=True, capture_output=True)
            # Run a second time to prove a versioned migration is idempotent.
            subprocess.run([sys.executable, "-m", "app.cli", "migrate"], cwd=ROOT, env=env, check=True, capture_output=True)
            checks.append("CLI versioned SQLite migration + repeat upgrade")
            for role in ("admin", "user", "readonly"):
                subprocess.run([sys.executable, "-m", "app.cli", "create-user", "--username", role, "--role", role], cwd=ROOT, env=env, check=True, capture_output=True)
            checks.append("CLI provisioned three roles with generated password")
            with (temp / "process.log").open("w+") as logs:
                for cmd in ([sys.executable, "tools/provider_fixture.py", "--port", str(provider_port)],
                            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"]):
                    processes.append(subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=logs, stderr=logs))
                with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=15) as client:
                    for attempt in range(100):
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        time.sleep(.1)
                    else:
                        raise AssertionError("API never became ready")
                    checks.append("Uvicorn HTTP readiness with real Redis + SQLite")
                    assert client.get("/health/live").status_code == 200
                    assert client.post("/chat", json={"question": "fixture"}).status_code == 401
                    assert client.get("/metrics").status_code == 401
                    tokens = {}
                    for role in ("admin", "user", "readonly"):
                        response = client.post("/auth/login", json={"username": role, "password": password})
                        assert response.status_code == 200
                        tokens[role] = {"Authorization": "Bearer " + response.json()["access_token"]}
                    assert client.post("/chat", json={"question": "fixture"}, headers=tokens["readonly"]).status_code == 403
                    assert client.get("/metrics", headers=tokens["user"]).status_code == 403
                    checks.append("Real HTTP JWT login, anonymous rejection and RBAC")
                    first = client.post("/chat", json={"question": "local transport fixture"}, headers=tokens["user"])
                    assert first.status_code == 200, first.text
                    result = first.json()
                    assert not result["cached"] and result["usage"]["total_tokens"] == 29
                    assert result["request_id"] == first.headers["X-Request-ID"]
                    second = client.post("/chat", json={"question": "local transport fixture"}, headers=tokens["user"])
                    assert second.status_code == 200 and second.json()["cached"]
                    third = client.post("/chat", json={"question": "local transport fixture"}, headers=tokens["admin"])
                    assert third.status_code == 200 and not third.json()["cached"]
                    checks.append("Actual provider-fixture HTTP roundtrip, token accounting and per-user cache isolation")
                    metrics = client.get("/metrics", headers=tokens["admin"])
                    assert metrics.status_code == 200 and "aiqa_" in metrics.text
                    checks.append("Admin-authenticated Prometheus scrape")
                    with sqlite3.connect(db) as connection:
                        rows = connection.execute("SELECT question, answer, cached, total_tokens FROM chat_records").fetchall()
                        assert len(rows) == 3 and all(row[0] is None and row[1] is None and row[3] == 29 for row in rows)
                        connection.execute("UPDATE users SET role='readonly' WHERE username='user'")
                    assert client.post("/chat", json={"question": "no longer allowed"}, headers=tokens["user"]).status_code == 403
                    checks.append("Durable audit privacy defaults and immediate DB-rechecked role changes")
                logs.flush()
                logs.seek(0)
                output = logs.read()
                assert password not in output and "local transport fixture" not in output
                checks.append("No generated password or test question in server logs")
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print(json.dumps({"status": "passed", "checks": checks, "provider": "explicit local fixture (not LLM inference)", "database": "SQLite (not PostgreSQL)"}, indent=2))


if __name__ == "__main__":
    main()
