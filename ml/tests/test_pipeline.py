"""Training pipeline: time split, metrics on hand-computed scores, and an end-to-end run."""

import json
from pathlib import Path

import numpy as np
import optuna
import pandas as pd
import pytest
import xgboost as xgb
import yaml

from ml.features.build import training_frame
from ml.features.schema import FEATURE_NAMES
from ml.pipelines.datasets import from_training_frame, load_ieee
from ml.pipelines.metrics import (
    classification_metrics,
    fpr_gap,
    recall_at_fpr,
    scenario_recall,
    threshold_for_fpr,
)
from ml.pipelines.model_cards import txn_card
from ml.pipelines.split import time_split
from ml.pipelines.train import Prepared, TrainResult, fit, format_table, prepare, train_model
from ml.pipelines.train import main as train_main
from ml.pipelines.tune import build_meta, save_model, shap_summary, shap_values, tune
from ml.simulator.config import load_config
from ml.simulator.run import simulate
from ml.simulator.schema import SCENARIOS

# --- split -------------------------------------------------------------------------------------


def test_time_split_sizes_and_order() -> None:
    rng = np.random.default_rng(0)
    ts = pd.Series(rng.permutation(1000))  # shuffled times
    s = time_split(ts)
    assert (len(s.train), len(s.val), len(s.test)) == (700, 150, 150)
    assert ts.iloc[s.train].max() < ts.iloc[s.val].min()
    assert ts.iloc[s.val].max() < ts.iloc[s.test].min()
    assert len(np.unique(np.concatenate(s))) == 1000  # every row exactly once


def test_time_split_with_ties_never_goes_backwards() -> None:
    ts = pd.Series([3, 1, 2, 2, 2, 1, 3, 3, 2, 1] * 10)
    s = time_split(ts)
    assert ts.iloc[s.train].max() <= ts.iloc[s.val].min()
    assert ts.iloc[s.val].max() <= ts.iloc[s.test].min()


def test_time_split_rejects_bad_fractions() -> None:
    with pytest.raises(ValueError):
        time_split(pd.Series(range(10)), (0.8, 0.15, 0.15))


# --- metrics -----------------------------------------------------------------------------------

# 100 legit rows scored 0.00 … 0.99; 10 fraud rows: 5 obvious (2.0) and 5 hidden (0.5).
Y = np.array([0] * 100 + [1] * 10)
S = np.concatenate([np.arange(100) / 100, [2.0] * 5, [0.5] * 5])


def test_recall_at_1pct_fpr_by_hand() -> None:
    # Allowing 1 false positive (the 0.99 legit row) catches only the 5 obvious frauds.
    assert recall_at_fpr(Y, S, 0.01) == pytest.approx(0.5)
    # Allowing 50 false positives (FPR 50%) also reaches the hidden ones at 0.5.
    assert recall_at_fpr(Y, S, 0.50) == pytest.approx(1.0)


def test_threshold_for_fpr_by_hand() -> None:
    thr = threshold_for_fpr(Y, S, 0.01)
    assert thr == pytest.approx(0.98)  # flags 0.99 (1 legit) and the five 2.0s
    m = classification_metrics(Y, S, thr)
    assert m["fpr"] == pytest.approx(0.01)
    assert m["recall"] == pytest.approx(0.5)
    # precision 5/6, recall 5/10 -> F1 = 2*(5/6*0.5)/(5/6+0.5) = 0.625
    assert m["f1"] == pytest.approx(0.625)
    assert m["fraud_rate"] == pytest.approx(10 / 110)


def test_threshold_with_no_room_flags_nothing_legit() -> None:
    thr = threshold_for_fpr(Y, S, 0.001)  # 0.1% of 100 legit rows < 1 row
    assert ((thr < S) & (Y == 0)).sum() == 0


def test_scenario_recall() -> None:
    scenario = pd.Series(["a", "a", "b", None, None])
    flagged = np.array([True, False, True, True, False])
    assert scenario_recall(scenario, flagged) == {"a": 0.5, "b": 1.0}
    with_missing = scenario_recall(scenario, flagged, expected=["a", "c"])
    assert with_missing["a"] == 0.5 and np.isnan(with_missing["c"]) and with_missing["b"] == 1.0


