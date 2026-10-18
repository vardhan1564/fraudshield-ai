"""Train the transaction model baseline (spec 03 sections 1, 5, 6) and log it to MLflow.

python -m ml.pipelines.train --dataset simulated
python -m ml.pipelines.train --dataset ieee
"""

import argparse
import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
import yaml

from ml.features.schema import to_json
from ml.pipelines.datasets import Dataset, load_ieee, load_simulated
from ml.pipelines.metrics import classification_metrics, scenario_recall, threshold_for_fpr
from ml.pipelines.split import time_split

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")


@dataclass
class TrainResult:
    booster: xgb.Booster
    params: dict[str, Any]
    threshold: float
    metrics: dict[str, dict[str, float]]  # {"val": {...}, "test": {...}}
    scenario_recall: dict[str, float] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    data_hash: str = ""
    seconds: float = 0.0


def _rows(frame: pd.DataFrame, positions: np.ndarray) -> pd.DataFrame:
    """Rows by position; a contiguous block is sliced (no copy), which matters for IEEE."""
    if len(positions) and (np.diff(positions) == 1).all():
        return frame.iloc[positions[0] : positions[-1] + 1]
    return frame.iloc[positions]


def data_hash(X: pd.DataFrame, y: np.ndarray) -> str:
    """Short fingerprint of the training data (spec 03 §1), so a run can be traced to its data."""
    digest = hashlib.sha256(pd.util.hash_pandas_object(X, index=False).to_numpy().tobytes())
    digest.update(np.ascontiguousarray(y).tobytes())
    return digest.hexdigest()[:16]


def train_model(ds: Dataset, cfg: dict[str, Any]) -> TrainResult:
    started = time.perf_counter()
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    x_train, x_val, x_test = (_rows(ds.X, p) for p in split)
    y_train, y_val, y_test = (ds.y[p] for p in split)

    positives = int(y_train.sum())
    if positives == 0 or not y_val.any():
        raise ValueError("train and validation both need fraud rows; use more data")
    xcfg = cfg["xgboost"]
    params = {
        **xcfg["params"],
        "objective": "binary:logistic",
        "scale_pos_weight": (len(y_train) - positives) / positives,  # legit / fraud in train
        "seed": cfg["seed"],
        "nthread": -1,
    }
    # Quantile matrices store binned values: much smaller than a copy of the data.
    bins = params.setdefault("max_bin", 256)  # must match across both matrices and the booster
    dtrain = xgb.QuantileDMatrix(x_train, label=y_train, enable_categorical=True, max_bin=bins)
    dval = xgb.QuantileDMatrix(
        x_val, label=y_val, enable_categorical=True, max_bin=bins, ref=dtrain
    )
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=xcfg["num_boost_round"],
        evals=[(dval, "val")],
        early_stopping_rounds=xcfg["early_stopping_rounds"],
        verbose_eval=False,
    )
    del dtrain, dval

    best = (0, booster.best_iteration + 1)
    s_val = booster.inplace_predict(x_val, iteration_range=best)
    s_test = booster.inplace_predict(x_test, iteration_range=best)
    max_fpr = cfg["target_fpr"]
    threshold = threshold_for_fpr(y_val, s_val, max_fpr)  # chosen on val, used as-is on test
    result = TrainResult(
        booster=booster,
        params={**params, "best_iteration": booster.best_iteration},
        threshold=threshold,
        metrics={
            "val": classification_metrics(y_val, s_val, threshold, max_fpr),
            "test": classification_metrics(y_test, s_test, threshold, max_fpr),
        },
        sizes={"train": len(y_train), "val": len(y_val), "test": len(y_test)},
        data_hash=data_hash(x_train, y_train),
    )
    if ds.scenario is not None:
        test_scenarios = ds.scenario.iloc[split.test]
        result.scenario_recall = scenario_recall(test_scenarios, s_test > threshold)
    result.seconds = time.perf_counter() - started
    return result


