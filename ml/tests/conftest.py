"""Shared fixtures: a small simulated dataset with every base model trained on it."""

import json
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
    (folder / "txn_model.meta.json").write_text(
        json.dumps({"data_hash": txn.data_hash, "version": "test", "threshold": txn.threshold}),
        encoding="utf-8",
    )
    anomaly = anomaly_model.train_anomaly(ds, CFG)
    anomaly_model.save(anomaly.winner.model, folder / "anomaly_model.pt", {})
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
