from typing import Literal
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: Literal["development", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./aiqa.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "aiqa"
    jwt_audience: str = "aiqa-api"
    jwt_expiry_minutes: int = Field(default=30, ge=1, le=1440)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "gpt-4o-mini"
    llm_fallback_model: str | None = None
    llm_fallback_base_url: str | None = None
    llm_fallback_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=20, gt=0, le=300)
    llm_total_timeout_seconds: float = Field(default=45, gt=0, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_concurrency: int = Field(default=20, ge=1, le=10000)
    llm_max_output_tokens: int = Field(default=512, ge=1, le=32768)
    cache_ttl_seconds: int = Field(default=0, ge=0, le=86400)
    rate_limit_requests: int = Field(default=30, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    login_rate_limit_requests: int = Field(default=10, ge=1)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1)
    store_chat_content: bool = False
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    redis_max_connections: int = Field(default=100, ge=1)

    @model_validator(mode="after")
    def safe_production(self):
        for url in (self.llm_base_url, self.llm_fallback_base_url):
            if url and not url.startswith(("https://", "http://")):
                raise ValueError("Provider URL must be HTTP(S)")
            if self.app_env == "production" and url and not url.startswith("https://"):
                raise ValueError("Production provider URLs must use HTTPS")
        if self.app_env == "production" and not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("Production requires PostgreSQL asyncpg")
        return self
