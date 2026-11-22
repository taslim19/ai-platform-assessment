import pytest
import jwt
from sqlalchemy import select
from redis.exceptions import ConnectionError as RedisConnectionError
from conftest import login


async def test_chat_roundtrip_persists_metadata_not_content(api):
    from app.db import ChatRecord
    client, app, calls = api
    headers = await login(client)
    response = await client.post('/chat', json={'question':'private question'}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['answer'] == 'answer'
    assert body['usage']['total_tokens'] == 5
    assert body['request_id'] == response.headers['x-request-id']
    assert not body['cached']
    assert body['latency_ms'] >= 0
    async with app.state.sessions() as session:
        record = (await session.execute(select(ChatRecord))).scalar_one()
        assert record.question is None and record.answer is None
        assert record.total_tokens == 5


async def test_provider_total_token_metrics_preserve_reasoning_usage_and_exclude_cache(api, monkeypatch):
    client, app, calls = api
    provider_calls = []

    async def complete(question):
        provider_calls.append(question)
        return {'answer': '42', 'model': 'reasoning-model',
                'usage': {'prompt_tokens': 26, 'completion_tokens': 2, 'total_tokens': 169}}

    monkeypatch.setattr(app.state.gateway, 'complete', complete)
    user = await login(client)
    first = await client.post('/chat', json={'question': 'reasoning test'}, headers=user)
    second = await client.post('/chat', json={'question': 'reasoning test'}, headers=user)
    assert first.status_code == second.status_code == 200
    assert first.json()['usage']['total_tokens'] == second.json()['usage']['total_tokens'] == 169
    assert second.json()['cached'] is True
    assert len(provider_calls) == 1
    metrics = app.state.metrics.tokens
    assert metrics.labels('prompt_tokens')._value.get() == 26
    assert metrics.labels('completion_tokens')._value.get() == 2
    assert metrics.labels('total_tokens')._value.get() == 169


async def test_auth_rbac_metrics_and_liveness(api):
    client, app, calls = api
    assert (await client.get('/health/live')).status_code == 200
    assert (await client.get('/health')).status_code == 200
    assert (await client.post('/chat',json={'question':'hi'})).status_code == 401
    assert (await client.get('/metrics')).status_code == 401
    assert (await client.post('/auth/login',json={'username':'user','password':'bad'})).status_code == 401
    readonly = await login(client,'readonly')
    assert (await client.post('/chat',json={'question':'hi'},headers=readonly)).status_code == 403
    user = await login(client)
    assert (await client.get('/metrics',headers=user)).status_code == 403
    admin = await login(client,'admin')
    assert (await client.post('/chat',json={'question':'hi'},headers=admin)).status_code == 200
    metrics = await client.get('/metrics',headers=admin)
    assert metrics.status_code == 200
    assert 'aiqa_http_request_duration_seconds' in metrics.text
    assert 'aiqa_llm_tokens_total' in metrics.text
    assert 'private question' not in metrics.text


async def test_cache_per_user_and_validation(api):
    client, app, calls = api
    user = await login(client)
    first = await client.post('/chat',json={'question':'hi'},headers=user)
    second = await client.post('/chat',json={'question':'hi'},headers=user)
    assert not first.json()['cached']
    assert second.json()['cached']
    assert len(calls) == 1
    admin = await login(client,'admin')
    assert not (await client.post('/chat',json={'question':'hi'},headers=admin)).json()['cached']
    assert len(calls) == 2
    assert (await client.post('/chat',json={'question':'  '},headers=user)).status_code == 422
    assert (await client.post('/chat',json={'question':'x'*16001},headers=user)).status_code == 422


async def test_redis_failure_fails_closed_and_readiness_fails(api, monkeypatch):
    client, app, calls = api
    user = await login(client)
    async def unavailable(*args,**kwargs):
        raise RedisConnectionError('secret backend location')
    monkeypatch.setattr(app.state.redis,'eval',unavailable)
    response = await client.post('/chat',json={'question':'hi'},headers=user)
    assert response.status_code == 503
    assert 'secret backend' not in response.text
    assert not calls
    monkeypatch.setattr(app.state.redis,'ping',unavailable)
    assert (await client.get('/health')).status_code == 503
    assert (await client.get('/health/live')).status_code == 200


async def test_database_failure_is_sanitized(api):
    client, app, calls = api
    from sqlalchemy import text
    async with app.state.engine.begin() as conn:
        await conn.execute(text('DROP TABLE users'))
    response = await client.post('/auth/login',json={'username':'user','password':'password-1234'})
    assert response.status_code == 503
    assert 'SELECT' not in response.text


async def test_limits_and_invalid_tokens(api):
    client, app, calls = api
    user = await login(client)
    app.state.settings.rate_limit_requests = 1
    assert (await client.post('/chat',json={'question':'one'},headers=user)).status_code == 200
    response = await client.post('/chat',json={'question':'two'},headers=user)
    assert response.status_code == 429
    assert int(response.headers['retry-after']) >= 1
    response = await client.post('/chat',json={'question':'two'},headers={'Authorization':'Bearer corrupt'})
    assert response.status_code == 401
    token = jwt.encode({'sub':'user','aud':'aiqa-api','iss':'aiqa','iat':1,'exp':2},
                       app.state.settings.jwt_secret.get_secret_value(),algorithm='HS256')
    assert (await client.get('/metrics',headers={'Authorization':'Bearer '+token})).status_code == 401


async def test_disabled_users_and_changed_roles_rechecked(api):
    from app.db import User
    client, app, calls = api
    user = await login(client)
    async with app.state.sessions() as session:
        row = await session.get(User,'user')
        row.role = 'readonly'
        await session.commit()
    assert (await client.post('/chat',json={'question':'hi'},headers=user)).status_code == 403
    async with app.state.sessions() as session:
        row = await session.get(User,'user')
        row.active = False
        await session.commit()
    assert (await client.get('/metrics',headers=user)).status_code == 401
