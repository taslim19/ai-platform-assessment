import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx
import jwt
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.controls import Controls, RateLimited, CapacityExceeded
from app.db import database, User, ChatRecord
from app.gateway import Gateway, ProviderError, ProviderTimeout
from app.metrics import Metrics
from app.schemas import LoginRequest, TokenResponse, ChatRequest, ChatResponse
from app.security import hash_password, verify_password, issue_token, decode_token

logger = logging.getLogger('aiqa')
# Suppress HTTP client URL logging (endpoints may contain sensitive routing data).
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
bearer = HTTPBearer(auto_error=False)
DUMMY_HASH = hash_password(uuid.uuid4().hex)


class _Readiness:
    """One dependency probe per worker, with a caller-independent hard deadline.

    asyncio.timeout/wait_for wait for cancellation cleanup; a DB driver may
    shield that cleanup indefinitely. Never await that cleanup in an HTTP
    request, and never spawn replacement probes while one is still draining.
    """

    def __init__(self, check, timeout=3.0):
        self.check = check
        self.timeout = timeout
        self.task = None
        self.deadline = 0.0
        self.closed = False
        self.cancelled_task = None

    def _cancel_once(self, task):
        # Track our own cancellation, not Task.cancelling(): driver cleanup may
        # call uncancel() internally, but still must not receive a second one.
        if not task.done() and self.cancelled_task is not task:
            self.cancelled_task = task
            task.cancel()

    async def _run(self):
        try:
            await self.check()
        except Exception as exc:
            # Driver errors can contain credentials/SQL: record only the type.
            logger.warning('readiness_failed error_type=%s', type(exc).__name__)
            return False
        return True

    async def ready(self):
        if self.closed:
            return False
        loop = asyncio.get_running_loop()
        if self.task is None or self.task.done():
            self.deadline = loop.time() + self.timeout
            self.task = asyncio.create_task(self._run(), name='readiness-probe')
            timer = loop.call_at(self.deadline, self._cancel_once, self.task)

            def finished(task):
                timer.cancel()
                if not task.cancelled():
                    task.exception()  # Retrieve failures even if all callers disconnect.

            self.task.add_done_callback(finished)
        task, deadline = self.task, self.deadline
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        # asyncio.wait does not cancel/await the child on timeout or when this
        # caller disconnects. The independent timer cancels the probe once.
        done, _ = await asyncio.wait([task], timeout=remaining)
        return bool(done and loop.time() < deadline and not task.cancelled() and task.result())

    async def close(self):
        self.closed = True
        if self.task is not None:
            self._cancel_once(self.task)
            _, pending = await asyncio.wait([self.task], timeout=self.timeout)
            if pending:
                # Python cannot force-stop cancellation-resistant coroutines.
                # Retain the single tracked task until cleanup or process exit.
                logger.warning('readiness_cleanup_pending')


