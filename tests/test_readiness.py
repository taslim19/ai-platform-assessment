"""Readiness deadline regressions; stalled cleanup is always explicitly released."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.config import Settings
import app.main as main


class Engine:
    disposed = False

    async def dispose(self):
        self.disposed = True


class Dependencies:
    def __init__(self):
        self.started = asyncio.Event()
        self.cleanup = asyncio.Event()
        self.release = asyncio.Event()
        self.stall = True
        self.starts = 0
        self.cancel_count = 0
        self.uncancel = False
        self.statements = []
        self.error = None
        self.redis_error = None
        self.pings = 0

    @asynccontextmanager
    async def session(self):
        self.starts += 1
        try:
            yield self
        finally:
            if self.stall:
                self.cleanup.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_count += 1
                    raise

    async def execute(self, statement):
        self.statements.append(str(statement))
        self.started.set()
        if self.error:
            raise self.error
        if self.stall:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancel_count += 1
                if self.uncancel:
                    asyncio.current_task().uncancel()
                raise

    async def ping(self):
        self.pings += 1
        if self.redis_error:
            raise self.redis_error


@pytest.fixture
async def readiness(monkeypatch):
    deps, engine = Dependencies(), Engine()
    monkeypatch.setattr(main, 'database', lambda settings: (engine, deps.session))
    settings = Settings(database_url='sqlite+aiosqlite://',
                        jwt_secret='test-only-secret-not-for-deployment-123456789')
    provider = httpx.AsyncClient()
    app = main.create_app(settings, redis_client=deps, provider_client=provider)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    tasks = []
    try:
        yield app, client, deps, engine, tasks
    finally:
        deps.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if hasattr(app.state, 'readiness'):
            await app.state.readiness.close()
        await client.aclose()
        await provider.aclose()


async def test_readiness_deadline_does_not_wait_for_session_cleanup(readiness):
    app, client, deps, _, tasks = readiness
    # Exercise the production three-second budget, not a monkeypatched timeout.
    request = asyncio.create_task(client.get('/health'))
    tasks.append(request)
    await deps.started.wait()
    done, _ = await asyncio.wait([request], timeout=3.3)
    assert done, 'readiness deadline waited for cancellation-resistant DB cleanup'
    response = request.result()
    assert response.status_code == 503
    assert response.json()['detail'] == 'Dependencies not ready'
    assert deps.cleanup.is_set() and not deps.release.is_set()
    assert (await client.get('/health/live')).status_code == 200


async def test_single_flight_and_no_repeated_cancellation_during_cleanup(readiness):
    app, client, deps, _, tasks = readiness
    app.state.readiness.timeout = 0.03
    tasks.extend(asyncio.create_task(client.get('/health')) for _ in range(12))
    responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 503 for r in responses)
    assert deps.starts == 1
    assert deps.cancel_count == 1
    for _ in range(12):
        assert (await client.get('/health')).status_code == 503
    assert deps.starts == 1 and deps.cancel_count == 1
    deps.stall = False
    deps.release.set()
    await asyncio.wait([app.state.readiness.task], timeout=1)
    assert (await client.get('/health')).status_code == 200
    assert deps.starts == 2
    assert any('users.id' in sql for sql in deps.statements)
    assert any('chat_records.id' in sql for sql in deps.statements)
    assert deps.pings == 1


@pytest.mark.parametrize('dependency', ['database', 'redis'])
async def test_dependency_errors_are_sanitized(readiness, dependency):
    _, client, deps, _, _ = readiness
    deps.stall = False
    error = RuntimeError('secret-password-in-driver-error')
    if dependency == 'database':
        deps.error = error
    else:
        deps.redis_error = error
    response = await client.get('/health')
    assert response.status_code == 503
    assert 'secret-password' not in response.text
    assert response.json()['detail'] == 'Dependencies not ready'


@pytest.mark.parametrize('uncancel', [False, True])
async def test_shutdown_wait_is_bounded_and_does_not_recancel_cleanup(readiness, uncancel):
    app, client, deps, engine, tasks = readiness
    deps.uncancel = uncancel
    app.state.readiness.timeout = 0.02
    tasks.append(asyncio.create_task(client.get('/health')))
    assert (await tasks[0]).status_code == 503
    # Shutdown uses the same bounded drain budget; it cannot force a broken
    # driver's cleanup to stop, but must not issue another destructive cancel.
    async def shutdown():
        async with app.router.lifespan_context(app):
            pass

    tasks.append(asyncio.create_task(shutdown()))
    done, _ = await asyncio.wait([tasks[-1]], timeout=0.3)
    assert done, 'shutdown waited indefinitely for readiness cleanup'
    tasks[-1].result()
    assert engine.disposed
    assert deps.cancel_count == 1
    assert not app.state.readiness.task.done()
    assert (await client.get('/health')).status_code == 503
    deps.release.set()
    await asyncio.wait([app.state.readiness.task], timeout=1)
    assert app.state.readiness.task.done()


async def test_caller_cancellation_does_not_abandon_probe_deadline(readiness):
    app, client, deps, _, tasks = readiness
    app.state.readiness.timeout = 0.03
    tasks.append(asyncio.create_task(client.get('/health')))
    await deps.started.wait()
    tasks[0].cancel()
    await asyncio.gather(tasks[0], return_exceptions=True)
    tasks.append(asyncio.create_task(deps.cleanup.wait()))
    done, _ = await asyncio.wait([tasks[-1]], timeout=1)
    assert done
    assert deps.cancel_count == 1
    assert (await client.get('/health')).status_code == 503
