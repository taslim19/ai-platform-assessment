import pytest


def test_password_hash_and_verified_jwt():
    from app.config import Settings
    from app.security import hash_password, verify_password, issue_token, decode_token
    s = Settings(jwt_secret='x' * 48)
    hashed = hash_password('correct-horse-battery')
    assert hashed.startswith('$argon2id$')
    assert verify_password('correct-horse-battery', hashed)
    assert not verify_password('wrong', hashed)
    token = issue_token('abc', s)
    claims = decode_token(token, s)
    assert claims['sub'] == 'abc'
    assert claims['aud'] == 'aiqa-api'
    import jwt
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, s.model_copy(update={'jwt_audience': 'wrong'}))


def test_weak_jwt_secret_rejected():
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(jwt_secret='short')
