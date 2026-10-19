"""POST /transactions/score: the synchronous scoring path (spec 02 §3, budget < 100 ms p95).

In the request: duplicate check -> online features (one Redis round trip) -> models (worker
thread) -> decision -> response. After the response (background task): update the Redis
feature state, save transaction + model_scores to Postgres, publish to
stream:transactions.scored (outbox row if Redis is down).
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.ml_runtime.registry import ModelOutput, ModelRegistry
from app.models import Device, ModelScore, Outbox, Payee, Transaction, User
from app.models.enums import Decision, Role
from app.schemas.transactions import (
    CoachQuestion,
    ModelScores,
    Reason,
    ScoreRequest,
    ScoreResponse,
)
from app.services.decision import QUESTIONS, ActiveRule, Outcome, decide
from app.services.features import PaymentIn, build_features, record

logger = logging.getLogger(__name__)

SEEN_TTL_S = 7 * 86_400  # duplicate txn_id window (409 DUPLICATE_TXN)
TOP_REASONS = 3
STREAM_MAXLEN = 100_000


def model_unavailable() -> AppError:
    return AppError(503, "MODEL_UNAVAILABLE", "Scoring models are not loaded")


async def resolve_user(user_id: str, principal: Principal, session: AsyncSession) -> User:
    """The customer being scored. Customers may only score their own payments."""
    if principal.role == Role.CUSTOMER:
        me = principal.user
        if me is None or user_id not in (me.external_id, str(me.id)):
            raise AppError(403, "FORBIDDEN", "You can only score your own payments")
        return me
    user = await session.scalar(select(User).where(User.external_id == user_id))
    if user is None:
        try:
            user = await session.get(User, uuid.UUID(user_id))
        except ValueError:
            user = None
    if user is None or user.role != Role.CUSTOMER:
        raise AppError(404, "NOT_FOUND", "Unknown user_id")
    return user


def to_payment(body: ScoreRequest) -> PaymentIn:
    ctx, beh = body.context, body.behaviour
    return PaymentIn(
        txn_id=body.txn_id,
        user_id=body.user_id,
        payee_vpa=body.payee_vpa.lower(),
        amount=body.amount,
        channel=body.channel.value,
        merchant_category=body.merchant_category,
        ts=body.timestamp,
        device_id=body.device_id,
        geo_lat=None if body.geo is None else body.geo.lat,
        geo_lon=None if body.geo is None else body.geo.lon,
        is_vpn=ctx.is_vpn,
        on_call=ctx.on_call,
        caller_in_contacts=ctx.caller_in_contacts,
        call_duration_s=ctx.call_duration_s,
        screen_share_active=ctx.screen_share_active,
        remote_access_app=ctx.remote_access_app,
        typing_speed_z=beh.typing_speed_z,
        session_len_s=beh.session_len_s,
        login_to_pay_s=beh.login_to_pay_s,
    )


def top_reasons(out: ModelOutput, features: dict[str, Any], boost: float) -> list[Reason]:
    """Features pushing towards fraud, largest first (txn_model TreeSHAP, log-odds), plus the
    F2 rule when it fired. T3.5 replaces the text with plain-language phrases."""
    reasons: list[Reason] = []
    if out.rule_hits:
        reasons.append(Reason(feature="ctx_unknown_call", value=True, impact=round(boost, 3),
                              text="You are on a call with an unknown number"))  # fmt: skip
    positive = sorted(
        ((f, v) for f, v in out.contributions.items() if v > 0 and f != "ctx_unknown_call"),
        key=lambda fv: -fv[1],
    )
    for name, impact in positive[: TOP_REASONS - len(reasons)]:
        value = features[name]
        reasons.append(Reason(feature=name, value=value, impact=round(impact, 3),
                              text=f"{name.replace('_', ' ')}: {value}"))  # fmt: skip
    return reasons


def template_sentence(outcome: Outcome, reasons: list[Reason]) -> str:
    """Instant explanation; the worker's LLM sentence replaces it later (T3.5/T3.6)."""
    first = reasons[0].text.lower() if reasons else "unusual activity"
    if outcome.decision == Decision.HOLD:
        return f"We paused this payment because it looks risky ({first}). An analyst will check it."
    if outcome.action == "SCAM_COACH":
        return f"Before you pay, please answer a few quick questions ({first})."
    if outcome.decision == Decision.STEP_UP:
        return "Please confirm this payment with the OTP we sent you."
    return "This payment looks normal."


