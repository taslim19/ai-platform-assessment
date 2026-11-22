#!/usr/bin/env python3
"""Generate local credentials once; never overwrite an existing .env."""
from pathlib import Path
import os
import secrets


def main():
    root = Path(__file__).resolve().parents[1]
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(24),
        "REDIS_PASSWORD": secrets.token_hex(24),
        "JWT_SECRET": secrets.token_hex(32),
    }
    values["DATABASE_URL"] = (
        "postgresql+asyncpg://app:" + values["POSTGRES_PASSWORD"] + "@postgres:5432/aiqa"
    )
    values["REDIS_URL"] = "redis://:" + values["REDIS_PASSWORD"] + "@redis:6379/0"
    content = (root / ".env.example").read_text()
    content = "\n".join(
        f"{line.split('=', 1)[0]}={values[line.split('=', 1)[0]]}"
        if "=" in line and line.split("=", 1)[0] in values else line
        for line in content.splitlines()
    ) + "\n"
    try:
        fd = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env already exists; refusing to overwrite credentials") from None
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    print("Created .env mode 0600. Set LLM_API_KEY before calling the AI endpoint.")


if __name__ == "__main__":
    main()
