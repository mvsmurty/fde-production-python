from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIAGE_", env_file=".env")

    llm_provider: Literal["mock", "xai"] = "mock"
    xai_api_key: SecretStr | None = None
    xai_model: str = "grok-4-fast-non-reasoning"
    xai_base_url: str = "https://api.x.ai/v1"

    llm_timeout_s: float = 3.0
    llm_max_attempts: int = 2
    llm_retry_backoff_s: float = 0.3
    # Never sleep longer than this on a provider's Retry-After; fail instead and let the caller retry.
    llm_max_retry_after_s: float = 5.0
    # Cap on LLM calls in flight from this process, to stay under the provider's rate limit.
    llm_max_concurrency: int = 8
    rate_limit_requests: int = 5
    rate_limit_window_s: float = 10.0
    # A ticket is a few KB. Anything far bigger is rejected before its body is read.
    max_body_bytes: int = 64 * 1024
    log_level: str = "INFO"
    # Enables the chaos header, span tracking and the /demo endpoints. Never on in a real deployment.
    demo_mode: bool = True

    @model_validator(mode="after")
    def require_key_for_xai(self) -> Self:
        if self.llm_provider == "xai" and not self.xai_api_key:
            raise ValueError("TRIAGE_XAI_API_KEY must be set when TRIAGE_LLM_PROVIDER=xai")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
