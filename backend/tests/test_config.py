import pytest
from pydantic import ValidationError

from app.core.config import REPO_ROOT, Settings


def test_env_example_parses_with_inline_comments() -> None:
    settings = Settings(_env_file=REPO_ROOT / ".env.example")

    assert settings.graph_backend == "networkx"  # value has a trailing "# ..." comment
    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_api_key is None  # empty value means "not set"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings.model_dir == REPO_ROOT / "ml" / "artifacts"


def test_cors_origins_split_on_commas() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test")

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="THRESHOLD_APPROVE"):
        Settings(_env_file=None, threshold_approve=0.8, threshold_hold=0.7)


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, app_env="production")

    ok = Settings(_env_file=None, app_env="production", secret_key="x" * 40)
    assert ok.app_env == "production"
