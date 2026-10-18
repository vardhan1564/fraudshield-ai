"""Application settings, read from the repo-root `.env` (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> parents[3] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDER_SECRET = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `ANTHROPIC_API_KEY=` means "not set"
        extra="ignore",  # .env also holds POSTGRES_* and VITE_* for other tools
    )

    # App
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: SecretStr = SecretStr(PLACEHOLDER_SECRET)
    access_token_expire_minutes: int = Field(default=30, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    login_rate_limit_per_minute: int = Field(default=10, gt=0)  # per client IP (spec 08)
    # Partner systems call the API with X-API-Key: "name:key,name2:key2".
    partner_api_keys: SecretStr | None = None

    # Stores (127.0.0.1, not localhost: see the note in .env.example)
    database_url: str = "postgresql+asyncpg://fraudshield:fraudshield@127.0.0.1:5432/fraudshield"
    redis_url: str = "redis://127.0.0.1:6379/0"
    stream_scored: str = "stream:transactions.scored"
    stream_cases: str = "stream:cases.labeled"
    stream_reports: str = "stream:reports.created"

    # Graph
    graph_backend: Literal["networkx", "neo4j"] = "networkx"
    graph_refresh_minutes: int = Field(default=5, gt=0)
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None

    # ML
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    model_dir: Path = Path("ml/artifacts")
    threshold_approve: float = Field(default=0.30, ge=0, le=1)
    threshold_hold: float = Field(default=0.70, ge=0, le=1)

    # LLM
    llm_provider: Literal["anthropic", "ollama", "template"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-haiku-5-5"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    llm_timeout_ms: int = Field(default=1500, gt=0)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, as written in `.env`."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    def partner_keys(self) -> dict[str, str]:
        """{api_key: partner name} from PARTNER_API_KEYS."""
        if self.partner_api_keys is None:
            return {}
        pairs = (item.split(":", 1) for item in self.partner_api_keys.get_secret_value().split(","))
        return {key.strip(): name.strip() for name, key in pairs if key.strip()}

    @field_validator("partner_api_keys")
    @classmethod
    def _check_partner_keys(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(
            ":" not in item for item in value.get_secret_value().split(",")
        ):
            raise ValueError("PARTNER_API_KEYS must look like name:key,name2:key2")
        return value

    @field_validator("model_dir")
    @classmethod
    def _resolve_model_dir(cls, value: Path) -> Path:
        """Relative paths are relative to the repo root, not the current directory."""
        return value if value.is_absolute() else REPO_ROOT / value

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        if self.threshold_approve >= self.threshold_hold:
            raise ValueError("THRESHOLD_APPROVE must be lower than THRESHOLD_HOLD")
        if self.app_env == "production" and (
            self.secret_key.get_secret_value() == PLACEHOLDER_SECRET
            or len(self.secret_key.get_secret_value()) < 32
        ):
            raise ValueError("Set a real SECRET_KEY (32+ characters) in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
