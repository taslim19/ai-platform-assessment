import asyncio
import httpx
import pytest
from app.config import Settings


def settings(**kwargs):
    return Settings(jwt_secret='x'*48, llm_api_key='test-key', **kwargs)


def completion(model='primary'):
    return {'model': model, 'choices': [{'message': {'content': 'Helpful answer'}}],
            'usage': {'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6}}


async def test_gateway_retries_retriable_status_then_succeeds():
    from app.gateway import Gateway
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(429 if len(requests) < 3 else 200, json=completion())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        gateway = Gateway(settings(llm_max_retries=2), client)
        result = await gateway.complete('hi')
    assert len(requests) == 3
    assert result['usage']['total_tokens'] == 6
    assert requests[0].headers['authorization'] == 'Bearer test-key'


async def test_gateway_fallback_and_no_retry_on_bad_request():
    from app.gateway import Gateway, ProviderError
    calls = []
    def provider(request):
        calls.append(str(request.url))
        return httpx.Response(503 if 'primary' in request.url.host else 200, json=completion('fallback'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        gateway = Gateway(settings(llm_base_url='https://primary/v1', llm_fallback_base_url='https://backup/v1',
                                   llm_fallback_model='fallback', llm_max_retries=0), client)
        result = await gateway.complete('hi')
    assert result['model'] == 'fallback'
    assert len(calls) == 2
    calls.clear()
    def reject(request):
        calls.append(request)
        return httpx.Response(400, json={'error': 'bad request'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
        with pytest.raises(ProviderError):
            await Gateway(settings(llm_max_retries=3, llm_fallback_model='fallback'), client).complete('hi')
    assert len(calls) == 1


async def test_gateway_total_deadline_and_transport_timeout():
    from app.gateway import Gateway, ProviderTimeout
    async def slow(request):
        await asyncio.sleep(.2)
        return httpx.Response(200, json=completion())
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        with pytest.raises(ProviderTimeout):
            await Gateway(settings(llm_total_timeout_seconds=.03), client).complete('hi')
    calls = []
    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout('timeout', request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderTimeout):
            await Gateway(settings(llm_max_retries=1), client).complete('hi')
    assert len(calls) == 2


async def test_fallback_has_reserved_time_when_primary_stalls():
    from app.gateway import Gateway
    calls = []
    async def provider(request):
        calls.append(request.url.host)
        if request.url.host == 'primary':
            await asyncio.sleep(1)
        return httpx.Response(200, json=completion('fallback'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Gateway(settings(
            llm_base_url='https://primary/v1', llm_fallback_base_url='https://backup/v1',
            llm_fallback_model='fallback', llm_total_timeout_seconds=.2,
            llm_timeout_seconds=1, llm_max_retries=2), client).complete('hi')
    assert result['model'] == 'fallback'
    assert calls == ['primary', 'backup']


async def test_connection_failure_reaches_configured_fallback():
    from app.gateway import Gateway
    calls = []
    def provider(request):
        calls.append(request.url.host)
        if request.url.host == 'primary':
            raise httpx.ConnectError('provider unavailable', request=request)
        return httpx.Response(200, json=completion('fallback'))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result = await Gateway(settings(
            llm_base_url='https://primary/v1', llm_fallback_base_url='https://backup/v1',
            llm_fallback_model='fallback', llm_max_retries=0), client).complete('hi')
    assert result['model'] == 'fallback'
    assert calls == ['primary', 'backup']


async def test_gateway_rejects_malformed_response():
    from app.gateway import Gateway, ProviderError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client:
        with pytest.raises(ProviderError):
            await Gateway(settings(), client).complete('hi')
