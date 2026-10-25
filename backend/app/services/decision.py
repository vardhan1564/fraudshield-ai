"""Decision and action for one scored payment (spec 03 §5, PRD §8, spec 01 F1).

1. Model: risk_score > hold -> HOLD; > step_up -> STEP_UP; else APPROVE (thresholds.json, chosen
   on validation for FPR <= 1% / 5%; spec 03 §5 wins over the PRD's illustrative 0.30 / 0.70).
2. Product rule: a payee reported >= 5 times is at least STEP_UP (force a warning).
3. Admin rules (rules table): a hit raises the decision to the rule's action, never lowers it.
4. Action: HOLD -> ANALYST_REVIEW; STEP_UP -> SCAM_COACH when it looks like social engineering
   (spec 01 F1 trigger), else OTP; APPROVE -> NONE. TRUSTED_CONTACT comes with T5.6.
5. Coach first (T5.4, spec 01 F1): a payment with scam signals (unknown caller, screen sharing,
   a reported payee) that would be held is answered STEP_UP + SCAM_COACH instead, and the held
   decision is kept in `model_decision`. The trained models hold almost every payment made on a
   scam call, so without this the coach would never be seen. After the answers
   (services/scam_coach.py) the final decision is never below `model_decision`: the coach can only
   raise a decision, never release a payment the models would hold.
"""

import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.enums import Decision

REPORTED_PAYEE_MIN = 5  # PRD §8
SOCIAL_COACH_MIN = 0.5  # spec 01 F1 trigger
Action = Literal["NONE", "SCAM_COACH", "OTP", "TRUSTED_CONTACT", "ANALYST_REVIEW"]
RANK = {Decision.APPROVE: 0, Decision.STEP_UP: 1, Decision.HOLD: 2}

OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass(frozen=True)
class ActiveRule:
    """A row of the rules table, as cached in memory (no DB call while scoring)."""

    name: str
    condition: dict[str, Any]
    action: Decision  # STEP_UP or HOLD
    priority: int


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    action: Action
    rule_hits: list[str] = field(default_factory=list)
    # what the models and rules decided; differs from `decision` only when the coach goes first
    model_decision: Decision | None = None


def rule_matches(condition: dict[str, Any], values: dict[str, Any]) -> bool:
    """`{"all": [...]}` or `{"any": [...]}` of `{"field", "op", "value"}`; unknown field -> no."""

    def check(c: dict[str, Any]) -> bool:
        if c.get("field") not in values or c.get("op") not in OPS:
            return False
        try:
            return OPS[c["op"]](values[c["field"]], c["value"])
        except TypeError:  # e.g. comparing a string with a number
            return False

    if "all" in condition:
        return bool(condition["all"]) and all(check(c) for c in condition["all"])
    if "any" in condition:
        return any(check(c) for c in condition["any"])
    return False


def from_score(risk: float, step_up: float, hold: float) -> Decision:
    if risk > hold:
        return Decision.HOLD
    if risk > step_up:
        return Decision.STEP_UP
    return Decision.APPROVE


def scam_signals(features: dict[str, Any]) -> bool:
    """Signs the customer is being talked into this payment (spec 01 F1/F2)."""
    return (
        bool(features["ctx_unknown_call"])
        or bool(features["ctx_screen_share"])
        or int(features["payee_report_count"]) > 0
    )


def decide(
    risk: float,
    step_up: float,
    hold: float,
    features: dict[str, Any],
    social_eng_score: float,
    rules: Sequence[ActiveRule] = (),
    coach_first: bool = True,
) -> Outcome:
    """`coach_first=False` after the coach has been answered (no second round of questions)."""
    decision = from_score(risk, step_up, hold)
    hits: list[str] = []
    if int(features["payee_report_count"]) >= REPORTED_PAYEE_MIN:
        hits.append("reported_payee")
        decision = max(decision, Decision.STEP_UP, key=RANK.__getitem__)
    for rule in sorted(rules, key=lambda r: r.priority):
        if rule_matches(rule.condition, features):
            hits.append(rule.name)
            decision = max(decision, rule.action, key=RANK.__getitem__)

    if decision == Decision.HOLD:
        if coach_first and scam_signals(features):
            return Outcome(Decision.STEP_UP, "SCAM_COACH", hits, Decision.HOLD)
        return Outcome(decision, "ANALYST_REVIEW", hits, decision)
    if decision == Decision.APPROVE:
        return Outcome(decision, "NONE", hits, decision)
    social = (
        social_eng_score >= SOCIAL_COACH_MIN
        or int(features["payee_report_count"]) > 0
        or bool(features["ctx_unknown_call"])
    )
    if social and coach_first:
        return Outcome(decision, "SCAM_COACH", hits, decision)
    return Outcome(decision, "OTP", hits, decision)
