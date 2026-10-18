"""Model metrics (spec 03 §6): PR-AUC, ROC-AUC, recall at a fixed FPR, F1, per-scenario recall."""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score, roc_curve


def recall_at_fpr(y_true: np.ndarray, scores: np.ndarray, max_fpr: float = 0.01) -> float:
    """Best recall (TPR) reachable while the false-positive rate stays at or below max_fpr."""
    fpr, tpr, _ = roc_curve(y_true, scores)
    return float(tpr[fpr <= max_fpr].max())


def threshold_for_fpr(y_true: np.ndarray, scores: np.ndarray, max_fpr: float = 0.01) -> float:
    """Score threshold (flag when score > threshold) whose FPR on these rows is <= max_fpr.

    Chosen on validation and then applied unchanged to test (spec 03 §5, HOLD threshold).
    """
    negatives = np.sort(np.asarray(scores)[np.asarray(y_true) == 0])
    allowed = int(np.floor(max_fpr * len(negatives)))  # negatives we may flag
    if allowed == 0:
        return float(negatives[-1])
    return float(negatives[len(negatives) - allowed - 1])


def classification_metrics(
    y_true: np.ndarray, scores: np.ndarray, threshold: float, max_fpr: float = 0.01
) -> dict[str, float]:
    y_true = np.asarray(y_true).astype(int)
    flagged = np.asarray(scores) > threshold
    negatives = y_true == 0
    return {
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "recall_at_1pct_fpr": recall_at_fpr(y_true, scores, max_fpr),
        "f1": float(f1_score(y_true, flagged, zero_division=0)),
        "recall": float(flagged[y_true == 1].mean()) if (y_true == 1).any() else float("nan"),
        "fpr": float(flagged[negatives].mean()) if negatives.any() else float("nan"),
        "fraud_rate": float(y_true.mean()),
    }


def fpr_gap(y_true: np.ndarray, flagged: np.ndarray, groups: pd.DataFrame) -> dict[str, float]:
    """Largest difference in false-positive rate between groups, in percentage points, per
    column (spec 03 §6 target: < 2 points). Only legit rows count: FPR is about wrong flags."""
    legit = np.asarray(y_true) == 0
    out = {}
    for col in groups.columns:
        rates = pd.Series(np.asarray(flagged)[legit]).groupby(groups[col].to_numpy()[legit]).mean()
        out[col] = float((rates.max() - rates.min()) * 100) if len(rates) > 1 else 0.0
    return out


def scenario_recall(scenario: pd.Series, flagged: np.ndarray) -> dict[str, float]:
    """Share of each fraud scenario's rows that the threshold catches (spec target: each >= 80%)."""
    frame = pd.DataFrame({"scenario": scenario.to_numpy(), "flagged": flagged})
    fraud = frame[frame["scenario"].notna()]
    return {str(k): float(v) for k, v in fraud.groupby("scenario")["flagged"].mean().items()}
