#!/usr/bin/env python3
"""Emit a Kubernetes Secret JSON to stdout, never to the repository.

Required environment: DATABASE_URL, REDIS_URL, LLM_API_KEY.
JWT_SECRET may be supplied (recommended from a secret manager) or generated once.
Pipe output directly into kubectl apply -f -; never log this output in CI.
"""
import json
import os
import secrets


def main():
    required = ["DATABASE_URL", "REDIS_URL", "LLM_API_KEY"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise SystemExit("Missing required environment: " + ", ".join(missing))
    data = {key: os.environ[key] for key in required}
    data["JWT_SECRET"] = os.environ.get("JWT_SECRET") or secrets.token_hex(32)
    if len(data["JWT_SECRET"]) < 32:
        raise SystemExit("JWT_SECRET must contain at least 32 characters")
    if os.environ.get("LLM_FALLBACK_API_KEY"):
        data["LLM_FALLBACK_API_KEY"] = os.environ["LLM_FALLBACK_API_KEY"]
    print(json.dumps({"apiVersion": "v1", "kind": "Secret", "metadata": {
        "name": "aiqa-secrets", "namespace": "aiqa"}, "type": "Opaque", "stringData": data}))


if __name__ == "__main__":
    main()
