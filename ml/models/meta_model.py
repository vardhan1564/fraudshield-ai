"""Meta-model (spec 03 §1): combine the model scores into one calibrated risk_score and decide.

    python -m ml.models.meta_model
    python -m ml.models.meta_model --no-mlflow

Inputs per payment: txn_score (XGBoost), anomaly_score, social_eng_score (before any Scam Coach
answers) and graph_score (0 until phase 5). A logistic regression wrapped in isotonic calibration
is fitted on the validation split, where the base models' scores are out-of-sample. Thresholds
follow spec 03 §5: HOLD at FPR <= 1% and STEP_UP at FPR <= 5% on validation, chosen on
out-of-fold predictions so the model never grades its own homework. Final metrics on test.

One explicit rule sits on top (spec 01 F2): an unknown caller on the line while paying a new
payee adds a fixed boost to risk_score. The learned weights barely use the call signal (screen
share dominates the social model), and a rule is easy to explain in top_reasons.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
import yaml
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from ml.features.schema import FEATURE_NAMES
from ml.models import anomaly_model, social_model
from ml.pipelines.datasets import Dataset
from ml.pipelines.metrics import classification_metrics, fpr_gap, threshold_for_fpr
from ml.pipelines.split import Split, time_split
from ml.pipelines.train import DEFAULT_CONFIG, ROOT, data_hash, load_dataset, mlflow_client

ARTIFACTS = ROOT / "ml" / "artifacts"
SCORE_COLUMNS = ["txn_score", "anomaly_score", "social_eng_score", "graph_score"]
DECISIONS = ("APPROVE", "STEP_UP", "HOLD")
RULE_UNKNOWN_CALL = "unknown_call_new_payee"


# --- base model scores -------------------------------------------------------------------------


@dataclass
class BaseModels:
    txn: xgb.Booster
    anomaly: anomaly_model.AnomalyModel
    social: lgb.Booster
    txn_meta: dict[str, Any]

    @classmethod
    def load(cls, folder: Path = ARTIFACTS) -> "BaseModels":
        missing = [
            f
            for f in ("txn_model.json", "anomaly_model.pt", "social_model.txt")
            if not (folder / f).exists()
        ]
        if missing:
            raise FileNotFoundError(f"train these first, missing in {folder}: {missing}")
        return cls(
            txn=xgb.Booster(model_file=str(folder / "txn_model.json")),
            anomaly=anomaly_model.load(folder / "anomaly_model.pt"),
            social=social_model.load(folder / "social_model.txt"),
            txn_meta=json.loads((folder / "txn_model.meta.json").read_text(encoding="utf-8")),
        )

    def scores(self, X: pd.DataFrame) -> pd.DataFrame:
        """The four meta-model inputs for each row of a spec 03 §2 feature frame."""
        social_in = social_model._frame(X).assign(
            coach_red_flags=0.0
        )  # first decision: no coach yet
        return pd.DataFrame(
            {
                "txn_score": self.txn.predict(
                    xgb.DMatrix(X[FEATURE_NAMES], enable_categorical=True)
                ),
                "anomaly_score": self.anomaly.score(X),
                "social_eng_score": self.social.predict(social_in),
                "graph_score": X["graph_score"].astype("float64").to_numpy(),  # 0 until phase 5
            },
            index=X.index,
        )


# --- the meta-model ----------------------------------------------------------------------------


def _logit_probabilities(x: np.ndarray) -> np.ndarray:
    """Model probabilities live near 0 and 1; logits spread them out for a linear model."""
    out = np.asarray(x, dtype="float64").copy()
    eps = 1e-6
    for col in (0, 2):  # txn_score, social_eng_score are probabilities
        p = np.clip(out[:, col], eps, 1 - eps)
        out[:, col] = np.log(p / (1 - p))
    return out


def make_meta(seed: int) -> CalibratedClassifierCV:
    """Logistic regression on (logit) scores, calibrated with isotonic regression."""
    lr = make_pipeline(
        FunctionTransformer(_logit_probabilities),
        StandardScaler(),
        LogisticRegression(class_weight="balanced", max_iter=1000, random_state=seed),
    )
    return CalibratedClassifierCV(lr, method="isotonic", cv=5)


def rule_hits(X: pd.DataFrame) -> np.ndarray:
    """Spec 01 F2 rule: an unknown caller on the line while paying someone new."""
    return (X["ctx_unknown_call"].astype(bool) & X["payee_is_new"].astype(bool)).to_numpy()


def apply_rules(risk: np.ndarray, X: pd.DataFrame, boost: float) -> np.ndarray:
    """Add the fixed boost where the rule fires, capped at 1."""
    return np.minimum(1.0, risk + boost * rule_hits(X))


def risk_score(
    model: CalibratedClassifierCV, scores: pd.DataFrame, X: pd.DataFrame, boost: float
) -> np.ndarray:
    """Final risk_score: calibrated meta-model probability plus the explicit rule."""
    return apply_rules(model.predict_proba(scores[SCORE_COLUMNS].to_numpy())[:, 1], X, boost)


def choose_thresholds(y: np.ndarray, oof: np.ndarray, cfg: dict[str, float]) -> dict[str, float]:
    """Spec 03 §5 on out-of-fold validation scores: flag when risk > threshold."""
    hold = threshold_for_fpr(y, oof, cfg["hold_fpr"])
    step_up = min(threshold_for_fpr(y, oof, cfg["step_up_fpr"]), hold)  # STEP_UP never above HOLD
    return {"step_up": step_up, "hold": hold}


def decide(risk: np.ndarray, thresholds: dict[str, float]) -> np.ndarray:
    """APPROVE / STEP_UP / HOLD per row."""
    return np.where(
        risk > thresholds["hold"],
        "HOLD",
        np.where(risk > thresholds["step_up"], "STEP_UP", "APPROVE"),
    )


@dataclass
class MetaResult:
    model: CalibratedClassifierCV
    thresholds: dict[str, float]
    metrics: dict[str, Any]
    coefficients: dict[str, float]
    boost: float


def _report(
    y: np.ndarray,
    risk: np.ndarray,
    thresholds: dict[str, float],
    scenario: pd.Series,
    groups: pd.DataFrame | None,
) -> dict[str, Any]:
    decisions = decide(risk, thresholds)
    hold = risk > thresholds["hold"]
    out: dict[str, Any] = classification_metrics(y, risk, thresholds["hold"])
    out["brier"] = float(brier_score_loss(y, risk))
    out["fpr_step_up_or_hold"] = float((decisions[y == 0] != "APPROVE").mean())
    out["decisions"] = {
        label: {d: float((decisions[mask] == d).mean()) for d in DECISIONS}
        for label, mask in (("legit", y == 0), ("fraud", y == 1))
    }
    frame = pd.DataFrame({"scenario": scenario.to_numpy(), "d": decisions})
    fraud = frame[frame["scenario"].notna()]
    out["by_scenario"] = {
        str(name): {
            "hold": float((g["d"] == "HOLD").mean()),
            "step_up_or_hold": float((g["d"] != "APPROVE").mean()),
        }
        for name, g in fraud.groupby("scenario")
    }
    if groups is not None:
        out["fpr_gap_points"] = fpr_gap(y, hold, groups)
    return out


def split_for(ds: Dataset, cfg: dict[str, Any], base: BaseModels) -> Split:
    """The time split, after checking the saved models come from this exact train split
    (otherwise val/test scores would leak)."""
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    train_hash = data_hash(ds.X.iloc[split.train], ds.y[split.train])
    if train_hash != base.txn_meta.get("data_hash"):
        raise ValueError(
            "txn_model.json was trained on different data; re-run tune, anomaly and social first"
        )
    return split


def train_meta(ds: Dataset, base: BaseModels, cfg: dict[str, Any]) -> MetaResult:
    if ds.scenario is None:
        raise ValueError("the meta-model is trained on the simulated dataset")
    split = split_for(ds, cfg, base)
    x_val, x_test = ds.X.iloc[split.val], ds.X.iloc[split.test]
    y_val, y_test = ds.y[split.val], ds.y[split.test]
    s_val, s_test = base.scores(x_val), base.scores(x_test)

    mcfg, seed = cfg["meta"], cfg["seed"]
    boost = mcfg["unknown_call_boost"]
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    oof = cross_val_predict(
        make_meta(seed), s_val[SCORE_COLUMNS].to_numpy(), y_val, cv=folds, method="predict_proba"
    )[:, 1]
    oof = apply_rules(oof, x_val, boost)  # thresholds see the rule, so FPR targets still hold
    thresholds = choose_thresholds(y_val, oof, mcfg)
    model = make_meta(seed).fit(s_val[SCORE_COLUMNS].to_numpy(), y_val)

    risk_test = risk_score(model, s_test, x_test, boost)
    hits_test = rule_hits(x_test)
    groups_test = None if ds.groups is None else ds.groups.iloc[split.test]
    metrics = {
        "val_oof": _report(y_val, oof, thresholds, ds.scenario.iloc[split.val], None),
        "test": _report(y_test, risk_test, thresholds, ds.scenario.iloc[split.test], groups_test),
        # same rule (1% FPR on val) applied to txn_score alone, to show what combining adds
        "test_txn_only": classification_metrics(
            y_test,
            s_test["txn_score"].to_numpy(),
            threshold_for_fpr(y_val, s_val["txn_score"].to_numpy(), mcfg["hold_fpr"]),
        ),
        "f2_unknown_call": _f2_check(base, model, x_test, mcfg),
        "rule_hit_rate_test": {
            "legit": float(hits_test[y_test == 0].mean()),
            "fraud": float(hits_test[y_test == 1].mean()),
        },
    }
    lr_coefs = np.mean([c.estimator[-1].coef_[0] for c in model.calibrated_classifiers_], axis=0)
    return MetaResult(
        model,
        thresholds,
        metrics,
        dict(zip(SCORE_COLUMNS, lr_coefs.round(4).tolist(), strict=True)),
        boost,
    )


def _f2_check(
    base: BaseModels, model: CalibratedClassifierCV, x_test: pd.DataFrame, mcfg: dict[str, Any]
) -> dict[str, float]:
    """Spec 01 F2: the same new-payee payments with and without ctx_unknown_call.

    Only rows whose risk without the call leaves room to rise (<= f2_max_base_risk) count;
    a payment already at 0.95 cannot gain 0.2.
    """
    rows = x_test[x_test["payee_is_new"].astype(bool).to_numpy()]
    boost = mcfg["unknown_call_boost"]
    with_call = rows.assign(ctx_unknown_call=True)
    without = rows.assign(ctx_unknown_call=False, ctx_call_long=False)
    r_with = risk_score(model, base.scores(with_call), with_call, boost)
    r_without = risk_score(model, base.scores(without), without, boost)
    room = r_without <= mcfg["f2_max_base_risk"]
    if not room.any():
        return {}
    diff = r_with[room] - r_without[room]
    return {
        "rows": float(room.sum()),
        "mean_risk_with_call": float(r_with[room].mean()),
        "mean_risk_without_call": float(r_without[room].mean()),
        "mean_difference": float(diff.mean()),
        "min_difference": float(diff.min()),
    }


# --- outputs -----------------------------------------------------------------------------------


def thresholds_json(result: MetaResult, cfg: dict[str, Any], base: BaseModels) -> dict[str, Any]:
    oof = result.metrics["val_oof"]
    return {
        "model": "meta_model",
        "created": date.today().isoformat(),
        "rule": "risk_score > hold -> HOLD; risk_score > step_up -> STEP_UP; else APPROVE",
        "risk_rules": {
            RULE_UNKNOWN_CALL: {
                "when": "ctx_unknown_call and payee_is_new",
                "add": result.boost,
                "cap": 1.0,
            }
        },
        "step_up": result.thresholds["step_up"],
        "hold": result.thresholds["hold"],
        "targets": {
            "step_up_max_fpr": cfg["meta"]["step_up_fpr"],
            "hold_max_fpr": cfg["meta"]["hold_fpr"],
        },
        "validation_out_of_fold": {
            "fpr_hold": oof["fpr"],
            "fpr_step_up_or_hold": oof["fpr_step_up_or_hold"],
            "recall_hold": oof["recall"],
        },
        "inputs": SCORE_COLUMNS,
        "base_models": {
            "txn_model_version": base.txn_meta.get("version"),
            "txn_data_hash": base.txn_meta.get("data_hash"),
        },
    }


def format_table(result: MetaResult) -> str:
    t, b, val = result.metrics["test"], result.metrics["test_txn_only"], result.metrics["val_oof"]
    th = result.thresholds
    lines = [
        "meta_model | isotonic-calibrated logistic regression on the txn, anomaly, social,"
        " graph scores",
        f"rule {RULE_UNKNOWN_CALL}: +{result.boost:.2f} (test hit rate legit "
        f"{result.metrics['rule_hit_rate_test']['legit']:.2%}, fraud "
        f"{result.metrics['rule_hit_rate_test']['fraud']:.2%})",
        f"thresholds (validation, out-of-fold): STEP_UP > {th['step_up']:.4f} (FPR "
        f"{val['fpr_step_up_or_hold']:.2%}), HOLD > {th['hold']:.4f} (FPR {val['fpr']:.2%})",
        "LR weights (standardised inputs): "
        + ", ".join(f"{k} {v:+.2f}" for k, v in result.coefficients.items()),
        "",
        f"{'test metric':<26}{'meta_model':>12}{'txn only':>12}",
    ]
    for label, key in (
        ("PR-AUC", "pr_auc"),
        ("ROC-AUC", "roc_auc"),
        ("Recall @ 1% FPR", "recall_at_1pct_fpr"),
        ("F1 @ HOLD", "f1"),
        ("Recall @ HOLD", "recall"),
        ("FPR @ HOLD", "fpr"),
    ):
        lines.append(f"{label:<26}{t[key]:>12.4f}{b[key]:>12.4f}")
    lines += [
        f"{'Brier (calibration)':<26}{t['brier']:>12.4f}",
        f"{'FPR @ STEP_UP or HOLD':<26}{t['fpr_step_up_or_hold']:>12.4f}",
        "",
        f"{'test decisions':<14}" + "".join(f"{d:>10}" for d in DECISIONS),
    ]
    for label in ("legit", "fraud"):
        lines.append(
            f"{label:<14}" + "".join(f"{t['decisions'][label][d]:>10.1%}" for d in DECISIONS)
        )
    lines += ["", f"{'test scenario':<24}{'HOLD':>10}{'STEP_UP+':>10}"]
    for name, v in sorted(t["by_scenario"].items()):
        lines.append(f"{name:<24}{v['hold']:>10.1%}{v['step_up_or_hold']:>10.1%}")
    if "fpr_gap_points" in t:
        lines += [
            "",
            "FPR gap at HOLD (target < 2 points): "
            + ", ".join(f"{k} {v:.2f} pt" for k, v in t["fpr_gap_points"].items()),
        ]
    f2 = result.metrics["f2_unknown_call"]
    if f2:
        lines.append(
            f"F2 check, {f2['rows']:.0f} new-payee payments with vs without unknown call: risk "
            f"{f2['mean_risk_with_call']:.3f} vs {f2['mean_risk_without_call']:.3f} "
            f"(difference mean {f2['mean_difference']:+.3f}, min {f2['min_difference']:+.3f};"
            " spec wants >= 0.2)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the meta-model and choose thresholds.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--artifacts", type=Path, default=ARTIFACTS, help="base models in, meta model out"
    )
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    base = BaseModels.load(args.artifacts)
    result = train_meta(load_dataset("simulated", cfg), base, cfg)
    table = format_table(result)
    print(table)

    model_path = args.artifacts / "meta_model.pkl"
    joblib.dump(result.model, model_path)
    thresholds_path = args.artifacts / "thresholds.json"
    thresholds_path.write_text(
        json.dumps(thresholds_json(result, cfg, base), indent=2) + "\n", encoding="utf-8"
    )
    metrics_path = args.artifacts / "meta_model.meta.json"
    metrics_path.write_text(
        json.dumps({"coefficients": result.coefficients, "metrics": result.metrics}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    saved = ", ".join(p.name for p in (model_path, thresholds_path, metrics_path))
    print(f"\nsaved {saved} in {args.artifacts}")

    if not args.no_mlflow:
        mlflow = mlflow_client(cfg["meta"]["mlflow_experiment"])
        with mlflow.start_run(run_name="meta-isotonic-lr") as run:
            mlflow.set_tags(
                {"model": "meta_model", "txn_data_hash": base.txn_meta.get("data_hash")}
            )
            mlflow.log_params(
                {
                    "inputs": ",".join(SCORE_COLUMNS),
                    **{f"thr_{k}": v for k, v in result.thresholds.items()},
                    "unknown_call_boost": result.boost,
                }
            )
            flat = {
                f"test_{k}": v for k, v in result.metrics["test"].items() if isinstance(v, float)
            }
            flat |= {f"txn_only_{k}": v for k, v in result.metrics["test_txn_only"].items()}
            mlflow.log_metrics(flat)
            mlflow.log_text(table, "metrics.txt")
            for path in (model_path, thresholds_path, metrics_path):
                mlflow.log_artifact(str(path))
            print(
                f"MLflow run {run.info.run_id} in experiment '{cfg['meta']['mlflow_experiment']}'"
            )
    return 0


if __name__ == "__main__":
    # Run main() from the imported module: the pickled pipeline must reference
    # ml.models.meta_model._logit_probabilities, not __main__, or nothing else can load it.
    from ml.models import meta_model

    raise SystemExit(meta_model.main())
