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
        yield
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
        response = await call_next(request)
        route = request.scope.get('route')
        # Never label arbitrary paths, queries, usernames, model names or request IDs.
        label = route.path if route else 'unmatched'
        method = request.method if request.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') else 'OTHER'
        elapsed = time.perf_counter() - start
        metrics.http_requests.labels(method, label, str(response.status_code)).inc()
        metrics.http_latency.labels(method, label).observe(elapsed)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        logger.info(json.dumps({'event':'request', 'request_id': request.state.request_id,
                               'method':method, 'route':label, 'status':response.status_code,
                               'latency_ms':round(elapsed*1000)}))
        return response

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

    @application.get('/health')
    async def ready(request: Request):
        try:
            async with asyncio.timeout(3):
                async with sessions() as session:
                    # Verify both connectivity and application schema, not just TCP/SELECT 1.
                    await session.execute(select(User.id).limit(1))
                    await session.execute(select(ChatRecord.id).limit(1))
                await redis.ping()
        except (SQLAlchemyError, RedisError, OSError):
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
            for kind in ('prompt_tokens','completion_tokens'):
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
            await redis.set(cache_key, json.dumps(result), ex=settings.cache_ttl_seconds)
        return ChatResponse(**result, request_id=request.state.request_id, latency_ms=elapsed, cached=cached)

    return application


app = create_app()
