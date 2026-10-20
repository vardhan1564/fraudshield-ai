"""SHAP reasons for one scored payment (spec 01 F4, spec 03 §7).

Exact TreeSHAP from the boosters themselves (XGBoost `pred_contribs`, LightGBM `pred_contrib`:
the same algorithm as shap.TreeExplainer, without its per-call set-up) for the two tree models
that move the meta-model, txn_model and social_model. The meta-model is a logistic regression on
standardised logits, so one feature's effect on its log-odds is exactly
sum_m w_m * SHAP_m(feature), with w_m = coef_m / scale_m averaged over the calibration folds.
Isotonic calibration on top is monotone, so the ranking also holds for risk_score. `impact` is in
meta-model log-odds; the F2 rule's entry carries its fixed boost instead.
"""

import math
from collections import defaultdict
from typing import Any

import numpy as np
import xgboost as xgb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import ModelScore, Transaction, User
from app.models.enums import Decision, Language, Role
from app.schemas.transactions import ExplainResponse, Reason
from app.services.lookups import by_ref
from app.services.reason_map import phrase
from ml.features.schema import FEATURE_NAMES
from ml.models import social_model
from ml.models.meta_model import SCORE_COLUMNS, BaseModels

SHAP_MODELS = {"txn_model": "txn_score", "social_model": "social_eng_score"}
STORED = 10  # spec 03 §7: top 10 per scored payment in model_scores.shap_json
TOP_MIN, TOP_MAX = 3, 5  # spec 01 F4: 3-5 reasons
MIN_IMPACT = 0.05  # reasons 4 and 5 only when they matter
RULE_FEATURES = {
    "unknown_call_new_payee": "ctx_unknown_call",
    "reported_payee": "payee_report_count",
}


def meta_weights(meta: Any) -> dict[str, float]:
    """Log-odds of the meta-model per unit of each input (logit for txn/social, raw otherwise)."""
    per_fold = []
    for fold in meta.calibrated_classifiers_:
        steps = fold.estimator.named_steps
        per_fold.append(steps["logisticregression"].coef_[0] / steps["standardscaler"].scale_)
    mean = np.mean(per_fold, axis=0)
    return {column: float(w) for column, w in zip(SCORE_COLUMNS, mean, strict=True)}


def tree_shap(
    base: BaseModels, dmatrix: xgb.DMatrix, social_in: np.ndarray
) -> dict[str, dict[str, float]]:
    """Per-model TreeSHAP for one row (log-odds; the bias term is dropped). Takes the inputs the
    registry already built for scoring, so nothing is converted twice."""
    txn = base.txn.predict(dmatrix, pred_contribs=True)[0]
    social = np.asarray(base.social.predict(social_in, pred_contrib=True, num_threads=1))[0]
    return {
        "txn_model": dict(zip(FEATURE_NAMES, map(float, txn[:-1]), strict=True)),
        "social_model": dict(
            zip(social_model.SOCIAL_FEATURES, map(float, social[:-1]), strict=True)
        ),
    }


def combine(shap: dict[str, dict[str, float]], weights: dict[str, float]) -> dict[str, float]:
    """Each feature's effect on the meta-model log-odds, summed over the tree models."""
    total: dict[str, float] = defaultdict(float)
    for model, values in shap.items():
        w = weights[SHAP_MODELS[model]]
        for feature, value in values.items():
            total[feature] += w * value
    return {name: total[name] for name in FEATURE_NAMES}


