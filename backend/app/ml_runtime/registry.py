"""Model registry: every artifact the scoring path needs, loaded once at startup (spec 02 §3).

Artifacts come from MODEL_DIR (default ml/artifacts): txn_model.json(+meta), anomaly_model.pt,
social_model.txt, meta_model.pkl and thresholds.json, as written by the ml/ pipelines.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb

from ml.features.schema import DTYPES, FEATURE_NAMES, MERCHANT_CATEGORIES
from ml.models.meta_model import SCORE_COLUMNS, BaseModels, apply_rules, rule_hits

logger = logging.getLogger(__name__)

REQUIRED = ("txn_model.json", "anomaly_model.pt", "social_model.txt", "meta_model.pkl",
            "thresholds.json")  # fmt: skip


@dataclass(frozen=True)
class ModelOutput:
    """One payment's scores. `contributions` are the txn model's exact TreeSHAP values."""

    scores: dict[str, float]  # txn_score, anomaly_score, social_eng_score, graph_score
    meta_score: float  # calibrated meta-model probability, before rules
    risk_score: float  # meta_score plus the F2 rule boost, capped at 1
    rule_hits: list[str]
    contributions: dict[str, float]  # feature -> SHAP value (log-odds) for txn_model


class ModelRegistry:
    def __init__(self, base: BaseModels, meta: Any, thresholds: dict[str, Any]) -> None:
        self.base = base
        self.meta = meta
        self.thresholds = thresholds
        self.step_up = float(thresholds["step_up"])
        self.hold = float(thresholds["hold"])
        self.boost = float(thresholds["risk_rules"]["unknown_call_new_payee"]["add"])
        txn_version = base.txn_meta.get("version", "?")
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
        """CPU-bound (~20 ms): call it from a worker thread, not the event loop."""
        X = to_frame(features)
        scores = self.base.scores(X)
        meta = float(self.meta.predict_proba(scores[SCORE_COLUMNS].to_numpy())[:, 1][0])
        risk = float(apply_rules(np.array([meta]), X, self.boost)[0])  # = risk_score()
        hits = ["unknown_call_new_payee"] if bool(rule_hits(X)[0]) else []
        contribs = self.base.txn.predict(
            xgb.DMatrix(X[FEATURE_NAMES], enable_categorical=True), pred_contribs=True
        )[0]
        return ModelOutput(
            scores={k: float(v) for k, v in scores.iloc[0].items()},
            meta_score=meta,
            risk_score=risk,
            rule_hits=hits,
            contributions=dict(zip(FEATURE_NAMES, map(float, contribs[:-1]), strict=True)),
        )


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
