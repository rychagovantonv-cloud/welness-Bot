from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr
    allowed_users: Annotated[list[int], NoDecode] = Field(default_factory=list)

    database_url: str

    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-haiku-4-5"

    github_token: SecretStr | None = None
    github_content_repo: str | None = None

    youtube_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"

    sentry_dsn: SecretStr | None = None
    log_level: str = "INFO"
    env: Literal["dev", "prod"] = "prod"

    # Budget tracking (no hard cap, только информационные индикаторы).
    budget_monthly_usd: Decimal = Decimal("10.00")

    port: int = 8080

    @field_validator("allowed_users", mode="before")
    @classmethod
    def _parse_allowed_users(cls, v: object) -> list[int]:
        if isinstance(v, list):
            return [int(x) for x in v]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return []

    @field_validator("database_url", mode="after")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        # Railway отдаёт postgresql://..., нам для async нужен postgresql+asyncpg://...
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()  # type: ignore[call-arg]
