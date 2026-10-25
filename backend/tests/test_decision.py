"""Decision rules (spec 03 §5, PRD §8, spec 01 F1): pure functions, no services needed.
Question choice and the coach's answers: test_scam_coach.py."""

from typing import Any

from app.models.enums import Decision
from app.services.decision import ActiveRule, decide, rule_matches

STEP_UP, HOLD = 0.1, 0.5


def feats(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "amount": 1000.0,
        "payee_report_count": 0,
        "payee_is_new": False,
        "ctx_unknown_call": False,
        "ctx_call_long": False,
        "ctx_screen_share": False,
        "merchant_category": "GROCERY",
    }
    return {**base, **overrides}


def test_thresholds_are_strict_upper_bounds() -> None:
    assert decide(0.1, STEP_UP, HOLD, feats(), 0.0).decision == Decision.APPROVE
    assert decide(0.100001, STEP_UP, HOLD, feats(), 0.0).decision == Decision.STEP_UP
    assert decide(0.5, STEP_UP, HOLD, feats(), 0.0).decision == Decision.STEP_UP
    assert decide(0.51, STEP_UP, HOLD, feats(), 0.0).decision == Decision.HOLD


def test_actions_follow_the_decision() -> None:
    assert decide(0.01, STEP_UP, HOLD, feats(), 0.0).action == "NONE"
    assert decide(0.9, STEP_UP, HOLD, feats(), 0.0).action == "ANALYST_REVIEW"
    assert decide(0.2, STEP_UP, HOLD, feats(), 0.0).action == "OTP"  # no scam signal


def test_scam_coach_trigger_spec_01_f1() -> None:
    for reason in (
        {"social": 0.5, "f": feats()},
        {"social": 0.0, "f": feats(payee_report_count=1)},
        {"social": 0.0, "f": feats(ctx_unknown_call=True)},
    ):
        out = decide(0.2, STEP_UP, HOLD, reason["f"], reason["social"])
        assert out.action == "SCAM_COACH"
        assert out.decision == out.model_decision == Decision.STEP_UP


def test_coach_goes_first_on_a_scam_signal_hold() -> None:
    """T5.4: the models hold scam-call payments outright; the coach still asks first, and the
    held decision is kept for after the answers."""
    for f in (feats(ctx_unknown_call=True), feats(ctx_screen_share=True),
              feats(payee_report_count=2)):  # fmt: skip
        out = decide(0.9, STEP_UP, HOLD, f, 0.95)
        assert (out.decision, out.action, out.model_decision) == (
            Decision.STEP_UP, "SCAM_COACH", Decision.HOLD,
        )  # fmt: skip
    # no scam signal (e.g. a stolen phone): held at once, as before
    plain = decide(0.9, STEP_UP, HOLD, feats(), 0.0)
    assert (plain.decision, plain.action) == (Decision.HOLD, "ANALYST_REVIEW")


def test_no_second_coach_after_the_answers() -> None:
    f = feats(ctx_unknown_call=True)
    assert decide(0.9, STEP_UP, HOLD, f, 0.9, coach_first=False).action == "ANALYST_REVIEW"
    assert decide(0.2, STEP_UP, HOLD, f, 0.9, coach_first=False).action == "OTP"


def test_reported_payee_is_at_least_step_up_prd_8() -> None:
    out = decide(0.0, STEP_UP, HOLD, feats(payee_report_count=5), 0.0)
    assert out.decision == Decision.STEP_UP and "reported_payee" in out.rule_hits
    assert decide(0.0, STEP_UP, HOLD, feats(payee_report_count=4), 0.0).decision == Decision.APPROVE


def test_admin_rules_raise_but_never_lower() -> None:
    big_new = ActiveRule(
        name="big new payee",
        condition={"all": [{"field": "amount", "op": ">", "value": 50000},
                           {"field": "payee_is_new", "op": "==", "value": True}]},
        action=Decision.HOLD,
        priority=10,
    )  # fmt: skip
    hit = decide(0.0, STEP_UP, HOLD, feats(amount=60000.0, payee_is_new=True), 0.0, [big_new])
    assert hit.decision == Decision.HOLD and hit.rule_hits == ["big new payee"]
    soft = ActiveRule("soft", {"any": [{"field": "amount", "op": ">", "value": 0}]},
                      Decision.STEP_UP, 1)  # fmt: skip
    assert decide(0.9, STEP_UP, HOLD, feats(), 0.0, [soft]).decision == Decision.HOLD


def test_rule_conditions() -> None:
    values = {"amount": 100.0, "merchant_category": "P2P"}
    assert rule_matches({"any": [{"field": "amount", "op": ">=", "value": 100}]}, values)
    assert not rule_matches({"all": []}, values)  # an empty rule matches nothing
    assert not rule_matches({"all": [{"field": "missing", "op": ">", "value": 1}]}, values)
    assert not rule_matches({"all": [{"field": "amount", "op": "~", "value": 1}]}, values)
    assert not rule_matches(
        {"all": [{"field": "merchant_category", "op": ">", "value": 1}]}, values
    )
    assert not rule_matches({"none": []}, values)
