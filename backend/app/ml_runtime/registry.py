"""Model registry: every artifact the scoring path needs, loaded once at startup (spec 02 §3).

Artifacts come from MODEL_DIR (default ml/artifacts): txn_model.json(+meta), anomaly_model.pt,
social_model.txt, meta_model.pkl and thresholds.json, as written by the ml/ pipelines.

`predict` is the per-payment hot path (T3.9 load test): one numpy row and one XGBoost DMatrix
shared by the score and its SHAP values, numpy for LightGBM, and the calibrated meta-model as
plain numpy (FastMeta). For a single row, pandas/sklearn per-call overhead was ~40 of 54 ms and
held the GIL. tests/test_registry.py checks it still equals the ML pipeline exactly.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import torch
import xgboost as xgb

from app.services.explain import combine, meta_weights, tree_shap
from ml.features.schema import DTYPES, FEATURE_NAMES, MERCHANT_CATEGORIES
from ml.models.meta_model import SCORE_COLUMNS, BaseModels, _logit_probabilities
from ml.models.social_model import SOCIAL_FEATURES

logger = logging.getLogger(__name__)

REQUIRED = ("txn_model.json", "anomaly_model.pt", "social_model.txt", "meta_model.pkl",
            "thresholds.json")  # fmt: skip

COL = {name: i for i, name in enumerate(FEATURE_NAMES)}
CATEGORY_CODE = {c: i for i, c in enumerate(MERCHANT_CATEGORIES)}
TYPES = ["c" if DTYPES[n] == "category" else "float" for n in FEATURE_NAMES]
SOCIAL_IDX = [COL[n] for n in SOCIAL_FEATURES]
COACH = SOCIAL_FEATURES.index("coach_red_flags")


@dataclass(frozen=True)
class ModelOutput:
    """One payment's scores. `contributions`: each feature's effect on the meta-model log-odds
    (TreeSHAP of txn_model + social_model, see services/explain.py)."""

    scores: dict[str, float]  # txn_score, anomaly_score, social_eng_score, graph_score
    meta_score: float  # calibrated meta-model probability, before rules
    risk_score: float  # meta_score plus the F2 rule boost, capped at 1
    rule_hits: list[str]
    contributions: dict[str, float]  # feature -> meta-model log-odds


class ModelRegistry:
    def __init__(self, base: BaseModels, meta: Any, thresholds: dict[str, Any]) -> None:
        self.base = base
        self.meta = meta
        self.thresholds = thresholds
        self.step_up = float(thresholds["step_up"])
        self.hold = float(thresholds["hold"])
        self.boost = float(thresholds["risk_rules"]["unknown_call_new_payee"]["add"])
        txn_version = base.txn_meta.get("version", "?")
        self.shap_weights = meta_weights(meta)
        self.fast_meta = FastMeta.of(meta)
        base.txn.set_param({"nthread": 1})  # one row per call: no thread pool (T3.9)
        pre = base.anomaly.pre
        self.anomaly_idx = [COL[n] for n in pre.features]
        torch.set_num_threads(1)
        self.version = f"ensemble-{thresholds.get('created', 'unknown')}-txn{txn_version}"

    @classmethod
    def load(cls, folder: Path) -> "ModelRegistry":
        missing = [name for name in REQUIRED if not (folder / name).exists()]
        if missing:
            raise FileNotFoundError(f"model artifacts missing in {folder}: {missing}")
        thresholds = json.loads((folder / "thresholds.json").read_text(encoding="utf-8"))
        registry = cls(BaseModels.load(folder), joblib.load(folder / "meta_model.pkl"), thresholds)
        registry.predict(_warmup_row())  # first call pays for lazy init, not a customer
        logger.info("models loaded", extra={"model_version": registry.version})
        return registry

    def predict(self, features: dict[str, Any]) -> ModelOutput:
        """CPU-bound (~10 ms): call it from a worker thread, not the event loop."""
        row = encode(features)
        dmatrix = xgb.DMatrix(row.astype("float32"), feature_names=FEATURE_NAMES,
                              feature_types=TYPES, enable_categorical=True, nthread=1)  # fmt: skip
        social_in = row[:, SOCIAL_IDX]
        social_in[:, COACH] = 0.0  # first decision: no coach answers yet (as BaseModels.scores)
        scores = {
            "txn_score": float(self.base.txn.predict(dmatrix)[0]),
            "anomaly_score": self._anomaly(row),
            "social_eng_score": float(self.base.social.predict(social_in, num_threads=1)[0]),
            "graph_score": float(row[0, COL["graph_score"]]),  # 0 until phase 5
        }
        if self.fast_meta is not None:
            meta = self.fast_meta.predict(scores)
        else:
            x = np.array([[scores[c] for c in SCORE_COLUMNS]])
            meta = float(self.meta.predict_proba(x)[:, 1][0])
        hit = bool(features["ctx_unknown_call"]) and bool(features["payee_is_new"])  # F2 rule
        return ModelOutput(
            scores=scores,
            meta_score=meta,
            risk_score=min(1.0, meta + self.boost * hit),  # = meta_model.apply_rules
            rule_hits=["unknown_call_new_payee"] if hit else [],
            contributions=combine(tree_shap(self.base, dmatrix, social_in), self.shap_weights),
        )

    def _anomaly(self, row: np.ndarray) -> float:
        """AnomalyModel.score for one row without a DataFrame: log1p, scale, raw, min-max."""
        model = self.base.anomaly
        pre = model.pre
        values = pre._log(row[:, self.anomaly_idx], pre.features, pre.log_columns)
        z = ((values - pre.mean) / pre.scale).astype("float32")
        return float(model.minmax.apply(model.scorer.raw(z))[0])


def encode(features: dict[str, Any]) -> np.ndarray:
    """One feature dict -> a (1, 38) float row; merchant_category as its category code."""
    values = [
        CATEGORY_CODE.get(features[n], np.nan) if TYPES[i] == "c" else float(features[n])
        for i, n in enumerate(FEATURE_NAMES)
    ]
    return np.array([values], dtype="float64")


@dataclass(frozen=True)
class FastMeta:
    """CalibratedClassifierCV(Pipeline(logit, StandardScaler, LogisticRegression), isotonic) as
    numpy: per fold z = (logit(x) - mean) / scale, d = z.coef + b, p = isotonic(d) (np.interp on
    the fitted thresholds = out_of_bounds "clip"), then the mean over folds."""

    means: np.ndarray  # (folds, 4)
    scales: np.ndarray
    coefs: np.ndarray
    intercepts: np.ndarray  # (folds,)
    thresholds: list[tuple[np.ndarray, np.ndarray]]  # isotonic (X, y) per fold

    @classmethod
    def of(cls, meta: Any) -> "FastMeta | None":
        """None (fall back to sklearn) when the saved meta-model isn't the shape this mirrors."""
        try:
            folds = meta.calibrated_classifiers_
            steps = [f.estimator.named_steps for f in folds]
            if any(f.method != "isotonic" or len(f.calibrators) != 1 for f in folds) or any(
                st["functiontransformer"].func is not _logit_probabilities for st in steps
            ):
                return None
            return cls(
                means=np.array([st["standardscaler"].mean_ for st in steps]),
                scales=np.array([st["standardscaler"].scale_ for st in steps]),
                coefs=np.array([st["logisticregression"].coef_[0] for st in steps]),
                intercepts=np.array([st["logisticregression"].intercept_[0] for st in steps]),
                thresholds=[
                    (f.calibrators[0].X_thresholds_, f.calibrators[0].y_thresholds_) for f in folds
                ],  # fmt: skip
            )
        except (AttributeError, KeyError, IndexError):
            return None

    def predict(self, scores: dict[str, float]) -> float:
        x = _logit_probabilities(np.array([[scores[c] for c in SCORE_COLUMNS]]))[0]
        d = ((x - self.means) / self.scales * self.coefs).sum(axis=1) + self.intercepts
        p = [np.interp(di, xt, yt) for di, (xt, yt) in zip(d, self.thresholds, strict=True)]
        return float(np.mean(np.clip(p, 0.0, 1.0)))


def to_frame(features: dict[str, Any]) -> pd.DataFrame:
    """One feature dict -> the typed one-row frame the models were trained on."""
    X = pd.DataFrame([[features[n] for n in FEATURE_NAMES]], columns=FEATURE_NAMES)
    for name, dtype in DTYPES.items():
        if dtype == "category":
            X[name] = pd.Categorical(X[name], categories=MERCHANT_CATEGORIES)
        else:
            X[name] = X[name].astype(dtype)
    return X


def _warmup_row() -> dict[str, Any]:
    row: dict[str, Any] = {n: np.zeros(1, dtype=d)[0].item() for n, d in DTYPES.items()
                           if d != "category"}  # fmt: skip
    row["merchant_category"] = MERCHANT_CATEGORIES[0]
    row["amount"] = 100.0
    return row
