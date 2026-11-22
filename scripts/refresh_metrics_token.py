#!/usr/bin/env python3
"""Login as a dedicated admin and atomically rotate a Prometheus bearer file.

Keep --watch running in a supervised process. The directory mount (not a file
mount) ensures atomic replacements are visible inside the Prometheus container.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--username", default=os.environ.get("METRICS_USERNAME"))
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / ".secrets/metrics/token")
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    parsed = urllib.parse.urlsplit(args.url)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Use HTTPS for non-loopback login endpoints")
    username = args.username or input("Metrics admin username: ")
    password = os.environ.get("METRICS_PASSWORD") or getpass.getpass("Metrics admin password: ")
    default_secrets = Path(__file__).resolve().parents[1] / ".secrets"
    if args.output.parent.parent == default_secrets:
        default_secrets.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(default_secrets, 0o700)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    # Nonroot Prometheus must traverse this directory and read its token.
    # The containing .secrets directory remains private to the host owner.
    os.chmod(args.output.parent, 0o755)
    while True:
        try:
            request = urllib.request.Request(
                args.url.rstrip("/") + "/auth/login",
                data=json.dumps({"username": username, "password": password}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                token = json.load(response)["access_token"]
            if not isinstance(token, str) or not token or "\n" in token:
                raise ValueError("Login did not return a usable access_token")
            verification = urllib.request.Request(
                args.url.rstrip("/") + "/metrics",
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(verification, timeout=15) as response:
                if response.status != 200:
                    raise ValueError("Metrics admin authorization check failed")
            fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix=".token-")
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(token + "\n")
                os.chmod(temporary, 0o644)
                os.replace(temporary, args.output)
            finally:
                Path(temporary).unlink(missing_ok=True)
            print("Metrics token rotated", flush=True)
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            # Do not log credentials, request body, or returned tokens.
            print(f"Token rotation failed ({type(exc).__name__}); previous token retained", flush=True)
            if not args.watch:
                raise SystemExit(1) from None
        if not args.watch:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
