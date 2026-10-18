"""Train the transaction model baseline (spec 03 sections 1, 5, 6) and log it to MLflow.

python -m ml.pipelines.train --dataset simulated
python -m ml.pipelines.train --dataset ieee
"""

import argparse
import gc
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
from ml.pipelines.metrics import (
    classification_metrics,
    fpr_gap,
    scenario_recall,
    threshold_for_fpr,
)
from ml.pipelines.split import time_split

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
SHAP_ROWS = 2_000


@dataclass
class Prepared:
    """Train / val / test as XGBoost binned matrices; the source DataFrame can then be freed."""

    name: str
    features: list[str]
    dtrain: xgb.QuantileDMatrix
    dval: xgb.QuantileDMatrix
    dtest: xgb.QuantileDMatrix
    y_train: np.ndarray
    y_val: np.ndarray
    y_test: np.ndarray
    test_scenario: pd.Series | None
    data_hash: str
    test_groups: pd.DataFrame | None = None  # city_tier, age_band of the test rows
    shap_sample: pd.DataFrame | None = None  # up to SHAP_ROWS test rows, for explanations


@dataclass
class TrainResult:
    name: str
    features: list[str]
    booster: xgb.Booster
    params: dict[str, Any]
    threshold: float
    metrics: dict[str, dict[str, float]]  # {"val": {...}, "test": {...}}
    scenario_recall: dict[str, float] = field(default_factory=dict)
    sizes: dict[str, int] = field(default_factory=dict)
    data_hash: str = ""
    seconds: float = 0.0
    fairness: dict[str, float] = field(default_factory=dict)  # FPR gap in points per group


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


def prepare(ds: Dataset, cfg: dict[str, Any]) -> Prepared:
    """Time split, then bin each part into a QuantileDMatrix (~4x smaller than float32 data)."""
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    x_train, x_val, x_test = (_rows(ds.X, p) for p in split)
    y_train, y_val, y_test = (ds.y[p] for p in split)
    if not y_train.any() or not y_val.any():
        raise ValueError("train and validation both need fraud rows; use more data")
    bins = cfg["xgboost"]["params"].get("max_bin", 256)  # same for all matrices and the booster
    dtrain = xgb.QuantileDMatrix(x_train, label=y_train, enable_categorical=True, max_bin=bins)
    return Prepared(
        name=ds.name,
        features=list(ds.features),
        dtrain=dtrain,
        dval=xgb.QuantileDMatrix(
            x_val, label=y_val, enable_categorical=True, max_bin=bins, ref=dtrain
        ),
        dtest=xgb.QuantileDMatrix(
            x_test, label=y_test, enable_categorical=True, max_bin=bins, ref=dtrain
        ),
        y_train=y_train,
        y_val=y_val,
        y_test=y_test,
        test_scenario=None if ds.scenario is None else ds.scenario.iloc[split.test],
        data_hash=data_hash(x_train, y_train),
        test_groups=None if ds.groups is None else ds.groups.iloc[split.test],
        shap_sample=x_test.sample(min(SHAP_ROWS, len(x_test)), random_state=cfg["seed"]).copy(),
    )


def fit(prep: Prepared, cfg: dict[str, Any], tuned: dict[str, Any] | None = None) -> TrainResult:
    """Train with config params, overridden by `tuned` (e.g. Optuna's best), and evaluate."""
    started = time.perf_counter()
    xcfg = cfg["xgboost"]
    positives = int(prep.y_train.sum())
    params = {
        **xcfg["params"],
        **(tuned or {}),
        "objective": "binary:logistic",
        "scale_pos_weight": (len(prep.y_train) - positives) / positives,  # legit / fraud in train
        "seed": cfg["seed"],
        "nthread": -1,
    }
    params.setdefault("max_bin", 256)
    booster = xgb.train(
        params,
        prep.dtrain,
        num_boost_round=xcfg["num_boost_round"],
        evals=[(prep.dval, "val")],
        early_stopping_rounds=xcfg["early_stopping_rounds"],
        verbose_eval=False,
    )
    best = (0, booster.best_iteration + 1)
    s_val = booster.predict(prep.dval, iteration_range=best)
    s_test = booster.predict(prep.dtest, iteration_range=best)
    max_fpr = cfg["target_fpr"]
    threshold = threshold_for_fpr(prep.y_val, s_val, max_fpr)  # chosen on val, used on test
    result = TrainResult(
        name=prep.name,
        features=prep.features,
        booster=booster,
        params={**params, "best_iteration": booster.best_iteration},
        threshold=threshold,
        metrics={
            "val": classification_metrics(prep.y_val, s_val, threshold, max_fpr),
            "test": classification_metrics(prep.y_test, s_test, threshold, max_fpr),
        },
        sizes={"train": len(prep.y_train), "val": len(prep.y_val), "test": len(prep.y_test)},
        data_hash=prep.data_hash,
    )
    if prep.test_scenario is not None:
        result.scenario_recall = scenario_recall(prep.test_scenario, s_test > threshold)
    if prep.test_groups is not None:
        result.fairness = fpr_gap(prep.y_test, s_test > threshold, prep.test_groups)
    result.seconds = time.perf_counter() - started
    return result


