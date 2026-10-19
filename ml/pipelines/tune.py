"""Tune the transaction model with Optuna, save the best model, explain it and write its card.

    python -m ml.pipelines.tune --dataset simulated --trials 30
    python -m ml.pipelines.tune --dataset ieee --trials 30 --timeout 7200   # benchmark, slow

Spec 03: Optuna 30 trials, objective = PR-AUC on validation (§5); SHAP summary plot in MLflow
(§7); model card from the §9 template. The simulated model is the one the API serves.
"""

import argparse
import gc
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import optuna
import pandas as pd
import xgboost as xgb
import yaml
from sklearn.metrics import average_precision_score

from ml.features.schema import to_json
from ml.pipelines.evaluate import ieee_from_mlflow
from ml.pipelines.model_cards import txn_card
from ml.pipelines.train import (
    DEFAULT_CONFIG,
    ROOT,
    Prepared,
    TrainResult,
    fit,
    format_table,
    load_dataset,
    log_to_mlflow,
    prepare,
)

ARTIFACTS = {  # the simulated model is the served txn_model; IEEE is a benchmark
    "simulated": ROOT / "ml" / "artifacts" / "txn_model.json",
    "ieee": ROOT / "ml" / "artifacts" / "txn_model_ieee.json",
}
MODEL_CARD = ROOT / "ml" / "models" / "txn_model" / "MODEL_CARD.md"


# --- search ------------------------------------------------------------------------------------


def search_space(trial: optuna.Trial) -> dict[str, Any]:
    return {
        "eta": trial.suggest_float("eta", 0.01, 0.3, log=True),
        "max_depth": trial.suggest_int("max_depth", 3, 10),
        "min_child_weight": trial.suggest_float("min_child_weight", 1.0, 20.0, log=True),
        "subsample": trial.suggest_float("subsample", 0.5, 1.0),
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.3, 1.0),
        "gamma": trial.suggest_float("gamma", 1e-3, 5.0, log=True),
        "lambda": trial.suggest_float("lambda", 1e-3, 10.0, log=True),
        "alpha": trial.suggest_float("alpha", 1e-3, 10.0, log=True),
    }


class _Pruning(xgb.callback.TrainingCallback):
    """Report validation PR-AUC to Optuna each round; stop trials that trail the median."""

    def __init__(self, trial: optuna.Trial) -> None:
        self.trial = trial

    def after_iteration(self, model: xgb.Booster, epoch: int, evals_log: dict[str, Any]) -> bool:
        self.trial.report(float(evals_log["val"]["aucpr"][-1]), epoch)
        if self.trial.should_prune():
            raise optuna.TrialPruned()
        return False


def tune(
    prep: Prepared, cfg: dict[str, Any], n_trials: int, timeout: float | None = None
) -> optuna.Study:
    """Search on train, score each trial by exact PR-AUC on validation (never on test)."""
    xcfg = cfg["xgboost"]
    positives = int(prep.y_train.sum())
    base = {
        **xcfg["params"],
        "objective": "binary:logistic",
        "eval_metric": "aucpr",
        "scale_pos_weight": (len(prep.y_train) - positives) / positives,
        "seed": cfg["seed"],
        "nthread": -1,
    }

    def objective(trial: optuna.Trial) -> float:
        booster = xgb.train(
            {**base, **search_space(trial)},
            prep.dtrain,
            num_boost_round=xcfg["num_boost_round"],
            evals=[(prep.dval, "val")],
            early_stopping_rounds=xcfg["early_stopping_rounds"],
            callbacks=[_Pruning(trial)],
            verbose_eval=False,
        )
        trial.set_user_attr("best_iteration", booster.best_iteration)
        scores = booster.predict(prep.dval, iteration_range=(0, booster.best_iteration + 1))
        return float(average_precision_score(prep.y_val, scores))

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name=f"txn_model-{prep.name}",
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=cfg["seed"]),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=30),
    )
    study.optimize(objective, n_trials=n_trials, timeout=timeout)
    return study


# --- explanation -------------------------------------------------------------------------------


def shap_values(result: TrainResult, sample: pd.DataFrame) -> np.ndarray:
    """Per-row, per-feature SHAP values (log-odds) from XGBoost's built-in TreeSHAP.

    Shape (rows, features + 1); the last column is the bias. Handles categorical columns.
    """
    matrix = xgb.DMatrix(sample, enable_categorical=True)
    best = (0, result.params["best_iteration"] + 1)
    return result.booster.predict(matrix, pred_contribs=True, iteration_range=best)


