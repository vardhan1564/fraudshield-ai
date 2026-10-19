"""Shared fixtures: a small simulated dataset with every base model trained on it."""

import json
import runpy
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from ml.features.build import training_frame
from ml.models import anomaly_model, social_model
from ml.pipelines.datasets import Dataset, from_training_frame
from ml.pipelines.train import DEFAULT_CONFIG, train_model
from ml.simulator.config import load_config
from ml.simulator.run import simulate

CFG = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
CFG["xgboost"]["num_boost_round"] = 150  # keep the tests quick
CFG["anomaly"]["autoencoder"]["epochs"] = 10
CFG["social"]["lightgbm"]["num_boost_round"] = 150


@pytest.fixture(scope="session")
def mini_artifacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Dataset]:
    """Raw simulator tables + txn, anomaly and social models in one folder, like ml/artifacts."""
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
    meta = {  # the same keys ml.pipelines.tune.build_meta writes for the real model
        "model": "txn_model",
        "dataset": "simulated",
        "version": "test",
        "trained_on": "2026-01-01",
        "threshold": txn.threshold,
        "best_iteration": txn.params["best_iteration"],
        "params": {k: v for k, v in txn.params.items() if k != "best_iteration"},
        "optuna": {"trials": 0, "best_val_pr_auc": txn.metrics["val"]["pr_auc"]},
        "metrics": txn.metrics,
        "scenario_recall": txn.scenario_recall,
        "fairness_fpr_gap_points": txn.fairness,
        "data_hash": txn.data_hash,
        "sizes": txn.sizes,
    }
    (folder / "txn_model.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    anomaly = anomaly_model.train_anomaly(ds, CFG)
    anomaly_model.save(
        anomaly.winner.model, folder / "anomaly_model.pt", anomaly_model.saved_meta(anomaly)
    )
    social = social_model.train_social(ds, CFG)
    social_model.save(social, folder / "social_model.txt")
    return folder, ds


def config_for(folder: Path) -> Path:
    """A config.yaml whose simulated dataset is the fixture's raw tables in `folder`."""
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
    path = folder / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def served_folder(
    mini_artifacts: tuple[Path, Dataset], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """A private copy of the mini artifacts plus the meta-model and thresholds.json, built with
    `python -m` semantics (run as __main__) like the real command, so the pickle loads."""
    folder = tmp_path_factory.mktemp("served")
    shutil.copytree(mini_artifacts[0], folder, dirs_exist_ok=True)
    argv = ["meta_model", "--config", str(config_for(folder)), "--artifacts", str(folder)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [*argv, "--no-mlflow"])
        with pytest.raises(SystemExit) as done:
            runpy.run_module("ml.models.meta_model", run_name="__main__", alter_sys=True)
    assert done.value.code == 0
    return folder