def train_model(ds: Dataset, cfg: dict[str, Any]) -> TrainResult:
    return fit(prepare(ds, cfg), cfg)


def format_table(result: TrainResult) -> str:
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
        f"txn_model baseline | dataset {result.name} | {len(result.features)} features | "
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
    if result.fairness:
        lines += ["", "test FPR gap between groups (target < 2 points)"]
        for col, gap in result.fairness.items():
            lines.append(f"{col:<26}{gap:>6.2f} pt")
    return "\n".join(lines)


def mlflow_client(experiment: str) -> Any:
    """The mlflow module, pointed at MLFLOW_TRACKING_URI from .env and the given experiment.

    Imported lazily: MLflow is heavy and not installed in CI.
    """
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    uri = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    # On Windows "localhost" tries IPv6 first and adds ~2 s to every one of MLflow's calls.
    mlflow.set_tracking_uri(uri.replace("//localhost", "//127.0.0.1"))
    mlflow.set_experiment(experiment)
    return mlflow


def log_to_mlflow(
    result: TrainResult,
    cfg: dict[str, Any],
    table: str,
    *,
    stage: str = "baseline",
    extra_params: dict[str, Any] | None = None,
    artifacts: list[Path] | None = None,
    register: bool = False,
) -> tuple[str, str | None]:
    """Log params, metrics, feature list, data hash and the model.

    Returns (run id, registered model version or None).
    """
    mlflow = mlflow_client(cfg["mlflow"]["experiment"])
    import mlflow.xgboost as mlflow_xgboost

    with mlflow.start_run(run_name=f"{stage}-{result.name}") as run:
        mlflow.set_tags(
            {
                "dataset": result.name,
                "model": "txn_model",
                "stage": stage,
                "data_hash": result.data_hash,
            }
        )
        mlflow.log_params(
            {
                **{k: v for k, v in result.params.items() if k != "nthread"},
                "threshold": round(result.threshold, 6),
                "n_features": len(result.features),
                **{f"n_{k}": v for k, v in result.sizes.items()},
                **(extra_params or {}),
            }
        )
        metrics = {f"{part}_{k}": v for part, m in result.metrics.items() for k, v in m.items()}
        metrics |= {f"test_recall_{k}": v for k, v in result.scenario_recall.items()}
        metrics |= {f"test_fpr_gap_{k}": v for k, v in result.fairness.items()}
        mlflow.log_metrics(metrics)
        features = to_json() if result.name == "simulated" else {"features": result.features}
        mlflow.log_dict(features, "features.json")
        mlflow.log_text(table, "metrics.txt")
        for path in artifacts or []:
            mlflow.log_artifact(str(path))
        info = mlflow_xgboost.log_model(
            result.booster,
            name="model",
            registered_model_name="txn_model" if register else None,
        )
        version = getattr(info, "registered_model_version", None)
        return str(run.info.run_id), None if version is None else str(version)


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
    # The DataFrame is only referenced inside prepare(), so it is freed before training starts.
    prepared = prepare(load_dataset(name, cfg), cfg)
    gc.collect()
    result = fit(prepared, cfg)
    del prepared
    table = format_table(result)
    print(table)
    if not args.no_mlflow:
        run_id, _ = log_to_mlflow(result, cfg, table)
        print(f"\nMLflow run {run_id} in experiment '{cfg['mlflow']['experiment']}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
