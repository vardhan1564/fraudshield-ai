"""Training pipeline: time split, metrics on hand-computed scores, and an end-to-end run."""

import numpy as np
import pandas as pd
import pytest

from ml.pipelines.metrics import (
    classification_metrics,
    recall_at_fpr,
    scenario_recall,
    threshold_for_fpr,
)
from ml.pipelines.split import time_split

# --- split -------------------------------------------------------------------------------------


def test_time_split_sizes_and_order() -> None:
    rng = np.random.default_rng(0)
    ts = pd.Series(rng.permutation(1000))  # shuffled times
    s = time_split(ts)
    assert (len(s.train), len(s.val), len(s.test)) == (700, 150, 150)
    assert ts.iloc[s.train].max() < ts.iloc[s.val].min()
    assert ts.iloc[s.val].max() < ts.iloc[s.test].min()
    assert len(np.unique(np.concatenate(s))) == 1000  # every row exactly once


def test_time_split_with_ties_never_goes_backwards() -> None:
    ts = pd.Series([3, 1, 2, 2, 2, 1, 3, 3, 2, 1] * 10)
    s = time_split(ts)
    assert ts.iloc[s.train].max() <= ts.iloc[s.val].min()
    assert ts.iloc[s.val].max() <= ts.iloc[s.test].min()


def test_time_split_rejects_bad_fractions() -> None:
    with pytest.raises(ValueError):
        time_split(pd.Series(range(10)), (0.8, 0.15, 0.15))


# --- metrics -----------------------------------------------------------------------------------

# 100 legit rows scored 0.00 … 0.99; 10 fraud rows: 5 obvious (2.0) and 5 hidden (0.5).
Y = np.array([0] * 100 + [1] * 10)
S = np.concatenate([np.arange(100) / 100, [2.0] * 5, [0.5] * 5])


def test_recall_at_1pct_fpr_by_hand() -> None:
    # Allowing 1 false positive (the 0.99 legit row) catches only the 5 obvious frauds.
    assert recall_at_fpr(Y, S, 0.01) == pytest.approx(0.5)
    # Allowing 50 false positives (FPR 50%) also reaches the hidden ones at 0.5.
    assert recall_at_fpr(Y, S, 0.50) == pytest.approx(1.0)


def test_threshold_for_fpr_by_hand() -> None:
    thr = threshold_for_fpr(Y, S, 0.01)
    assert thr == pytest.approx(0.98)  # flags 0.99 (1 legit) and the five 2.0s
    m = classification_metrics(Y, S, thr)
    assert m["fpr"] == pytest.approx(0.01)
    assert m["recall"] == pytest.approx(0.5)
    # precision 5/6, recall 5/10 -> F1 = 2*(5/6*0.5)/(5/6+0.5) = 0.625
    assert m["f1"] == pytest.approx(0.625)
    assert m["fraud_rate"] == pytest.approx(10 / 110)


def test_threshold_with_no_room_flags_nothing_legit() -> None:
    thr = threshold_for_fpr(Y, S, 0.001)  # 0.1% of 100 legit rows < 1 row
    assert ((thr < S) & (Y == 0)).sum() == 0


def test_scenario_recall() -> None:
    scenario = pd.Series(["a", "a", "b", None, None])
    flagged = np.array([True, False, True, True, False])
    assert scenario_recall(scenario, flagged) == {"a": 0.5, "b": 1.0}