async def score(
    body: ScoreRequest,
    principal: Principal,
    session: AsyncSession,
    redis: Redis,
    registry: ModelRegistry | None,
    rules: list[ActiveRule],
) -> tuple[ScoreResponse, User, ModelOutput]:
    started = time.perf_counter()
    if registry is None:
        raise model_unavailable()
    user = await resolve_user(body.user_id, principal, session)

    async with redis.pipeline(transaction=False) as pipe:
        pipe.set(f"seen:{body.txn_id}", "1", nx=True, ex=SEEN_TTL_S)
        pipe.exists(f"graph:{body.user_id}")
        first_time, has_graph = await pipe.execute()
    if not first_time:
        raise AppError(409, "DUPLICATE_TXN", "This txn_id was already scored")
    try:
        payment = to_payment(body)
        features = await build_features(payment, redis)
        out = await asyncio.to_thread(registry.predict, features)
    except Exception:
        await redis.delete(f"seen:{body.txn_id}")  # let the client retry the same txn_id
        raise
    outcome = decide(
        out.risk_score, registry.step_up, registry.hold, features,
        out.scores["social_eng_score"], rules,
    )  # fmt: skip
    reasons = top_reasons(out, features, registry.boost)
    response = ScoreResponse(
        txn_id=body.txn_id,
        risk_score=round(out.risk_score, 4),
        decision=outcome.decision,
        action=outcome.action,
        model_scores=ModelScores(
            transaction=round(out.scores["txn_score"], 4),
            anomaly=round(out.scores["anomaly_score"], 4),
            graph=round(out.scores["graph_score"], 4),
            social_eng=round(out.scores["social_eng_score"], 4),
        ),
        top_reasons=reasons,
        explanation=template_sentence(outcome, reasons),
        explanation_status="ready" if outcome.decision == Decision.APPROVE else "pending",
        coach_questions=[
            CoachQuestion(key=k, text=QUESTIONS[k][0]) for k in outcome.coach_questions
        ],
        model_version=registry.version,
        degraded=[] if has_graph else ["graph"],  # graph scores arrive with phase 5
        latency_ms=0,
    )
    response.latency_ms = int((time.perf_counter() - started) * 1000)
    return response, user, out


async def after_response(
    body: ScoreRequest,
    response: ScoreResponse,
    user_id: uuid.UUID,
    out: ModelOutput,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
) -> None:
    """Background task: feature state, Postgres rows, stream event. Never raises."""
    payment = to_payment(body)
    status = "SUCCESS" if response.decision == Decision.APPROVE else "HELD"
    try:
        await record(payment, status, redis)
    except RedisError:
        logger.exception("feature state update failed", extra={"txn_id": body.txn_id})
    event = {
        "txn_id": response.txn_id,
        "user_id": str(user_id),
        "risk_score": response.risk_score,
        "decision": response.decision.value,
        "action": response.action,
        "top_reasons": [r.model_dump() for r in response.top_reasons],
        "explanation": response.explanation,
        "model_version": response.model_version,
        "scored_at": datetime.now(UTC).isoformat(),
    }
    try:
        async with sessionmaker() as session:
            txn_uuid = await _save(session, body, response, user_id, out)
            event["id"] = str(txn_uuid)
            try:
                await redis.xadd(
                    settings.stream_scored,
                    {"data": json.dumps(event)},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )
            except RedisError:  # the worker replays outbox rows when Redis is back
                session.add(Outbox(stream=settings.stream_scored, payload=event))
            await session.commit()
    except Exception:
        logger.exception("saving scored transaction failed", extra={"txn_id": body.txn_id})


async def _save(
    session: AsyncSession,
    body: ScoreRequest,
    response: ScoreResponse,
    user_id: uuid.UUID,
    out: ModelOutput,
) -> uuid.UUID:
    vpa = body.payee_vpa.lower()
    payee = await session.scalar(select(Payee).where(Payee.vpa_or_account == vpa))
    if payee is None:
        payee = Payee(vpa_or_account=vpa)
        session.add(payee)
        await session.flush()
    device_pk = None
    if body.device_id:
        device = await session.scalar(
            select(Device).where(Device.user_id == user_id, Device.fingerprint == body.device_id)
        )
        if device is None:
            device = Device(user_id=user_id, fingerprint=body.device_id,
                            first_seen=body.timestamp, last_seen=body.timestamp)  # fmt: skip
            session.add(device)
            await session.flush()
        else:
            device.last_seen = max(device.last_seen, body.timestamp)
        device_pk = device.id
    ctx = body.context.model_dump()
    txn = Transaction(
        txn_id=body.txn_id,
        user_id=user_id,
        payee_id=payee.id,
        device_id=device_pk,
        amount=Decimal(str(body.amount)),
        currency=body.currency,
        channel=body.channel,
        merchant_category=body.merchant_category,
        ip=None if body.ip is None else str(body.ip),
        geo_lat=None if body.geo is None else body.geo.lat,
        geo_lon=None if body.geo is None else body.geo.lon,
        context_json={**ctx, "behaviour": body.behaviour.model_dump()},
        ts=body.timestamp,
        risk_score=response.risk_score,
        decision=response.decision,
        action=response.action,
        model_version=response.model_version,
        latency_ms=response.latency_ms,
    )
    session.add(txn)
    await session.flush()
    scores = response.model_scores
    session.add(
        ModelScore(
            txn_id=txn.id,
            transaction_score=scores.transaction,
            anomaly_score=scores.anomaly,
            graph_score=scores.graph,
            social_eng_score=scores.social_eng,
            meta_score=out.meta_score,  # before the F2 rule; risk_score is on the txn
            shap_json={
                "txn_model": out.contributions,
                "rule_hits": out.rule_hits,
                "top_reasons": [r.model_dump() for r in response.top_reasons],
            },
            explanation=response.explanation,
        )
    )
    return txn.id
