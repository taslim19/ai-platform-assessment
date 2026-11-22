import asyncio
import os
import pytest
from fakeredis.aioredis import FakeRedis


@pytest.fixture
async def redis_client():
    client = FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_rate_limit_is_atomic_and_expires(redis_client):
    from app.controls import Controls, RateLimited
    c = Controls(redis_client)
    await c.rate_limit('user-a', 2, 1)
    await c.rate_limit('user-a', 2, 1)
    with pytest.raises(RateLimited):
        await c.rate_limit('user-a', 2, 1)
    await c.rate_limit('user-b', 2, 1)
    await asyncio.sleep(1.05)
    await c.rate_limit('user-a', 2, 1)


async def test_concurrency_leases_release_and_expire(redis_client):
    from app.controls import Controls, CapacityExceeded
    c = Controls(redis_client)
    async with c.lease(1, 0.1):
        with pytest.raises(CapacityExceeded):
            async with c.lease(1, 0.1):
                pytest.fail('capacity exceeded')
    assert await redis_client.zcard('aiqa:provider:leases') == 0
    # A crashed holder disappears automatically after its server-clock lease expires.
    now = await redis_client.time()
    milliseconds = int(now[0] * 1000 + now[1] / 1000)
    await redis_client.zadd('aiqa:provider:leases', {'crashed': milliseconds + 50})
    await asyncio.sleep(0.07)
    async with c.lease(1, 0.1):
        pass


async def test_lease_releases_on_error(redis_client):
    from app.controls import Controls
    with pytest.raises(RuntimeError):
        async with Controls(redis_client).lease(1, 1):
            raise RuntimeError('provider failed')
    assert await redis_client.zcard('aiqa:provider:leases') == 0


async def test_cleanup_outage_does_not_replace_completed_result(redis_client, monkeypatch, caplog):
    from app.controls import Controls, RELEASE_SCRIPT
    from redis.exceptions import ConnectionError
    original_eval = redis_client.eval
    async def fail_release(script, *args):
        if script == RELEASE_SCRIPT:
            raise ConnectionError('sensitive endpoint details must not be logged')
        return await original_eval(script, *args)
    monkeypatch.setattr(redis_client, 'eval', fail_release)
    async with Controls(redis_client).lease(1, .05):
        result = 'completed paid operation'
    assert result == 'completed paid operation'
    assert 'provider_lease_release_failed' in caplog.text
    assert 'sensitive endpoint' not in caplog.text
    await asyncio.sleep(.07)
    assert await redis_client.zcard('aiqa:provider:leases') == 0


@pytest.mark.integration
async def test_real_redis_atomic_limit_and_lease():
    url = os.getenv('REDIS_INTEGRATION_URL') or os.getenv('TEST_REDIS_URL')
    if not url:
        pytest.skip('set REDIS_INTEGRATION_URL to a dedicated disposable Redis database')
    from redis.asyncio import Redis
    from app.controls import Controls, RateLimited, CapacityExceeded
    client = Redis.from_url(url, decode_responses=True)
    import uuid
    namespace = 'aiqa:test:' + uuid.uuid4().hex
    c = Controls(client, namespace=namespace)
    try:
        results = await asyncio.gather(*(c.rate_limit('parallel', 3, 2) for _ in range(10)),
                                       return_exceptions=True)
        assert sum(result is None for result in results) == 3
        assert sum(isinstance(result, RateLimited) for result in results) == 7
        async with c.lease(1, .1):
            with pytest.raises(CapacityExceeded):
                async with c.lease(1, .1):
                    pytest.fail('global capacity must be enforced')
        assert await client.zcard(namespace + ':provider:leases') == 0
        now = await client.time()
        millis = now[0] * 1000 + now[1] // 1000
        await client.zadd(namespace + ':provider:leases', {'crashed': millis + 30})
        await asyncio.sleep(.05)
        async with c.lease(1, .1):
            pass
    finally:
        await client.delete(namespace + ':rate:parallel', namespace + ':provider:leases')
        await client.aclose()
