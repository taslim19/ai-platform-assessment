"""Redis-side atomic admission controls; never silently degrade to process-local limits."""
import asyncio
import logging
import math
import uuid
from contextlib import asynccontextmanager
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

RATE_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('PTTL', KEYS[1])}
"""
LEASE_SCRIPT = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""
RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class CapacityExceeded(Exception):
    pass


class Controls:
    def __init__(self, redis, namespace: str = 'aiqa'):
        self.redis = redis
        self.namespace = namespace

    async def rate_limit(self, identity: str, maximum: int, window: float):
        count, ttl = await self.redis.eval(RATE_SCRIPT, 1, self.namespace + ':rate:' + identity,
                                           max(1, math.ceil(window * 1000)))
        if count > maximum:
            raise RateLimited(max(1, math.ceil(ttl / 1000)))

    @asynccontextmanager
    async def lease(self, maximum: int, seconds: float):
        token = uuid.uuid4().hex
        key = self.namespace + ':provider:leases'
        admitted = await self.redis.eval(LEASE_SCRIPT, 1, key, maximum,
                                         max(1, math.ceil(seconds * 1000)), token)
        if not admitted:
            raise CapacityExceeded()
        try:
            yield
        finally:
            # Shield cleanup against request cancellation. Redis TTL recovers a dead
            # process/outage. Cleanup must not replace completed work or its error.
            try:
                await asyncio.shield(self.redis.eval(RELEASE_SCRIPT, 1, key, token))
            except (RedisError, OSError) as exc:
                logger.warning('provider_lease_release_failed error_type=%s', type(exc).__name__)
