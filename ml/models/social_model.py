"""Social-engineering model (spec 03 §1): is the customer being talked into this payment?

Targets the scams where the real user pays on their own phone (digital arrest, fake support
refund, investment task), which the anomaly model cannot see. LightGBM on the context, payee,
coach and amount-ratio features; output `social_eng_score`.

    python -m ml.models.social_model
    python -m ml.models.social_model --no-mlflow

`coach_red_flags` is 0 when a payment is first scored; the Scam Coach (spec 01 F1) only asks its
2-3 questions after a STEP_UP. So training simulates coach answers for a random half of the
STEP_UP-range rows, and the model learns what a red-flag answer means.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import yaml

from ml.features.schema import FEATURES
from ml.pipelines.datasets import Dataset
from ml.pipelines.metrics import classification_metrics, scenario_recall, threshold_for_fpr
from ml.pipelines.split import time_split
from ml.pipelines.train import DEFAULT_CONFIG, ROOT, data_hash, load_dataset, mlflow_client

ARTIFACT = ROOT / "ml" / "artifacts" / "social_model.txt"
SOCIAL_FEATURES = [f.name for f in FEATURES if f.group in ("context", "payee", "coach")] + [
    "amount_ratio_avg"
]


def social_target(scenario: pd.Series, scam_scenarios: list[str]) -> np.ndarray:
    """1 for social-engineering scams, 0 for everything else (legit and other fraud types)."""
    return scenario.isin(scam_scenarios).to_numpy().astype("int8")


def simulate_coach(
    asked: np.ndarray,
    scenario: pd.Series,
    cfg: dict[str, Any],
    rng: np.random.Generator,
) -> np.ndarray:
    """coach_red_flags for each row: 0 where no coach ran, else the count of red-flag YES answers
    to 2-3 questions. Victims often admit a red flag; legit users rarely do; fraudsters on a
    stolen account mostly answer no."""
    scams = scenario.isin(cfg["scenarios"]).to_numpy()
    fraud = scenario.notna().to_numpy()
    p_yes = np.where(
        scams,
        cfg["coach_yes"]["scam"],
        np.where(fraud, cfg["coach_yes"]["other_fraud"], cfg["coach_yes"]["legit"]),
    )
    lo, hi = cfg["coach_questions"]
    questions = rng.integers(lo, hi + 1, size=len(asked))
    flags = rng.binomial(questions, p_yes)
    return np.where(asked, flags, 0).astype("int64")


def _frame(X: pd.DataFrame) -> pd.DataFrame:
    """LightGBM input: booleans as 0/1 numbers, columns in SOCIAL_FEATURES order."""
    return X[SOCIAL_FEATURES].astype("float64")


@dataclass
class SocialResult:
    booster: lgb.Booster
    threshold: float  # HOLD-style 1% FPR on validation, before coach
    step_up_threshold: float  # first-pass score marking the STEP_UP range
    metrics: dict[str, dict[str, dict[str, float]]]  # [before|after][val|test]
    recall: dict[str, dict[str, float]]  # [before|after] -> scenario -> test recall
    coach: dict[str, float]  # how many rows got simulated coach answers
    sizes: dict[str, int]
    data_hash: str
    params: dict[str, Any]


def _train(
    params: dict[str, Any],
    lcfg: dict[str, Any],
    x_tr: pd.DataFrame,
    y_tr: np.ndarray,
    x_va: pd.DataFrame,
    y_va: np.ndarray,
) -> lgb.Booster:
    train_set = lgb.Dataset(x_tr, label=y_tr, free_raw_data=False)
    val_set = lgb.Dataset(x_va, label=y_va, reference=train_set, free_raw_data=False)
    return lgb.train(
        params,
        train_set,
        num_boost_round=lcfg["num_boost_round"],
        valid_sets=[val_set],
        valid_names=["val"],
        callbacks=[lgb.early_stopping(lcfg["early_stopping_rounds"], verbose=False)],
    )


def train_social(ds: Dataset, cfg: dict[str, Any]) -> SocialResult:
    if ds.scenario is None:
        raise ValueError("the social model needs the simulated dataset (scenario labels)")
    scfg, seed, max_fpr = cfg["social"], cfg["seed"], cfg["target_fpr"]
    rng = np.random.default_rng(seed)
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    x = {
        k: _frame(ds.X.iloc[p]).reset_index(drop=True)
        for k, p in zip(("train", "val", "test"), split, strict=True)
    }
    sc = {
        k: ds.scenario.iloc[p].reset_index(drop=True)
        for k, p in zip(("train", "val", "test"), split, strict=True)
    }
    y = {k: social_target(sc[k], scfg["scenarios"]) for k in sc}
    for part in x.values():  # nobody has been coached when a payment is first scored
        part["coach_red_flags"] = 0.0

    positives = int(y["train"].sum())
    params = {
        **scfg["lightgbm"]["params"],
        "objective": "binary",
        "metric": "average_precision",
        "scale_pos_weight": (len(y["train"]) - positives) / positives,
        "monotone_constraints": [
            int(f in scfg.get("monotone_increasing", [])) for f in SOCIAL_FEATURES
        ],
        "seed": seed,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 0,
        "verbose": -1,
    }

    # 1) First pass without coach answers: which rows would be stepped up?
    first = _train(params, scfg["lightgbm"], x["train"], y["train"], x["val"], y["val"])
    step_thr = threshold_for_fpr(y["val"], first.predict(x["val"]), scfg["step_up_fpr"])
    in_range = {k: first.predict(x[k]) > step_thr for k in x}

    # 2) Simulated coach answers for a random half of the STEP_UP-range training rows (and the
    #    same share on validation, which early stopping uses), then the final model.
    coached = {k: x[k].copy() for k in ("train", "val")}
    asked = {}
    for k in coached:
        asked[k] = in_range[k] & (rng.random(len(in_range[k])) < scfg["coach_share"])
        coached[k]["coach_red_flags"] = simulate_coach(asked[k], sc[k], scfg, rng)
    booster = _train(
        params, scfg["lightgbm"], coached["train"], y["train"], coached["val"], y["val"]
    )

    # 3) Evaluate: before coach (first score) and after coach (every STEP_UP-range row coached).
    after = {}
    for k in ("val", "test"):
        after[k] = x[k].copy()
        after[k]["coach_red_flags"] = simulate_coach(in_range[k], sc[k], scfg, rng)
    metrics: dict[str, dict[str, dict[str, float]]] = {}
    recall: dict[str, dict[str, float]] = {}
    threshold = 0.0
    for stage, frames in (("before", x), ("after", after)):
        s_val, s_test = booster.predict(frames["val"]), booster.predict(frames["test"])
        thr = threshold_for_fpr(y["val"], s_val, max_fpr)
        threshold = thr if stage == "before" else threshold
        metrics[stage] = {
            "val": classification_metrics(y["val"], s_val, thr, max_fpr),
            "test": classification_metrics(y["test"], s_test, thr, max_fpr),
        }
        recall[stage] = scenario_recall(sc["test"], s_test > thr, scfg["scenarios"])
    return SocialResult(
        booster=booster,
        threshold=threshold,
        step_up_threshold=step_thr,
        metrics=metrics,
        recall=recall,
        coach={
            "train_step_up_rows": int(in_range["train"].sum()),
            "train_coached_rows": int(asked["train"].sum()),
            "train_rows_with_red_flag": int((coached["train"]["coach_red_flags"] > 0).sum()),
        },
        sizes={k: len(v) for k, v in y.items()},
        data_hash=data_hash(x["train"], y["train"]),
        params={**params, "best_iteration": booster.best_iteration},
    )


# --- saving, reporting -------------------------------------------------------------------------


def save(result: SocialResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    result.booster.save_model(str(path), num_iteration=result.booster.best_iteration)
    meta = {
        "model": "social_model",
        "features": SOCIAL_FEATURES,
        "threshold": result.threshold,
        "step_up_threshold": result.step_up_threshold,
        "best_iteration": result.booster.best_iteration,
        "metrics": result.metrics,
        "scenario_recall": result.recall,
        "coach": result.coach,
        "data_hash": result.data_hash,
    }
    meta_path = path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta_path


def load(path: Path = ARTIFACT) -> lgb.Booster:
    return lgb.Booster(model_file=str(path))


def _pct(x: float) -> str:
    return "n/a" if np.isnan(x) else f"{x:.1%}"


def format_table(result: SocialResult, scam_scenarios: list[str]) -> str:
    nan = float("nan")
    s = result.sizes
    lines = [
        f"social_model | {len(SOCIAL_FEATURES)} features | train {s['train']:,} / val {s['val']:,} "
        f"/ test {s['test']:,} | best iteration {result.booster.best_iteration}",
        f"STEP_UP range in train: {result.coach['train_step_up_rows']:,} rows; "
        f"{result.coach['train_coached_rows']:,} got simulated coach answers "
        f"({result.coach['train_rows_with_red_flag']:,} with a red flag)",
        "",
        f"{'test metric':<26}{'before coach':>14}{'after coach':>14}",
    ]
    for label, key in (
        ("PR-AUC", "pr_auc"),
        ("ROC-AUC", "roc_auc"),
        ("Recall @ 1% FPR", "recall_at_1pct_fpr"),
        ("FPR @ threshold", "fpr"),
    ):
        b, a = result.metrics["before"]["test"][key], result.metrics["after"]["test"][key]
        lines.append(f"{label:<26}{b:>14.4f}{a:>14.4f}")
    lines += ["", f"{'test recall @ 1% FPR':<26}{'before coach':>14}{'after coach':>14}"]
    for name in sorted(set(result.recall["before"]) | set(result.recall["after"])):
        mark = " (target >= 80%)" if name in scam_scenarios else ""
        b, a = result.recall["before"].get(name, nan), result.recall["after"].get(name, nan)
        lines.append(f"{name:<26}{_pct(b):>14}{_pct(a):>14}{mark}")
    return "\n".join(lines)


def log_to_mlflow(result: SocialResult, cfg: dict[str, Any], table: str, files: list[Path]) -> str:
    mlflow = mlflow_client(cfg["social"]["mlflow_experiment"])
    with mlflow.start_run(run_name="social-lightgbm") as run:
        mlflow.set_tags({"model": "social_model", "data_hash": result.data_hash})
        mlflow.log_params(
            {
                **{k: v for k, v in result.params.items() if k not in ("num_threads", "verbose")},
                "features": ",".join(SOCIAL_FEATURES),
                "coach_share": cfg["social"]["coach_share"],
                "coach_yes": json.dumps(cfg["social"]["coach_yes"]),
                "threshold": round(result.threshold, 6),
                **{f"n_{k}": v for k, v in result.sizes.items()},
            }
        )
        for stage, parts in result.metrics.items():
            for part, m in parts.items():
                mlflow.log_metrics({f"{stage}_{part}_{k}": v for k, v in m.items()})
        for stage, rec in result.recall.items():
            mlflow.log_metrics({f"{stage}_test_recall_{k}": v for k, v in rec.items()})
        mlflow.log_dict({"features": SOCIAL_FEATURES}, "features.json")
        mlflow.log_text(table, "metrics.txt")
        for f in files:
            mlflow.log_artifact(str(f))
        return str(run.info.run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the social-engineering model (LightGBM).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=ARTIFACT)
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    result = train_social(load_dataset("simulated", cfg), cfg)
    table = format_table(result, cfg["social"]["scenarios"])
    print(table)
    meta_path = save(result, args.out)
    print(f"\nsaved {args.out} and {meta_path.name}")
    if not args.no_mlflow:
        run_id = log_to_mlflow(result, cfg, table, [args.out, meta_path])
        print(f"MLflow run {run_id} in experiment '{cfg['social']['mlflow_experiment']}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
