from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIAGE_", env_file=".env")

    llm_timeout_s: float = 3.0
    llm_max_attempts: int = 2
    llm_retry_backoff_s: float = 0.3
    log_level: str = "INFO"
    # Enables the chaos header, span headers and the /demo endpoints. Never on in a real deployment.
    demo_mode: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
