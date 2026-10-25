"""Scam Coach (spec 01 F1, T5.4): questions before a risky payment, answers that can stop it.

1. Scoring (services/scoring.py): when the decision is STEP_UP + SCAM_COACH, `questions_for`
   picks 2-3 questions for the scam the signals point to, and `save_context` keeps what the
   answers will need in Redis `coach:{txn_id}` for an hour: the scored event, the 38 features and
   the decision the models reached (HOLD when the coach went first, see services/decision.py).
2. POST /transactions/{id}/coach -> `answer`: count the red flags, re-score with
   `coach_red_flags` (the social model was trained on it), then decide. The final decision is the
   highest of the re-scored decision, the models' first decision, and HOLD if any answer is a red
   flag. A coach_sessions row is written, the payment and its scores are updated, and the updated
   event goes back on stream:transactions.scored, so the worker raises the analyst alert and
   writes the LLM sentence exactly as for any held payment.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.ml_runtime.registry import ModelRegistry
from app.models import CoachSession, ModelScore, Transaction, User
from app.models.enums import Decision, Language
from app.schemas.transactions import CoachResponse
from app.services import explain, persist
from app.services.decision import RANK, Action, ActiveRule, decide
from app.streaming import bus

CONTEXT_TTL_S = 3600  # answers after an hour are refused; the customer pays again
MIN_QUESTIONS, MAX_QUESTIONS = 2, 3


@dataclass(frozen=True)
class Question:
    text: str
    red_flag: bool  # the answer that is a warning sign


# spec 01 F1 question bank
QUESTIONS: dict[str, Question] = {
    "on_call_instructed": Question("Is someone on a call telling you to make this payment?", True),
    "threat_or_arrest": Question(
        "Did anyone say you'll be arrested, fined or your account blocked?", True
    ),
    "refund_to_receive": Question("Are you paying to receive a refund, prize or cashback?", True),
    "promised_returns": Question("Were you promised high or guaranteed returns?", True),
    "asked_secrecy": Question("Were you told not to tell family or the bank?", True),
    "know_payee": Question("Have you met or paid this person before?", False),
}

# The scam the signals point to -> its most telling questions, best first (spec 01 F1).
BY_SCAM: dict[str, list[str]] = {
    "digital_arrest": ["on_call_instructed", "threat_or_arrest", "asked_secrecy"],
    "fake_support_refund": ["on_call_instructed", "refund_to_receive", "know_payee"],
    "investment_task": ["promised_returns", "know_payee", "asked_secrecy"],
}


def predicted_scam(features: dict[str, Any]) -> str:
    """The simulator's three social-engineering scenarios (spec 03 §4), from the live signals:
    a long unknown call or screen sharing reads as digital arrest, any other call as fake
    support/refund, no call at all as an investment/task scam."""
    long_call = bool(features["ctx_unknown_call"]) and bool(features["ctx_call_long"])
    if long_call or bool(features["ctx_screen_share"]):
        return "digital_arrest"
    if bool(features["ctx_unknown_call"]) or bool(features["ctx_call_long"]):
        return "fake_support_refund"
    return "investment_task"


def questions_for(features: dict[str, Any]) -> list[str]:
    """2-3 question keys for the predicted scam; `know_payee` only for a payee never paid."""
    keys = [
        k for k in BY_SCAM[predicted_scam(features)]
        if k != "know_payee" or bool(features["payee_is_new"])
    ]  # fmt: skip
    for filler in ("asked_secrecy", "on_call_instructed"):  # F1: at least two questions
        if len(keys) >= MIN_QUESTIONS:
            break
        if filler not in keys:
            keys.append(filler)
    return keys[:MAX_QUESTIONS]


def red_flags(questions: list[str], answers: dict[str, bool]) -> int:
    return sum(answers[k] == QUESTIONS[k].red_flag for k in questions)


def key(txn_id: str) -> str:
    return f"coach:{txn_id}"


async def save_context(
    redis: Redis,
    event: dict[str, Any],
    features: dict[str, Any],
    questions: list[str],
    model_decision: Decision,
) -> None:
    payload = {
        "event": event,
        "features": {k: explain.jsonable(v) for k, v in features.items()},
        "questions": questions,
        "model_decision": model_decision.value,
    }
    await redis.set(key(event["txn_id"]), json.dumps(payload, default=str), ex=CONTEXT_TTL_S)


async def _already_answered(session: AsyncSession, txn_id: str) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(CoachSession.txn_id == Transaction.id, Transaction.txn_id == txn_id)
            )
        )
    )


async def answer(
    txn_id: str,
    answers: dict[str, bool],
    viewer: User,
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    registry: ModelRegistry,
    rules: list[ActiveRule],
    settings: Settings,
) -> CoachResponse:
    raw = await redis.get(key(txn_id))
    if raw is None:
        if await _already_answered(session, txn_id):
            raise AppError(409, "COACH_ALREADY_ANSWERED", "These questions were already answered")
        raise AppError(404, "NOT_FOUND", "No Scam Coach questions for this payment")
    ctx: dict[str, Any] = json.loads(raw)
    event: dict[str, Any] = ctx["event"]
    if event["user_id"] != str(viewer.id):
        raise AppError(404, "NOT_FOUND", "No Scam Coach questions for this payment")
    questions: list[str] = ctx["questions"]
    if set(answers) != set(questions):
        raise AppError(
            400, "VALIDATION_ERROR", "Answer exactly the questions asked",
            {"questions": questions},
        )  # fmt: skip
    if await redis.getdel(key(txn_id)) is None:  # a second tap got here first
        raise AppError(409, "COACH_ALREADY_ANSWERED", "These questions were already answered")

    try:
        return await _decide_and_save(
            event, ctx, answers, questions, session, sessionmaker, redis, registry, rules,
            settings,
        )  # fmt: skip
    except Exception:
        await redis.set(key(txn_id), raw, ex=CONTEXT_TTL_S)  # let the customer try again
        raise


async def _decide_and_save(
    event: dict[str, Any],
    ctx: dict[str, Any],
    answers: dict[str, bool],
    questions: list[str],
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    registry: ModelRegistry,
    rules: list[ActiveRule],
    settings: Settings,
) -> CoachResponse:
    flags = red_flags(questions, answers)
    features: dict[str, Any] = {**ctx["features"], "coach_red_flags": flags}
    out = registry.predict(features, coach_red_flags=flags)
    social = out.scores["social_eng_score"]
    after = decide(
        out.risk_score, registry.step_up, registry.hold, features, social, rules,
        coach_first=False,
    )  # fmt: skip
    floor = Decision.HOLD if flags else Decision.APPROVE
    decision = max(after.decision, Decision(ctx["model_decision"]), floor, key=RANK.__getitem__)
    # passed the coach: an extra check is the OTP, never the questions again
    action: Action = "NONE"
    if decision == Decision.HOLD:
        action = "ANALYST_REVIEW"
    elif decision == Decision.STEP_UP:
        action = "OTP"
    # the customer's own answers lead the reasons, then the rules
    hits = [*(["coach_red_flags"] if flags else []), *out.rule_hits, *after.rule_hits]
    stored = explain.ranked(out.contributions, features, hits, registry.boost)
    language = Language(event.get("language", Language.EN.value))
    reasons = explain.top_reasons(stored, language)
    sentence = explain.template_sentence(
        decision, action, [r.text for r in reasons], language, coach_flagged=flags > 0
    )
    risk = round(max(out.risk_score, float(event["risk_score"])), 4)  # the coach never lowers it
    now = datetime.now(UTC)

    await persist.ensure_saved(sessionmaker, event)  # the worker may not have saved it yet
    txn_pk = uuid.UUID(event["id"])
    updated = {
        **event,
        "risk_score": risk,
        "decision": decision.value,
        "action": action,
        "top_reasons": [r.model_dump() for r in reasons],
        "explanation": sentence,
        "explanation_status": "pending",
        "model_scores": {**event["model_scores"], "social_eng": round(social, 4)},
        "meta_score": out.meta_score,
        "shap_top": stored,
        "rule_hits": hits,
        "coach_red_flags": flags,
        "scored_at": now.isoformat(),
    }
    await session.execute(
        update(Transaction)
        .where(Transaction.id == txn_pk)
        .values(risk_score=risk, decision=decision, action=action)
    )
    await session.execute(
        update(ModelScore)
        .where(ModelScore.txn_id == txn_pk)
        .values(
            social_eng_score=social,
            meta_score=out.meta_score,
            shap_json={"top": stored, "rule_hits": hits},
            explanation=sentence,
            explanation_lang=language,
        )
    )
    session.add(
        CoachSession(
            txn_id=txn_pk,
            questions=[{"key": k, "text": QUESTIONS[k].text} for k in questions],
            answers=[
                {"key": k, "answer": answers[k], "red_flag": answers[k] == QUESTIONS[k].red_flag}
                for k in questions
            ],
            red_flags=flags,
            outcome=decision.value,
            completed_at=now,
        )
    )
    # Alert (HOLD) + LLM sentence by the worker; an outbox row in this transaction if Redis is down.
    await bus.publish(redis, session, settings.stream_scored, updated)
    await session.commit()
    return CoachResponse(
        txn_id=event["txn_id"],
        risk_score=risk,
        decision=decision,
        action=action,
        explanation=sentence,
        explanation_status="pending",
        top_reasons=reasons,
        red_flags=flags,
    )
