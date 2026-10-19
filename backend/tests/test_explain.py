"""explain: exact SHAP maths on real (small) models, reason ranking and template sentences."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.ml_runtime.registry import ModelRegistry, to_frame
from app.models.enums import Decision, Language
from app.services import explain
from ml.features.schema import FEATURE_NAMES
from ml.models.meta_model import SCORE_COLUMNS, _logit_probabilities
from ml.pipelines.datasets import Dataset
from ml.tests.conftest import mini_artifacts  # noqa: F401  # pytest fixture
from tests.test_registry import model_dir  # noqa: F401  # pytest fixture


def test_contributions_explain_the_meta_model_exactly(
    model_dir: Path,  # noqa: F811
    mini_artifacts: tuple[Path, Dataset],  # noqa: F811
) -> None:
    """Meta log-odds (mean over folds) = sum of contributions + anomaly/graph terms + a constant."""
    registry = ModelRegistry.load(model_dir)
    w = registry.shap_weights
    X = mini_artifacts[1].X
    offsets = []
    for i in np.linspace(0, len(X) - 1, 25).astype(int):
        row = {n: X[n].iloc[i] for n in FEATURE_NAMES}
        frame = to_frame(row)
        scores = registry.base.scores(frame)[SCORE_COLUMNS].to_numpy()
        logits = _logit_probabilities(scores)
        if np.abs(logits[0, [0, 2]]).max() > 13:  # clipped at 1e-6: not linear any more
            continue
        meta_logit = np.mean(
            [
                c.estimator.decision_function(scores)[0]
                for c in registry.meta.calibrated_classifiers_
            ]
        )
        out = registry.predict(row)
        rest = w["anomaly_score"] * scores[0, 1] + w["graph_score"] * scores[0, 3]
        offsets.append(meta_logit - sum(out.contributions.values()) - rest)
    assert len(offsets) >= 10
    assert np.ptp(offsets) < 1e-3  # the same constant for every row


def test_ranked_puts_rules_first_and_keeps_ten() -> None:
    contributions = {n: 0.0 for n in FEATURE_NAMES} | {
        "amount": 2.0, "hour": -1.5, "ctx_unknown_call": 0.01, "payee_report_count": 0.3,
    }  # fmt: skip
    features: dict[str, Any] = {n: 0 for n in FEATURE_NAMES} | {
        "amount": 500.0, "ctx_unknown_call": True, "payee_report_count": 7,
    }  # fmt: skip
    stored = explain.ranked(
        contributions, features, ["unknown_call_new_payee", "reported_payee", "fuel cap"], 0.25
    )
    assert len(stored) == explain.STORED
    assert [e["feature"] for e in stored[:4]] == [
        "ctx_unknown_call", "payee_report_count", "amount", "hour"
    ]  # fmt: skip
    assert stored[0] == {"feature": "ctx_unknown_call", "value": True, "impact": 0.25,
                         "rule": "unknown_call_new_payee"}  # fmt: skip
    assert stored[1]["impact"] == 0.3 and stored[3]["impact"] == -1.5


def entry(feature: str, impact: float, value: Any = 1, **extra: Any) -> dict[str, Any]:
    return {"feature": feature, "value": value, "impact": impact, **extra}


def test_top_reasons_three_to_five_positive_and_distinct() -> None:
    stored = [
        entry("amount", 3.0, 48500.0),
        entry("amount_log", 2.0, math.log1p(48_500)),  # same text as amount: skipped
        entry("hour", -1.0, 2),  # lowers the risk: never a reason
        entry("payee_is_new", 0.5, True),
        entry("txn_count_1h", 0.02, 4),  # small, but still needed to reach 3
        entry("is_vpn", 0.01, True),  # small and 3 already: skipped
    ]
    got = explain.top_reasons(stored, Language.EN)
    assert [r.feature for r in got] == ["amount", "payee_is_new", "txn_count_1h"]
    counts = ("txn_count_5m", "txn_count_1h", "txn_count_24h", "failed_count_1h", "graph_degree")
    many = [entry(f, 1.0, 5) for f in (*counts, "hops_to_fraud")]
    assert len(explain.top_reasons(many, Language.EN)) == explain.TOP_MAX
    ruled = [entry("payee_report_count", -0.1, 9, rule="reported_payee")]
    assert explain.top_reasons(ruled, Language.EN)[0].text == "Payee reported by 9 users"


@pytest.mark.parametrize(
    ("decision", "action", "start"),
    [
        (Decision.HOLD, "ANALYST_REVIEW", "We paused this payment. Flagged because: payee rep"),
        (Decision.STEP_UP, "SCAM_COACH", "Before you pay, please answer a few quick questions."),
        (Decision.STEP_UP, "OTP", "Please confirm this payment with the OTP we sent you."),
        (Decision.APPROVE, "NONE", "This payment looks normal."),
    ],
)
def test_template_sentence(decision: Decision, action: str, start: str) -> None:
    reasons = ["Payee reported by 9 users", "UPI payment", "New device", "VPN is on"]
    sentence = explain.template_sentence(decision, action, reasons, Language.EN)
    assert sentence.startswith(start)
    assert "VPN" not in sentence  # at most three reasons
    if decision != Decision.APPROVE:
        assert "UPI payment" in sentence  # acronyms keep their capitals
    for lang in (Language.HI, Language.MR):
        assert explain.template_sentence(decision, action, reasons, lang) != sentence