def shap_summary(result: TrainResult, sample: pd.DataFrame, path: Path) -> pd.Series:
    """Save the SHAP summary (beeswarm) plot; return mean |SHAP| per feature, largest first."""
    import matplotlib

    matplotlib.use("Agg")  # no window, just a file
    import matplotlib.pyplot as plt
    import shap

    values = shap_values(result, sample)[:, :-1]
    # Colour needs numbers: category columns are shown by their code.
    shown = sample.apply(lambda c: c.cat.codes if c.dtype == "category" else c).astype(float)
    plt.figure()
    shap.summary_plot(values, shown, feature_names=list(sample.columns), max_display=20, show=False)
    plt.title(f"txn_model ({result.name}): SHAP summary, {len(sample):,} test rows")
    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=120)
    plt.close("all")
    importance = pd.Series(np.abs(values).mean(axis=0), index=sample.columns)
    return importance.sort_values(ascending=False)


# --- outputs -----------------------------------------------------------------------------------


def save_model(result: TrainResult, path: Path, meta: dict[str, Any]) -> Path:
    """Save the booster cut at its best iteration, plus a meta file the API reads (threshold,
    features, metrics). Returns the meta path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    result.booster[: result.params["best_iteration"] + 1].save_model(path)
    meta_path = path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    return meta_path


def build_meta(result: TrainResult, study: optuna.Study, version: str) -> dict[str, Any]:
    return {
        "model": "txn_model",
        "dataset": result.name,
        "version": version,
        "trained_on": date.today().isoformat(),
        "threshold": result.threshold,  # HOLD threshold: 1% FPR on validation
        "best_iteration": result.params["best_iteration"],
        "params": {
            k: v for k, v in result.params.items() if k not in ("nthread", "best_iteration")
        },
        "optuna": {"trials": len(study.trials), "best_val_pr_auc": study.best_value},
        "metrics": result.metrics,
        "scenario_recall": result.scenario_recall,
        "fairness_fpr_gap_points": result.fairness,
        "data_hash": result.data_hash,
        "sizes": result.sizes,
        "features": to_json() if result.name == "simulated" else {"features": result.features},
    }


# --- command line ------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tune txn_model with Optuna and save the best.")
    parser.add_argument("--dataset", choices=["simulated", "ieee"], help="overrides config.yaml")
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--timeout", type=float, help="stop starting new trials after N seconds")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, help="model path (default by dataset)")
    parser.add_argument("--card", type=Path, default=MODEL_CARD)
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    name = args.dataset or cfg["dataset"]
    prepared = prepare(load_dataset(name, cfg), cfg)  # the DataFrame is freed after this
    gc.collect()

    study = tune(prepared, cfg, args.trials, args.timeout)
    result = fit(prepared, cfg, tuned=study.best_params)
    table = format_table(result).replace("baseline", "tuned", 1)
    print(table)

    model_path = args.out or ARTIFACTS[name]
    shap_png = model_path.parent / f"{model_path.stem}_shap_summary.png"
    importance = shap_summary(result, prepared.shap_sample, shap_png)
    trials_csv = model_path.parent / f"{model_path.stem}_optuna_trials.csv"
    study.trials_dataframe().to_csv(trials_csv, index=False)

    run_id, version = None, None
    if not args.no_mlflow:
        run_id, version = log_to_mlflow(
            result,
            cfg,
            table,
            stage="tuned",
            extra_params={
                "optuna_trials": len(study.trials),
                "optuna_best_val_pr_auc": study.best_value,
            },
            artifacts=[shap_png, trials_csv],
            register=name == "simulated",
        )
    version = version or f"local-{date.today():%Y%m%d}"
    meta = build_meta(result, study, version) | {"mlflow_run_id": run_id}
    meta_path = save_model(result, model_path, meta)
    print(f"\nsaved {model_path} and {meta_path.name}  |  model version {version}")

    if name == "simulated":
        ieee = None if args.no_mlflow else ieee_from_mlflow(cfg["mlflow"]["experiment"])
        label = f"MLflow registry `txn_model` v{version}" if run_id else f"txn_model {version}"
        args.card.parent.mkdir(parents=True, exist_ok=True)
        card = txn_card(meta, importance, ieee, label)
        args.card.write_text(card, encoding="utf-8", newline="\n")
        print(f"wrote {args.card} (all cards: python -m ml.pipelines.model_cards)")
    print("\ntop features by mean |SHAP|:")
    print(importance.head(10).round(4).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
