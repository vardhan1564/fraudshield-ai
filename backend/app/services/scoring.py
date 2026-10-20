"""POST /transactions/score: the synchronous scoring path (spec 02 §3, budget < 100 ms p95).

In the request: duplicate check -> online features (one Redis round trip) -> models (worker
thread) -> decision -> SHAP reasons + template sentence in the customer's language -> response
(explanation_status "pending": the worker's LLM sentence follows over WebSocket). After the
response (background task): update the Redis feature state and publish to
stream:transactions.scored (outbox row if Redis is down). The worker saves the transaction and
model_scores rows from that event (services/persist.py), keeping the API's CPU for scoring (T3.9).
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import Principal
from app.core.config import Settings
from app.core.errors import AppError
from app.ml_runtime.registry import ModelOutput, ModelRegistry
from app.models import User
from app.models.enums import Decision, Role
from app.schemas.transactions import CoachQuestion, ModelScores, ScoreRequest, ScoreResponse
from app.services import explain
from app.services.decision import QUESTIONS, decide
from app.services.features import PaymentIn, build_features, record
from app.services.rules import VERSION_KEY, RuleCache
from app.streaming import bus

logger = logging.getLogger(__name__)

SEEN_TTL_S = 7 * 86_400  # duplicate txn_id window (409 DUPLICATE_TXN)


def model_unavailable() -> AppError:
    return AppError(503, "MODEL_UNAVAILABLE", "Scoring models are not loaded")


# Per-process cache for the hot path (T3.9): every payment used to look the customer up.
USER_TTL_S = 60.0  # a role or language change shows within a minute
CACHE_MAX = 100_000
_users: dict[str, tuple[float, User]] = {}


def _remember(cache: dict[Any, Any], key: Any, value: Any) -> None:
    if len(cache) >= CACHE_MAX:  # simple bound; refills from Postgres
        cache.clear()
    cache[key] = value


async def resolve_user(user_id: str, principal: Principal, session: AsyncSession) -> User:
    """The customer being scored. Customers may only score their own payments."""
    if principal.role == Role.CUSTOMER:
        me = principal.user
        if me is None or user_id not in (me.external_id, str(me.id)):
            raise AppError(403, "FORBIDDEN", "You can only score your own payments")
        return me
    hit = _users.get(user_id)
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    user = await session.scalar(select(User).where(User.external_id == user_id))
    if user is None:
        try:
            user = await session.get(User, uuid.UUID(user_id))
        except ValueError:
            user = None
    if user is None or user.role != Role.CUSTOMER:
        raise AppError(404, "NOT_FOUND", "Unknown user_id")
    session.expunge(user)  # kept loaded and detached in the cache
    _remember(_users, user_id, (time.monotonic() + USER_TTL_S, user))
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


@dataclass(frozen=True)
class Scored:
    """What the background task needs besides the response."""

    out: ModelOutput
    shap_top: list[dict[str, Any]]  # explain.ranked(): stored in model_scores.shap_json


async def score(
    body: ScoreRequest,
    principal: Principal,
    session: AsyncSession,
    redis: Redis,
    registry: ModelRegistry | None,
    rules: RuleCache,
) -> tuple[ScoreResponse, User, Scored]:
    started = time.perf_counter()
    if registry is None:
        raise model_unavailable()
    user = await resolve_user(body.user_id, principal, session)
    # Give the pooled DB connection back now, not after the models: holding it for the whole
    # request exhausted the pool under load (T3.9). The user keeps its loaded fields.
    if user in session:
        session.expunge(user)
    await session.close()

    async with redis.pipeline(transaction=False) as pipe:
        pipe.set(f"seen:{body.txn_id}", "1", nx=True, ex=SEEN_TTL_S)
        pipe.exists(f"graph:{body.user_id}")
        pipe.get(VERSION_KEY)
        first_time, has_graph, rules_version = await pipe.execute()
    if not first_time:
        raise AppError(409, "DUPLICATE_TXN", "This txn_id was already scored")
    if rules_version != rules.version:  # an admin changed the rules (rare): reload once
        await rules.refresh(session, rules_version)
        await session.close()
    try:
        payment = to_payment(body)
        features = await build_features(payment, redis)
        # Inline, not in a worker thread (T3.9): predict is ~6 ms of CPU, and handing it to a
        # thread made every GIL release inside XGBoost/LightGBM/PyTorch wait up to the 5 ms
        # switch interval behind the event loop: ~400 ms p50 at 50 rps instead of tens of ms.
        out = registry.predict(features)
    except Exception:
        await redis.delete(f"seen:{body.txn_id}")  # let the client retry the same txn_id
        raise
    outcome = decide(
        out.risk_score, registry.step_up, registry.hold, features,
        out.scores["social_eng_score"], rules.rules,
    )  # fmt: skip
    stored = explain.ranked(
        out.contributions, features, [*out.rule_hits, *outcome.rule_hits], registry.boost
    )
    reasons = explain.top_reasons(stored, user.language)
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
        explanation=explain.template_sentence(
            outcome.decision, outcome.action, [r.text for r in reasons], user.language
        ),
        explanation_status="ready" if outcome.decision == Decision.APPROVE else "pending",
        coach_questions=[
            CoachQuestion(key=k, text=QUESTIONS[k][0]) for k in outcome.coach_questions
        ],
        model_version=registry.version,
        degraded=[] if has_graph else ["graph"],  # graph scores arrive with phase 5
        latency_ms=0,
    )
    response.latency_ms = int((time.perf_counter() - started) * 1000)
    return response, user, Scored(out, stored)


async def after_response(
    body: ScoreRequest,
    response: ScoreResponse,
    user: User,
    scored: Scored,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    settings: Settings,
) -> None:
    """Background task: Redis feature state + the scored event. Never raises. The Postgres rows
    are written by the worker from this event (services/persist.py, T3.9)."""
    payment = to_payment(body)
    status = "SUCCESS" if response.decision == Decision.APPROVE else "HELD"
    try:
        await record(payment, status, redis)
    except RedisError:
        logger.exception("feature state update failed", extra={"txn_id": body.txn_id})
    event = scored_event(body, response, user, scored)
    try:
        await redis.xadd(
            settings.stream_scored, bus.encode(event), maxlen=bus.STREAM_MAXLEN, approximate=True
        )
    except RedisError:  # the worker replays outbox rows when Redis is back
        try:
            async with sessionmaker() as session:
                await bus.publish(redis, session, settings.stream_scored, event)
                await session.commit()
        except Exception:
            logger.exception("scored event lost", extra={"txn_id": body.txn_id})


def scored_event(
    body: ScoreRequest, response: ScoreResponse, user: User, scored: Scored
) -> dict[str, Any]:
    """Everything the worker needs to save the payment and act on it (stays inside Redis/the
    outbox, never logged)."""
    scores = response.model_scores
    return {
        "id": str(uuid.uuid4()),  # the transactions.id the worker will use
        "txn_id": response.txn_id,
        "user_id": str(user.id),
        "language": user.language.value,
        "risk_score": response.risk_score,
        "decision": response.decision.value,
        "action": response.action,
        "top_reasons": [r.model_dump() for r in response.top_reasons],
        "explanation": response.explanation,
        "explanation_status": response.explanation_status,
        "model_version": response.model_version,
        "latency_ms": response.latency_ms,
        "model_scores": scores.model_dump(),
        "meta_score": scored.out.meta_score,
        "shap_top": scored.shap_top,
        "rule_hits": scored.out.rule_hits,
        "account_id": body.user_id,
        "payee_vpa": body.payee_vpa.lower(),
        "device_id": body.device_id,
        "ip": None if body.ip is None else str(body.ip),
        "geo_lat": None if body.geo is None else body.geo.lat,
        "geo_lon": None if body.geo is None else body.geo.lon,
        "amount": body.amount,
        "currency": body.currency,
        "channel": body.channel.value,
        "merchant_category": body.merchant_category,
        "context": {**body.context.model_dump(), "behaviour": body.behaviour.model_dump()},
        "ts": body.timestamp.isoformat(),
        "scored_at": datetime.now(UTC).isoformat(),
    }