def create_app(settings: Settings | None = None, *, redis_client=None, provider_client=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = database(settings)
    redis = redis_client if redis_client is not None else Redis.from_url(settings.redis_url,
        decode_responses=True, socket_connect_timeout=2, socket_timeout=2,
        max_connections=settings.redis_max_connections)
    client = provider_client if provider_client is not None else httpx.AsyncClient(
        limits=httpx.Limits(max_connections=settings.llm_max_concurrency,
                           max_keepalive_connections=settings.llm_max_concurrency),
        follow_redirects=False)
    metrics = Metrics()
    controls = Controls(redis)
    gateway = Gateway(settings, client, metrics)

    @asynccontextmanager
    async def lifespan(application):
        # Migrations are an explicit release step, NEVER startup create_all.
        try:
            yield
        finally:
            await readiness.close()
            if provider_client is None:
                await client.aclose()
            if redis_client is None:
                await redis.aclose()
            await engine.dispose()

    application = FastAPI(title='AIQA Platform', version='1.0.0', lifespan=lifespan,
                          docs_url='/docs' if settings.app_env == 'development' else None,
                          redoc_url=None)
    application.state.settings = settings
    application.state.engine = engine
    application.state.sessions = sessions
    application.state.redis = redis
    application.state.gateway = gateway
    application.state.metrics = metrics

    @application.middleware('http')
    async def observe(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['X-Request-ID'] = request.state.request_id
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        finally:
            # Observe even unexpected endpoint/dependency failures, then let the
            # framework propagate them. Never record exception text or inputs.
            route = request.scope.get('route')
            label = route.path if route else 'unmatched'
            method = request.method if request.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') else 'OTHER'
            elapsed = time.perf_counter() - start
            metrics.http_requests.labels(method, label, str(status)).inc()
            metrics.http_latency.labels(method, label).observe(elapsed)
            logger.info(json.dumps({'event':'request', 'request_id': request.state.request_id,
                                   'method':method, 'route':label, 'status':status,
                                   'latency_ms':round(elapsed*1000)}))

    def failure(request, status, detail, headers=None):
        return JSONResponse(status_code=status, content={'detail':detail,
            'request_id':getattr(request.state, 'request_id', '')}, headers=headers)

    @application.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Pydantic error structures contain the original input; never reflect passwords/prompts.
        return failure(request, 422, 'Invalid request body or parameters')

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return failure(request, 503, 'Database unavailable')

    @application.exception_handler(OSError)
    async def dependency_connection_error(request, exc):
        return failure(request, 503, 'Dependency connection unavailable')

    @application.exception_handler(RedisError)
    async def redis_error(request, exc):
        return failure(request, 503, 'Redis unavailable; request not admitted')

    @application.exception_handler(RateLimited)
    async def rate_error(request, exc):
        return failure(request, 429, 'Rate limit exceeded', {'Retry-After':str(exc.retry_after)})

    @application.exception_handler(CapacityExceeded)
    async def capacity_error(request, exc):
        return failure(request, 503, 'Provider capacity exhausted', {'Retry-After':'1'})

    @application.exception_handler(ProviderTimeout)
    async def timeout_error(request, exc):
        return failure(request, 504, 'Provider deadline exceeded')

    @application.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return failure(request, 502, 'Provider unavailable or invalid response')

    async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        unauthorized = HTTPException(401, 'Invalid or missing authentication', headers={'WWW-Authenticate':'Bearer'})
        if credentials is None:
            raise unauthorized
        try:
            claims = decode_token(credentials.credentials, settings)
        except jwt.InvalidTokenError:
            raise unauthorized
        async with sessions() as session:
            user = await session.get(User, claims['sub'])
        if user is None or not user.active:
            raise unauthorized
        return user

    @application.post('/auth/login', response_model=TokenResponse)
    async def login(body: LoginRequest, request: Request):
        # Trust only direct peer address; forwarded headers must be configured at the trusted proxy.
        peer = request.client.host if request.client else 'unknown'
        identity = hashlib.sha256(peer.encode()).hexdigest()
        await controls.rate_limit('login:' + identity, settings.login_rate_limit_requests,
                                  settings.login_rate_limit_window_seconds)
        async with sessions() as session:
            user = (await session.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
        verified = await run_in_threadpool(verify_password, body.password, user.password_hash if user else DUMMY_HASH)
        if not verified or user is None or not user.active:
            raise HTTPException(401, 'Invalid credentials', headers={'WWW-Authenticate':'Bearer'})
        return TokenResponse(access_token=issue_token(user.id, settings), expires_in=settings.jwt_expiry_minutes*60)

    @application.get('/health/live')
    async def live():
        return {'status':'alive'}

    async def check_dependencies():
        async with sessions() as session:
            # Verify connectivity AND application schema, not just TCP/SELECT 1.
            await session.execute(select(User.id).limit(1))
            await session.execute(select(ChatRecord.id).limit(1))
        await redis.ping()

    readiness = _Readiness(check_dependencies)
    application.state.readiness = readiness

    @application.get('/health')
    async def ready(request: Request):
        if not await readiness.ready():
            return failure(request, 503, 'Dependencies not ready')
        return {'status':'ready', 'database':'ok', 'redis':'ok'}

    @application.get('/metrics', response_class=Response)
    async def prometheus(user: User = Depends(current_user)):
        if user.role != 'admin':
            raise HTTPException(403, 'Admin role required')
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @application.post('/chat', response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, user: User = Depends(current_user)):
        if user.role not in ('admin','user'):
            raise HTTPException(403, 'User or Admin role required')
        start = time.perf_counter()
        await controls.rate_limit('chat:' + user.id, settings.rate_limit_requests, settings.rate_limit_window_seconds)
        # HMAC prevents dictionary recovery of low-entropy questions from cache key dumps.
        material = json.dumps([user.id, settings.llm_base_url, settings.llm_model,
                               settings.llm_fallback_base_url, settings.llm_fallback_model,
                               settings.llm_max_output_tokens, body.question], separators=(',', ':'))
        cache_key = 'aiqa:chat:' + hmac.new(settings.jwt_secret.get_secret_value().encode(),
                                          material.encode(), hashlib.sha256).hexdigest()
        result, cached = None, False
        if settings.cache_ttl_seconds:
            stored = await redis.get(cache_key)
            if stored:
                try:
                    result = json.loads(stored)
                    # Validate cached data before using it; malformed entries are never served.
                    ChatResponse(**result, request_id=request.state.request_id, latency_ms=0, cached=True)
                    cached = True
                except (ValueError, TypeError):
                    result = None
                    await redis.delete(cache_key)
            metrics.cache.labels('hit' if cached else 'miss').inc()
        else:
            metrics.cache.labels('disabled').inc()
        if result is None:
            # Lease outlives the bounded provider deadline, including retry sleeps and cancellation.
            async with controls.lease(settings.llm_max_concurrency, settings.llm_total_timeout_seconds + 5):
                result = await gateway.complete(body.question)
            # Preserve the provider's total: reasoning tokens need not appear in visible completions.
            for kind in ('prompt_tokens','completion_tokens','total_tokens'):
                metrics.tokens.labels(kind).inc(result['usage'][kind])
        elapsed = int((time.perf_counter() - start) * 1000)
        # Close DB sessions before network I/O; short writes avoid pinning a pool connection.
        async with sessions() as session:
            session.add(ChatRecord(request_id=request.state.request_id, user_id=user.id,
                model=result['model'], **result['usage'], latency_ms=elapsed, cached=cached,
                question=body.question if settings.store_chat_content else None,
                answer=result['answer'] if settings.store_chat_content else None))
            await session.commit()
        if settings.cache_ttl_seconds and not cached:
            try:
                await redis.set(cache_key, json.dumps(result), ex=settings.cache_ttl_seconds)
            except (RedisError, OSError) as exc:
                # Paid work and its audit commit are complete. Optional cache
                # population must not provoke a caller retry or duplicate charge.
                logger.warning('cache_population_failed error_type=%s', type(exc).__name__)
        return ChatResponse(**result, request_id=request.state.request_id, latency_ms=elapsed, cached=cached)

    return application


app = create_app()
