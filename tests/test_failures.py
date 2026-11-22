import asyncio
import json
import logging
import pytest
from conftest import login
from sqlalchemy.exc import OperationalError


async def test_os_database_connection_failure_sanitized(api, monkeypatch):
    client, app, calls = api
    def unavailable():
        raise ConnectionRefusedError('sensitive hostname/db/password')
    monkeypatch.setattr(app.state.sessions, '__call__', unavailable)
    # Session factory special methods resolve on the type, so use engine boundary instead.
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError('sensitive hostname/db/password')
    monkeypatch.setattr(app.state.engine.sync_engine, 'connect', refuse)
    response = await client.get('/health')
    assert response.status_code == 503
    assert 'sensitive hostname' not in response.text
    response = await client.post('/auth/login',json={'username':'user','password':'password-1234'})
    assert response.status_code == 503
    assert 'sensitive hostname' not in response.text


async def test_capacity_and_provider_timeout_release_lease(api, monkeypatch):
    from app.gateway import ProviderTimeout
    client, app, calls = api
    headers = await login(client)
    clock = await app.state.redis.time()
    now = int(clock[0] * 1000 + clock[1]/1000)
    await app.state.redis.zadd('aiqa:provider:leases',{'busy':now+60000})
    app.state.settings.llm_max_concurrency = 1
    response = await client.post('/chat',json={'question':'hi'},headers=headers)
    assert response.status_code == 503
    assert not calls
    await app.state.redis.delete('aiqa:provider:leases')
    async def timeout(question):
        raise ProviderTimeout('sensitive provider error')
    monkeypatch.setattr(app.state.gateway,'complete',timeout)
    response = await client.post('/chat',json={'question':'hi'},headers=headers)
    assert response.status_code == 504
    assert 'sensitive' not in response.text
    assert await app.state.redis.zcard('aiqa:provider:leases') == 0


async def test_login_limit_and_validation_does_not_reflect_password(api):
    client, app, calls = api
    app.state.settings.login_rate_limit_requests = 1
    body = {'username':'user','password':'wrong'}
    assert (await client.post('/auth/login',json=body)).status_code == 401
    response = await client.post('/auth/login',json=body)
    assert response.status_code == 429
    response = await client.post('/auth/login',json={'username':'invalid name','password':'ultra-secret'})
    assert response.status_code == 422
    assert 'ultra-secret' not in response.text


async def test_cache_off_and_content_storage_opt_in(api):
    from app.db import ChatRecord
    from sqlalchemy import select
    client, app, calls = api
    app.state.settings.cache_ttl_seconds = 0
    app.state.settings.store_chat_content = True
    headers = await login(client)
    for _ in range(2):
        response = await client.post('/chat',json={'question':'store this'},headers=headers)
        assert response.status_code == 200
        assert not response.json()['cached']
    assert len(calls) == 2
    async with app.state.sessions() as session:
        records = (await session.execute(select(ChatRecord))).scalars().all()
        assert len(records) == 2
        assert all(record.question == 'store this' and record.answer == 'answer' for record in records)


async def test_request_log_has_no_sensitive_data(api, caplog):
    client, app, calls = api
    with caplog.at_level(logging.INFO, logger='aiqa'):
        headers = await login(client)
        response = await client.post('/chat',json={'question':'secret-question-777'},headers=headers)
    entries = [json.loads(record.message) for record in caplog.records if record.name == 'aiqa']
    assert entries[-1]['request_id'] == response.headers['x-request-id']
    assert entries[-1]['route'] == '/chat'
    assert 'secret-question-777' not in caplog.text
    assert 'password-1234' not in caplog.text
    assert headers['Authorization'] not in caplog.text


async def test_token_metrics_exclude_cache_and_unknown_path_cardinality(api):
    client, app, calls = api
    headers = await login(client)
    for _ in range(2):
        assert (await client.post('/chat',json={'question':'hello'},headers=headers)).status_code == 200
    for path in ('/random-identifier-A','/random-identifier-B'):
        assert (await client.get(path)).status_code == 404
    admin = await login(client,'admin')
    metrics = (await client.get('/metrics',headers=admin)).text
    assert 'aiqa_llm_tokens_total{type="prompt_tokens"} 3.0' in metrics
    assert 'aiqa_llm_tokens_total{type="completion_tokens"} 2.0' in metrics
    assert 'random-identifier' not in metrics
    assert 'route="unmatched"' in metrics


async def test_cache_population_outage_preserves_completed_chat(api, monkeypatch, caplog):
    from redis.exceptions import ConnectionError
    from app.db import ChatRecord
    from sqlalchemy import select
    client, app, calls = api
    headers = await login(client)
    async def unavailable(*args, **kwargs):
        raise ConnectionError('sensitive cache endpoint')
    monkeypatch.setattr(app.state.redis, 'set', unavailable)
    response = await client.post('/chat', json={'question': 'hello'}, headers=headers)
    assert response.status_code == 200
    assert response.json()['answer'] == 'answer'
    assert len(calls) == 1
    async with app.state.sessions() as session:
        records = (await session.execute(select(ChatRecord))).scalars().all()
    assert len(records) == 1
    assert 'cache_population_failed' in caplog.text
    assert 'sensitive cache endpoint' not in caplog.text


async def test_unexpected_failure_is_counted_and_logged(api, caplog):
    import httpx
    from prometheus_client import generate_latest
    _, app, _ = api
    @app.get('/unexpected-failure')
    async def broken():
        raise RuntimeError('sensitive internal failure detail')
    with caplog.at_level(logging.INFO, logger='aiqa'):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                    base_url='http://test') as client:
            response = await client.get('/unexpected-failure')
    assert response.status_code == 500
    metrics = generate_latest(app.state.metrics.registry).decode()
    assert 'aiqa_http_requests_total{method="GET",route="/unexpected-failure",status="500"} 1.0' in metrics
    entries = [json.loads(record.message) for record in caplog.records if record.name == 'aiqa']
    assert entries[-1]['status'] == 500
    assert entries[-1]['route'] == '/unexpected-failure'
    assert 'sensitive internal failure detail' not in caplog.text
