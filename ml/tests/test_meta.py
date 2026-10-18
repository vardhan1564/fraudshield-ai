"""Meta-model: input transform, thresholds, decisions, calibration, and a full mini pipeline."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ml.features.build import training_frame
from ml.models import anomaly_model, social_model
from ml.models.meta_model import (
    SCORE_COLUMNS,
    BaseModels,
    _logit_probabilities,
    apply_rules,
    choose_thresholds,
    decide,
    main,
    make_meta,
    rule_hits,
    train_meta,
)
from ml.pipelines.datasets import from_training_frame
from ml.pipelines.metrics import threshold_for_fpr
from ml.pipelines.train import DEFAULT_CONFIG, train_model
from ml.simulator.config import load_config
from ml.simulator.run import simulate

CFG = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
CFG["xgboost"]["num_boost_round"] = 150
CFG["anomaly"]["autoencoder"]["epochs"] = 10
CFG["social"]["lightgbm"]["num_boost_round"] = 150
THRESHOLD_CFG = {"hold_fpr": 0.01, "step_up_fpr": 0.05}


def test_logit_only_touches_the_probability_columns() -> None:
    x = np.array([[0.5, 0.3, 0.5, 0.0], [0.9, 0.7, 0.1, 0.0], [1.0, 1.0, 0.0, 0.0]])
    out = _logit_probabilities(x)
    np.testing.assert_allclose(out[:, [1, 3]], x[:, [1, 3]])  # anomaly and graph unchanged
    assert out[0, 0] == pytest.approx(0.0) and out[1, 0] == pytest.approx(np.log(9))
    assert np.isfinite(out).all()  # 0 and 1 are clipped, never infinite
    np.testing.assert_allclose(
        x, [[0.5, 0.3, 0.5, 0.0], [0.9, 0.7, 0.1, 0.0], [1.0, 1.0, 0.0, 0.0]]
    )


def test_thresholds_meet_the_fpr_targets_and_are_ordered() -> None:
    rng = np.random.default_rng(0)
    y = (rng.random(20_000) < 0.03).astype(int)
    oof = np.clip(rng.normal(0.2 + 0.5 * y, 0.15), 0, 1)
    th = choose_thresholds(y, oof, THRESHOLD_CFG)
    assert th["step_up"] <= th["hold"]
    assert (oof[y == 0] > th["hold"]).mean() <= 0.01
    assert (oof[y == 0] > th["step_up"]).mean() <= 0.05
    assert th["hold"] == threshold_for_fpr(y, oof, 0.01)


def test_decide() -> None:
    th = {"step_up": 0.2, "hold": 0.7}
    assert decide(np.array([0.1, 0.2, 0.5, 0.7, 0.9]), th).tolist() == [
        "APPROVE",
        "APPROVE",
        "STEP_UP",
        "STEP_UP",
        "HOLD",
    ]


def test_rule_fires_only_on_unknown_call_to_new_payee_and_caps_at_one() -> None:
    X = pd.DataFrame(
        {
            "ctx_unknown_call": [True, True, False, False, True],
            "payee_is_new": [True, False, True, False, True],
        }
    )
    assert rule_hits(X).tolist() == [True, False, False, False, True]
    risk = apply_rules(np.array([0.1, 0.1, 0.1, 0.1, 0.9]), X, 0.25)
    np.testing.assert_allclose(risk, [0.35, 0.1, 0.1, 0.1, 1.0])


def test_calibrated_meta_gives_probabilities_that_rise_with_txn_score() -> None:
    rng = np.random.default_rng(1)
    n = 6000
    y = (rng.random(n) < 0.05).astype(int)
    txn = np.clip(rng.normal(0.1 + 0.7 * y, 0.1), 0, 1)
    X = np.column_stack([txn, rng.random(n), rng.random(n), np.zeros(n)])
    model = make_meta(0).fit(X, y)
    probe = np.column_stack(
        [np.linspace(0.01, 0.99, 50), np.full(50, 0.5), np.full(50, 0.5), np.zeros(50)]
    )
    risk = model.predict_proba(probe)[:, 1]
    assert ((risk >= 0) & (risk <= 1)).all()
    assert (np.diff(risk) >= -1e-9).all()  # isotonic: higher txn_score never lowers risk
    assert risk[-1] > 0.5 > risk[0]


# --- full mini pipeline: base models -> meta-model ---------------------------------------------


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, object]:
    folder = tmp_path_factory.mktemp("artifacts")
    tables = simulate(load_config(n_transactions=30_000, n_users=1_500))
    for name in ("transactions", "users", "devices", "reports"):
        tables[name].to_parquet(folder / f"{name}.parquet", index=False)
    frame = training_frame(
        tables["transactions"], tables["users"], tables["devices"], tables["reports"]
    )
    ds = from_training_frame(frame, warmup_days=14)

    txn = train_model(ds, CFG)
    txn.booster[: txn.params["best_iteration"] + 1].save_model(folder / "txn_model.json")
    (folder / "txn_model.meta.json").write_text(
        json.dumps({"data_hash": txn.data_hash, "version": "test"}), encoding="utf-8"
    )
    anomaly = anomaly_model.train_anomaly(ds, CFG)
    anomaly_model.save(anomaly.winner.model, folder / "anomaly_model.pt", {})
    social = social_model.train_social(ds, CFG)
    social_model.save(social, folder / "social_model.txt")
    return folder, ds


def test_meta_end_to_end(setup: tuple[Path, object]) -> None:
    folder, ds = setup
    base = BaseModels.load(folder)
    scores = base.scores(ds.X.head(200))  # type: ignore[attr-defined]
    assert (
        list(scores.columns) == SCORE_COLUMNS
        and scores.to_numpy().min() >= 0
        and scores.to_numpy().max() <= 1
    )
    assert (scores["graph_score"] == 0).all()

    result = train_meta(ds, base, CFG)  # type: ignore[arg-type]
    assert result.thresholds["step_up"] <= result.thresholds["hold"]
    oof = result.metrics["val_oof"]
    assert oof["fpr"] <= 0.0101 and oof["fpr_step_up_or_hold"] <= 0.0501  # spec 03 section 5
    test = result.metrics["test"]
    assert set(test["decisions"]["legit"]) == {"APPROVE", "STEP_UP", "HOLD"}
    assert test["pr_auc"] > 0.5 and 0 <= test["brier"] < 0.1
    assert set(result.coefficients) == set(SCORE_COLUMNS)
    f2 = result.metrics["f2_unknown_call"]  # spec 01 F2
    assert f2["rows"] > 0 and f2["min_difference"] >= 0.2


def test_meta_refuses_base_models_from_other_data(
    setup: tuple[Path, object], tmp_path: Path
) -> None:
    folder, ds = setup
    for f in folder.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    (tmp_path / "txn_model.meta.json").write_text(
        json.dumps({"data_hash": "different"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="different data"):
        train_meta(ds, BaseModels.load(tmp_path), CFG)  # type: ignore[arg-type]


def test_cli_writes_thresholds_json(setup: tuple[Path, object]) -> None:
    folder, _ = setup
    cfg = {
        **CFG,
        "datasets": {
            "simulated": {
                "features": str(folder / "missing.parquet"),
                "raw_dir": str(folder),
                "warmup_days": 14,
            }
        },
    }
    config = folder / "config.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert main(["--config", str(config), "--artifacts", str(folder), "--no-mlflow"]) == 0
    th = json.loads((folder / "thresholds.json").read_text(encoding="utf-8"))
    assert th["step_up"] <= th["hold"] and th["inputs"] == SCORE_COLUMNS
    assert th["risk_rules"]["unknown_call_new_payee"]["add"] == CFG["meta"]["unknown_call_boost"]
    assert (folder / "meta_model.pkl").exists()


def test_missing_base_model_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="train these first"):
        BaseModels.load(tmp_path)


def test_decisions_frame_shape() -> None:
    risk = pd.Series([0.0, 0.5, 1.0]).to_numpy()
    assert decide(risk, {"step_up": 0.3, "hold": 0.8}).shape == (3,)
