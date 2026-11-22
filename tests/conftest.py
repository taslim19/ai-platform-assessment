import os
os.environ.setdefault('JWT_SECRET', 'test-only-secret-not-for-deployment-123456789')

import httpx
import pytest
from fakeredis.aioredis import FakeRedis
from app.config import Settings


@pytest.fixture
async def api(tmp_path):
    from app.main import create_app
    from app.db import Base, User
    from app.security import hash_password
    calls = []
    async def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'model':'test-model','choices':[{'message':{'content':'answer'}}],
            'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})
    s = Settings(database_url=f'sqlite+aiosqlite:///{tmp_path}/test.db',
                 jwt_secret='test-only-secret-not-for-deployment-123456789', llm_api_key='fake',
                 cache_ttl_seconds=60, rate_limit_requests=100)
    redis = FakeRedis(decode_responses=True)
    provider_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(s, redis_client=redis, provider_client=provider_client)
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with app.state.sessions() as session:
        for role in ('admin','user','readonly'):
            session.add(User(id=role, username=role, password_hash=hash_password('password-1234'), role=role))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, app, calls
    await provider_client.aclose()
    await redis.aclose()
    await app.state.engine.dispose()


async def login(client, username='user'):
    response = await client.post('/auth/login', json={'username':username,'password':'password-1234'})
    assert response.status_code == 200, response.text
    return {'Authorization':'Bearer '+response.json()['access_token']}