def jsonable(value: Any) -> float | int | bool | str:
    if isinstance(value, bool | np.bool_):
        return bool(value)
    if isinstance(value, int | np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        return round(float(value), 4) if math.isfinite(value) else 0.0
    return str(value)


def ranked(
    contributions: dict[str, float],
    features: dict[str, Any],
    rule_hits: list[str],
    boost: float,
) -> list[dict[str, Any]]:
    """The stored top 10 by |impact| (both directions), plus the rules that fired, first."""
    items = sorted(contributions.items(), key=lambda fv: -abs(fv[1]))
    out = [{"feature": f, "value": jsonable(features[f]), "impact": round(v, 4)} for f, v in items]
    for hit in reversed(rule_hits):
        feature = RULE_FEATURES.get(hit)
        if feature is None:  # admin rules have no feature of their own
            continue
        entry = next((e for e in out if e["feature"] == feature), None)
        if entry is None:  # no SHAP value for it: the rule still explains the decision
            entry = {"feature": feature, "value": jsonable(features[feature]), "impact": 0.0}
        else:
            out.remove(entry)
        if hit == "unknown_call_new_payee":
            entry = {**entry, "impact": round(boost, 4)}
        out.insert(0, {**entry, "rule": hit})
    return out[:STORED]


def top_reasons(stored: list[dict[str, Any]], language: Language) -> list[Reason]:
    """3-5 reasons that pushed towards fraud: rule hits first, then by impact. One reason per
    text (amount and amount_log read the same)."""
    picks: list[Reason] = []
    for entry in stored:
        if len(picks) == TOP_MAX:
            break
        if "rule" in entry or (
            entry["impact"] > 0 and (len(picks) < TOP_MIN or entry["impact"] >= MIN_IMPACT)
        ):
            reason = to_reason(entry, language)
            if all(p.text != reason.text for p in picks):
                picks.append(reason)
    return picks


def to_reason(entry: dict[str, Any], language: Language) -> Reason:
    return Reason(
        feature=entry["feature"],
        value=entry["value"],
        impact=entry["impact"],
        text=phrase(entry["feature"], entry["value"], language),
    )


TEMPLATES: dict[str, dict[Language, str]] = {
    "HOLD": {
        Language.EN: (
            "We paused this payment. Flagged because: {reasons}. An analyst will check it."
        ),
        Language.HI: "हमने यह भुगतान रोक दिया है। कारण: {reasons}। एक विश्लेषक इसकी जाँच करेगा।",
        Language.MR: "आम्ही हे पेमेंट थांबवले आहे. कारण: {reasons}. एक विश्लेषक ते तपासेल.",
    },
    "SCAM_COACH": {
        Language.EN: (
            "Before you pay, please answer a few quick questions. Flagged because: {reasons}."
        ),
        Language.HI: "भुगतान से पहले कृपया कुछ छोटे सवालों के जवाब दें। कारण: {reasons}।",
        Language.MR: "पेमेंट करण्यापूर्वी कृपया काही छोट्या प्रश्नांची उत्तरे द्या. कारण: {reasons}.",
    },
    "STEP_UP": {
        Language.EN: (
            "Please confirm this payment with the OTP we sent you. Flagged because: {reasons}."
        ),
        Language.HI: "कृपया हमारे भेजे गए OTP से इस भुगतान की पुष्टि करें। कारण: {reasons}।",
        Language.MR: "कृपया आम्ही पाठवलेल्या OTP ने या पेमेंटची खात्री करा. कारण: {reasons}.",
    },
    "APPROVE": {
        Language.EN: "This payment looks normal.",
        Language.HI: "यह भुगतान सामान्य लगता है।",
        Language.MR: "हे पेमेंट सामान्य दिसते.",
    },
}


def template_sentence(
    decision: Decision, action: str, reasons: list[str], language: Language
) -> str:
    """Spec 01 F4 fallback ("Flagged because: r1, r2, r3."), worded for the decision."""
    if decision == Decision.HOLD:
        key = "HOLD"
    elif decision == Decision.STEP_UP:
        key = "SCAM_COACH" if action == "SCAM_COACH" else "STEP_UP"
    else:
        key = "APPROVE"
    texts = reasons[:3]
    if language == Language.EN:
        texts = [t[0].lower() + t[1:] if t[:2] != t[:2].upper() else t for t in texts]
    return TEMPLATES[key][language].format(reasons=", ".join(texts) or "-")


async def explanation_for(
    txn_ref: str, viewer: User, session: AsyncSession, language: Language | None
) -> ExplainResponse:
    """GET /transactions/{id}/explain: analysts and admins see any payment, customers their own.
    `txn_ref` is the client txn_id or our UUID. Another language than the stored sentence's
    gets the template sentence in that language."""
    query = select(Transaction, ModelScore, User).join(
        ModelScore, ModelScore.txn_id == Transaction.id
    ).join(User, User.id == Transaction.user_id)  # fmt: skip
    query = query.where(by_ref(txn_ref))
    row = (await session.execute(query)).first()
    if row is None or (viewer.role == Role.CUSTOMER and row[0].user_id != viewer.id):
        raise AppError(404, "NOT_FOUND", "Transaction not found")  # no hint it exists
    txn, scores, owner = row
    lang = language or scores.explanation_lang or owner.language
    stored: list[dict[str, Any]] = (scores.shap_json or {}).get("top", [])
    shap = [to_reason(e, lang) for e in stored]
    if scores.explanation and lang == (scores.explanation_lang or owner.language):
        sentence = scores.explanation
    else:
        texts = [r.text for r in top_reasons(stored, lang)]
        sentence = template_sentence(txn.decision or Decision.APPROVE, txn.action or "NONE",
                                     texts, lang)  # fmt: skip
    return ExplainResponse(txn_id=txn.txn_id, shap=shap, explanation=sentence, language=lang)