# --- datasets ----------------------------------------------------------------------------------


def test_load_ieee_aligns_identity_and_drops_ids(tmp_path: Path) -> None:
    pd.DataFrame(
        {
            "TransactionID": [10, 11, 12, 13],
            "isFraud": [0, 1, 0, 0],
            "TransactionDT": [400, 100, 300, 200],  # file not in time order
            "TransactionAmt": [5.0, 6.0, 7.0, 8.0],
            "ProductCD": ["W", "H", "W", "C"],
        }
    ).to_csv(tmp_path / "train_transaction.csv", index=False)
    pd.DataFrame(
        {"TransactionID": [11, 13], "id_01": [-5.0, -10.0], "DeviceType": ["mobile", "desktop"]}
    ).to_csv(tmp_path / "train_identity.csv", index=False)

    ds = load_ieee(tmp_path)
    assert ds.features == ["TransactionAmt", "ProductCD", "id_01", "DeviceType"]
    assert ds.ts.tolist() == [100, 200, 300, 400]  # sorted by time
    assert ds.y.tolist() == [1, 0, 0, 0]
    assert ds.X["id_01"].tolist()[:2] == [-5.0, -10.0]  # ids 11 and 13 got their identity rows
    assert ds.X["id_01"].isna().tolist()[2:] == [True, True]
    assert ds.X["DeviceType"].dtype == "category" and ds.X["ProductCD"].dtype == "category"
    assert ds.X["TransactionAmt"].dtype == "float32"
    assert (tmp_path / "train_transaction.parquet").exists()  # cached for the next run
    assert len(load_ieee(tmp_path, max_rows=2).y) == 2


@pytest.fixture(scope="module")
def frame() -> pd.DataFrame:
    tables = simulate(load_config(n_transactions=20_000, n_users=1_000))
    return training_frame(
        tables["transactions"], tables["users"], tables["devices"], tables["reports"]
    )


def test_warmup_drops_first_days(frame: pd.DataFrame) -> None:
    ds = from_training_frame(frame, warmup_days=14)
    assert ds.ts.min() >= frame["ts"].min() + pd.Timedelta(days=14)
    assert ds.ts.is_monotonic_increasing
    assert list(ds.X.columns) == FEATURE_NAMES
    assert len(ds.y) == len(ds.X) == len(ds.scenario)


# --- training ----------------------------------------------------------------------------------

FAST_CFG = {
    "seed": 42,
    "split": {"train": 0.70, "val": 0.15, "test": 0.15},
    "target_fpr": 0.01,
    "xgboost": {
        "num_boost_round": 60,
        "early_stopping_rounds": 20,
        "params": {
            "eta": 0.2,
            "max_depth": 4,
            "tree_method": "hist",
            "max_bin": 64,
            "eval_metric": "aucpr",
        },
    },
}


def test_train_end_to_end_on_simulated(frame: pd.DataFrame) -> None:
    ds = from_training_frame(frame, warmup_days=14)
    result = train_model(ds, FAST_CFG)
    assert set(result.metrics) == {"val", "test"}
    for part in ("val", "test"):
        m = result.metrics[part]
        assert 0 <= m["pr_auc"] <= 1 and 0.5 < m["roc_auc"] <= 1
    assert result.metrics["val"]["fpr"] <= 0.01  # threshold is set for 1% FPR on val
    assert result.metrics["test"]["pr_auc"] > 0.5  # the simulated signals are learnable
    assert set(result.scenario_recall) <= set(SCENARIOS)
    assert result.params["scale_pos_weight"] > 1
    assert sum(result.sizes.values()) == len(ds.y)
    assert len(result.data_hash) == 16
    assert "PR-AUC" in format_table(result)


def test_train_is_deterministic(frame: pd.DataFrame) -> None:
    ds = from_training_frame(frame, warmup_days=14)
    a, b = train_model(ds, FAST_CFG), train_model(ds, FAST_CFG)
    assert a.metrics == b.metrics and a.data_hash == b.data_hash


