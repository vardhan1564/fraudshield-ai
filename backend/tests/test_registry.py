"""Model registry on real (small) trained models: loads once, scores like the ML pipeline does,
fails clearly when an artifact is missing, and startup handles missing models per environment."""

import runpy
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app, load_models_and_rules
from app.ml_runtime.registry import FastMeta, ModelRegistry
from ml.features.schema import FEATURE_NAMES
from ml.models.meta_model import risk_score
from ml.pipelines.datasets import Dataset
from ml.tests.conftest import config_for, mini_artifacts  # noqa: F401  # pytest fixture


@pytest.fixture(scope="module")
def model_dir(
    mini_artifacts: tuple[Path, Dataset],  # noqa: F811
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Mini base models + a meta-model built like `python -m ml.models.meta_model`."""
    folder = tmp_path_factory.mktemp("models")
    shutil.copytree(mini_artifacts[0], folder, dirs_exist_ok=True)
    argv = ["meta_model", "--config", str(config_for(folder)), "--artifacts", str(folder)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [*argv, "--no-mlflow"])
        with pytest.raises(SystemExit) as done:
            runpy.run_module("ml.models.meta_model", run_name="__main__", alter_sys=True)
    assert done.value.code == 0
    return folder


def test_registry_scores_like_the_ml_pipeline(
    model_dir: Path,
    mini_artifacts: tuple[Path, Dataset],  # noqa: F811
) -> None:
    registry = ModelRegistry.load(model_dir)
    assert registry.version.startswith("ensemble-") and 0 < registry.step_up <= registry.hold
    X = mini_artifacts[1].X
    for i in (0, len(X) // 2, len(X) - 1):
        row = X.iloc[[i]]
        out = registry.predict({n: row[n].iloc[0] for n in FEATURE_NAMES})
        expected = risk_score(registry.meta, registry.base.scores(row), row, registry.boost)[0]
        assert out.risk_score == pytest.approx(expected, abs=1e-9)
        assert all(0.0 <= v <= 1.0 for v in out.scores.values())
        assert set(out.contributions) == set(FEATURE_NAMES)
        assert np.isfinite(list(out.contributions.values())).all()


def test_missing_artifact_is_a_clear_error(model_dir: Path, tmp_path: Path) -> None:
    for f in model_dir.iterdir():
        if f.name != "meta_model.pkl" and f.is_file():
            shutil.copy(f, tmp_path / f.name)
    with pytest.raises(FileNotFoundError, match="meta_model.pkl"):
        ModelRegistry.load(tmp_path)


def _app(tmp_path: Path, env: str) -> tuple[FastAPI, Settings]:
    # An unreachable database: rules loading must not crash startup outside production.
    settings = Settings(
        _env_file=None,
        app_env=env,  # type: ignore[arg-type]
        model_dir=tmp_path,
        secret_key="x" * 40,
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
    )
    return create_app(settings), settings


async def test_startup_without_models(tmp_path: Path) -> None:
    app, settings = _app(tmp_path, "test")
    await load_models_and_rules(app, settings)  # dev/test: keep running, score -> 503
    assert app.state.registry is None and app.state.rules.rules == []
    await app.state.engine.dispose()

    prod, prod_settings = _app(tmp_path, "production")
    with pytest.raises(FileNotFoundError):  # production: fail fast
        await load_models_and_rules(prod, prod_settings)
    await prod.state.engine.dispose()


async def test_startup_loads_real_models(model_dir: Path) -> None:
    app, settings = _app(model_dir, "test")
    await load_models_and_rules(app, settings)
    assert isinstance(app.state.registry, ModelRegistry)
    await app.state.engine.dispose()


def test_fast_meta_equals_sklearn(model_dir: Path) -> None:
    """The numpy meta-model (T3.9 hot path) gives sklearn's predict_proba to 1e-12."""
    registry = ModelRegistry.load(model_dir)
    assert registry.fast_meta is not None
    rng = np.random.default_rng(7)
    x = np.column_stack([rng.random(2000), rng.random(2000), rng.random(2000),
                         rng.choice([0.0, 0.3, 1.0], 2000)])  # fmt: skip
    x[:10, [0, 2]] = [[0.0, 1.0]] * 10  # clipped logits at the edges
    want = registry.meta.predict_proba(x)[:, 1]
    names = ("txn_score", "anomaly_score", "social_eng_score", "graph_score")
    got = [registry.fast_meta.predict(dict(zip(names, row, strict=True))) for row in x]
    np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)


def test_fast_meta_falls_back_for_other_models() -> None:
    assert FastMeta.of(object()) is None
