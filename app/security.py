from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from app.config import Settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def issue_token(subject: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": subject, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
                       "iss": settings.jwt_issuer, "aud": settings.jwt_audience},
                      settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_token(token: str, settings: Settings) -> dict:
    return jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"],
                      issuer=settings.jwt_issuer, audience=settings.jwt_audience,
                      options={"require": ["exp", "iat", "sub", "iss", "aud"]})