def format_table(ds: Dataset, result: TrainResult) -> str:
    rows = [
        ("PR-AUC", "pr_auc"),
        ("ROC-AUC", "roc_auc"),
        ("Recall @ 1% FPR", "recall_at_1pct_fpr"),
        ("F1 @ threshold", "f1"),
        ("Recall @ threshold", "recall"),
        ("FPR @ threshold", "fpr"),
        ("Fraud rate", "fraud_rate"),
    ]
    s = result.sizes
    lines = [
        f"txn_model baseline | dataset {ds.name} | {len(ds.features)} features | "
        f"train {s['train']:,} / val {s['val']:,} / test {s['test']:,} rows",
        f"best iteration {result.params['best_iteration']} | "
        f"scale_pos_weight {result.params['scale_pos_weight']:.1f} | "
        f"threshold {result.threshold:.4f} | {result.seconds:.0f} s",
        "",
        f"{'metric':<22}{'val':>9}{'test':>9}",
    ]
    for label, key in rows:
        lines.append(
            f"{label:<22}{result.metrics['val'][key]:>9.4f}{result.metrics['test'][key]:>9.4f}"
        )
    if result.scenario_recall:
        lines += ["", f"{'test recall by scenario':<26}{'recall':>8}"]
        for name, value in sorted(result.scenario_recall.items()):
            lines.append(f"{name:<26}{value:>8.1%}")
    return "\n".join(lines)


def log_to_mlflow(ds: Dataset, result: TrainResult, cfg: dict[str, Any], table: str) -> str:
    """Log params, metrics, feature list, data hash and the model; returns the run id."""
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow  # heavy and not installed in CI, so only imported when logging
    import mlflow.xgboost
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    # On Windows "localhost" tries IPv6 first and adds ~2 s to every one of MLflow's calls.
    mlflow.set_tracking_uri(uri.replace("//localhost", "//127.0.0.1"))
    mlflow.set_experiment(cfg["mlflow"]["experiment"])
    with mlflow.start_run(run_name=f"baseline-{ds.name}") as run:
        mlflow.set_tags(
            {
                "dataset": ds.name,
                "model": "txn_model",
                "stage": "baseline",
                "data_hash": result.data_hash,
            }
        )
        mlflow.log_params(
            {
                **{k: v for k, v in result.params.items() if k != "nthread"},
                "threshold": round(result.threshold, 6),
                "n_features": len(ds.features),
                **{f"n_{k}": v for k, v in result.sizes.items()},
            }
        )
        metrics = {f"{part}_{k}": v for part, m in result.metrics.items() for k, v in m.items()}
        metrics |= {f"test_recall_{k}": v for k, v in result.scenario_recall.items()}
        mlflow.log_metrics(metrics)
        features = to_json() if ds.name == "simulated" else {"features": ds.features}
        mlflow.log_dict(features, "features.json")
        mlflow.log_text(table, "metrics.txt")
        mlflow.xgboost.log_model(result.booster, name="model")
        return str(run.info.run_id)


def load_dataset(name: str, cfg: dict[str, Any]) -> Dataset:
    d = cfg["datasets"][name]
    if name == "simulated":
        return load_simulated(ROOT / d["features"], ROOT / d["raw_dir"], d["warmup_days"])
    if name == "ieee":
        return load_ieee(ROOT / d["raw_dir"], d.get("max_rows"))
    raise ValueError(f"unknown dataset {name!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the txn_model XGBoost baseline.")
    parser.add_argument("--dataset", choices=["simulated", "ieee"], help="overrides config.yaml")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-mlflow", action="store_true", help="train and print only")
    args = parser.parse_args(argv)
    # MLflow prints emoji; the Windows console (cp1252) would crash on them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    name = args.dataset or cfg["dataset"]
    ds = load_dataset(name, cfg)
    result = train_model(ds, cfg)
    table = format_table(ds, result)
    print(table)
    if not args.no_mlflow:
        run_id = log_to_mlflow(ds, result, cfg, table)
        print(f"\nMLflow run {run_id} in experiment '{cfg['mlflow']['experiment']}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
