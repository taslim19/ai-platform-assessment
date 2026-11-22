"""Small in-process OpenAI-compatible gateway. The endpoint can also be LiteLLM."""
import asyncio
import random
import time
import httpx
from pydantic import BaseModel, Field, ValidationError
from app.config import Settings


class ProviderError(Exception):
    pass


class ProviderTimeout(ProviderError):
    pass


class Usage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class Gateway:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, metrics=None):
        self.settings, self.client, self.metrics = settings, client, metrics

    async def complete(self, question: str) -> dict:
        try:
            async with asyncio.timeout(self.settings.llm_total_timeout_seconds):
                return await self._complete(question)
        except TimeoutError as exc:
            raise ProviderTimeout('Provider deadline exceeded') from exc

    async def _complete(self, question: str) -> dict:
        s = self.settings
        targets = [(s.llm_base_url, s.llm_model, s.llm_api_key)]
        if s.llm_fallback_model:
            targets.append((s.llm_fallback_base_url or s.llm_base_url,
                            s.llm_fallback_model, s.llm_fallback_api_key or s.llm_api_key))
        last_error = ProviderError('Provider unavailable')
        deadline = asyncio.get_running_loop().time() + s.llm_total_timeout_seconds
        for target_index, (base, model, key) in enumerate(targets):
            target_label = 'primary' if target_index == 0 else 'fallback'
            # Reserve an equal share of the remaining budget for each remaining
            # target. A stalled primary must not starve a configured fallback.
            budget = max(0, deadline - asyncio.get_running_loop().time()) / (len(targets) - target_index)
            try:
                async with asyncio.timeout(budget):
                    result, last_error = await self._target_complete(question, base, model, key, target_label)
                if result is not None:
                    return result
            except TimeoutError:
                last_error = ProviderTimeout('Provider target deadline exceeded')
        raise last_error

    async def _target_complete(self, question, base, model, key, target_label):
        s = self.settings
        last_error = ProviderError('Provider unavailable')
        for attempt in range(s.llm_max_retries + 1):
            started = time.perf_counter()
            outcome = 'error'
            try:
                response = await self.client.post(base.rstrip('/') + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + key.get_secret_value()},
                    json={'model': model, 'messages': [{'role': 'user', 'content': question}],
                          'max_tokens': s.llm_max_output_tokens, 'stream': False},
                    timeout=s.llm_timeout_seconds)
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = ProviderError('Provider temporarily unavailable')
                elif response.status_code >= 400:
                    # Invalid input/credentials must not trigger repeated paid requests or fallback.
                    raise ProviderError('Provider rejected request')
                else:
                    try:
                        data = response.json()
                        answer = data['choices'][0]['message']['content']
                        if not isinstance(answer, str) or not answer:
                            raise ValueError('Missing text answer')
                        usage = Usage.model_validate(data['usage']).model_dump()
                        actual_model = data.get('model', model)
                        if not isinstance(actual_model, str):
                            raise ValueError('Invalid model')
                    except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                        raise ProviderError('Malformed provider response') from exc
                    outcome = 'success'
                    return {'answer': answer, 'model': actual_model, 'usage': usage}, None
            except httpx.TimeoutException:
                last_error = ProviderTimeout('Provider timed out')
            except httpx.ConnectError:
                last_error = ProviderError('Provider connection failed')
            except httpx.RequestError as exc:
                # Other protocol/transport failures require explicit caller review.
                raise ProviderError('Provider connection failed') from exc
            finally:
                if self.metrics:
                    self.metrics.llm_requests.labels(target_label, outcome).inc()
                    self.metrics.llm_latency.labels(target_label).observe(time.perf_counter() - started)
            if attempt < s.llm_max_retries:
                if self.metrics:
                    self.metrics.llm_retries.labels(target_label).inc()
                await asyncio.sleep(random.uniform(0.05, 0.2) * 2 ** attempt)
        return None, last_error
