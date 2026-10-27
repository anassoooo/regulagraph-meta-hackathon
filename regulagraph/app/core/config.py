"""Environment-based settings.

Values come from process environment variables or a local ``.env`` file.
The API key is a ``SecretStr`` so it never appears in ``repr``/logs.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    app_name: str = "RegulaGraph"
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Meta Model API (https://dev.meta.ai/docs/api-reference/chat-completions).
    meta_api_key: SecretStr | None = Field(default=None, alias="MODEL_API_KEY")
    meta_api_base_url: str = Field(default="https://api.meta.ai/v1", alias="META_API_BASE_URL")
    meta_model_id: str = Field(default="muse-spark-1.3", alias="META_MODEL_ID")
    meta_inference_enabled: bool = Field(default=False, alias="META_INFERENCE_ENABLED")
    meta_max_completion_tokens: int = Field(default=1024, gt=0, le=32768, alias="META_MAX_COMPLETION_TOKENS")
    meta_request_timeout_seconds: float = Field(default=60.0, gt=0, alias="META_REQUEST_TIMEOUT_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
