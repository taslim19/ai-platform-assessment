from prometheus_client import CollectorRegistry, Counter, Histogram


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.http_requests = Counter('aiqa_http_requests_total', 'HTTP responses', ['method', 'route', 'status'], registry=self.registry)
        self.http_latency = Histogram('aiqa_http_request_duration_seconds', 'HTTP latency', ['method', 'route'], registry=self.registry)
        self.llm_requests = Counter('aiqa_llm_requests_total', 'Provider attempts', ['target', 'outcome'], registry=self.registry)
        self.llm_latency = Histogram('aiqa_llm_request_duration_seconds', 'Provider latency', ['target'], registry=self.registry)
        self.llm_retries = Counter('aiqa_llm_retries_total', 'Provider retries', ['target'], registry=self.registry)
        self.tokens = Counter('aiqa_llm_tokens_total', 'Actual provider token consumption (cache excluded)', ['type'], registry=self.registry)
        self.cache = Counter('aiqa_cache_requests_total', 'Cache outcomes', ['outcome'], registry=self.registry)