def test_train_cli_without_mlflow(frame: pd.DataFrame, tmp_path: Path) -> None:
    path = tmp_path / "features.parquet"
    frame.to_parquet(path)
    cfg = {**FAST_CFG, "dataset": "simulated", "mlflow": {"experiment": "unused"}}
    cfg["datasets"] = {
        "simulated": {"features": str(path), "raw_dir": str(tmp_path), "warmup_days": 14}
    }
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert train_main(["--config", str(config), "--no-mlflow"]) == 0


# --- fairness ----------------------------------------------------------------------------------


def test_fpr_gap_by_hand() -> None:
    y = np.array([0, 0, 0, 0, 0, 0, 1, 1])
    flagged = np.array([True, False, False, False, False, False, True, False])
    groups = pd.DataFrame({"city_tier": [1, 1, 2, 2, 3, 3, 1, 2], "age_band": ["a"] * 8})
    gap = fpr_gap(y, flagged, groups)
    # Legit rows: tier 1 FPR 1/2, tiers 2 and 3 FPR 0 -> 50 points; one age band -> 0.
    assert gap == {"city_tier": pytest.approx(50.0), "age_band": 0.0}


# --- tuning, saving, SHAP, model card ----------------------------------------------------------


@pytest.fixture(scope="module")
def tuned(frame: pd.DataFrame) -> tuple[Prepared, optuna.Study, TrainResult]:
    groups = pd.DataFrame(
        {"city_tier": np.arange(len(frame)) % 3 + 1, "age_band": "26-35"}, index=frame.index
    )
    prep = prepare(from_training_frame(frame, warmup_days=14, groups=groups), FAST_CFG)
    study = tune(prep, FAST_CFG, n_trials=3)
    return prep, study, fit(prep, FAST_CFG, tuned=study.best_params)


def test_tune_searches_the_space(tuned: tuple[Prepared, optuna.Study, TrainResult]) -> None:
    _, study, result = tuned
    assert len(study.trials) == 3
    best = study.best_params
    assert 0.01 <= best["eta"] <= 0.3 and 3 <= best["max_depth"] <= 10
    assert result.params["eta"] == best["eta"]  # the refit used the tuned values
    assert 0 < study.best_value <= 1
    assert set(result.fairness) == {"city_tier", "age_band"}


def test_saved_model_loads_and_predicts_the_same(
    tuned: tuple[Prepared, optuna.Study, TrainResult], tmp_path: Path
) -> None:
    prep, study, result = tuned
    path = tmp_path / "txn_model.json"
    meta_path = save_model(result, path, build_meta(result, study, "test"))
    loaded = xgb.Booster(model_file=str(path))
    sample = xgb.DMatrix(prep.shap_sample, enable_categorical=True)
    expected = result.booster.predict(
        sample, iteration_range=(0, result.params["best_iteration"] + 1)
    )
    np.testing.assert_allclose(loaded.predict(sample), expected, rtol=1e-6)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["threshold"] == result.threshold
    assert [f["name"] for f in meta["features"]["features"]] == FEATURE_NAMES


def test_shap_values_add_up_to_the_score(tuned: tuple[Prepared, optuna.Study, TrainResult]) -> None:
    prep, _, result = tuned
    sample = prep.shap_sample.head(200)
    contribs = shap_values(result, sample)
    assert contribs.shape == (200, len(FEATURE_NAMES) + 1)  # + bias column
    margin = result.booster.predict(
        xgb.DMatrix(sample, enable_categorical=True),
        output_margin=True,
        iteration_range=(0, result.params["best_iteration"] + 1),
    )
    np.testing.assert_allclose(contribs.sum(axis=1), margin, rtol=1e-4, atol=1e-4)


def test_shap_plot_and_model_card(
    tuned: tuple[Prepared, optuna.Study, TrainResult], tmp_path: Path
) -> None:
    prep, study, result = tuned
    png = tmp_path / "shap.png"
    importance = shap_summary(result, prep.shap_sample.head(300), png)
    assert png.stat().st_size > 10_000 and importance.index[0] in FEATURE_NAMES
    card = txn_card(build_meta(result, study, "test"), importance, None, "test")
    for section in (
        "## Purpose",
        "## Training data",
        "## Features",
        "## Metrics",
        "## Thresholds",
        "## Limitations",
        "## Fairness results",
        "## Version",
        "## Date",
    ):
        assert section in card, section
    assert f"{result.metrics['test']['pr_auc']:.4f}" in card
