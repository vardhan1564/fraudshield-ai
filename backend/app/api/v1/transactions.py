"""/transactions (spec 04 endpoints 5-9): scoring, Scam Coach answers, the analyst list, detail,
explanation."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from app.api.deps import (
    Principal,
    RedisDep,
    SessionDep,
    SettingsDep,
    require_principal,
    require_role,
)
from app.ml_runtime.registry import ModelRegistry
from app.models import User
from app.models.enums import Channel, Decision, Language, Role
from app.schemas.common import Page, PageNo, PageSize
from app.schemas.errors import ErrorResponse
from app.schemas.transactions import (
    CoachRequest,
    CoachResponse,
    ExplainResponse,
    ScoreRequest,
    ScoreResponse,
)
from app.schemas.txn_views import TxnDetail, TxnItem
from app.services import explain, scam_coach, scoring, txn_views
from app.services.rules import RuleCache

router = APIRouter(prefix="/transactions", tags=["transactions"])
ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 503)
}


def get_registry(request: Request) -> ModelRegistry | None:
    registry: ModelRegistry | None = request.app.state.registry
    return registry


def get_rules(request: Request) -> RuleCache:
    rules: RuleCache = request.app.state.rules
    return rules


@router.post("/score", response_model=ScoreResponse, responses=ERRORS)
async def score(
    body: ScoreRequest,
    request: Request,
    background: BackgroundTasks,
    who: Annotated[Principal, Depends(require_principal(Role.PARTNER, Role.CUSTOMER))],
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    registry: Annotated[ModelRegistry | None, Depends(get_registry)],
    rules: Annotated[RuleCache, Depends(get_rules)],
) -> ScoreResponse:
    """Score one payment in real time: risk_score, decision, action and top_reasons.

    Partners call with X-API-Key; customers with their own token and their own user_id.
    The transaction row, Redis feature state and the scored event are written after the
    response is sent. 409 DUPLICATE_TXN for a repeated txn_id, 503 MODEL_UNAVAILABLE when the
    models are not loaded.
    """
    response, user, scored = await scoring.score(body, who, session, redis, registry, rules)
    background.add_task(
        scoring.after_response, body, response, user, scored,
        request.app.state.sessionmaker, redis, settings,
    )  # fmt: skip
    return response


@router.post("/{txn_id}/coach", response_model=CoachResponse, responses=ERRORS)
async def coach(
    txn_id: str,
    body: CoachRequest,
    request: Request,
    customer: Annotated[User, Depends(require_role(Role.CUSTOMER))],
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    registry: Annotated[ModelRegistry | None, Depends(get_registry)],
    rules: Annotated[RuleCache, Depends(get_rules)],
) -> CoachResponse:
    """Scam Coach answers (spec 01 F1) for the customer's own payment, by its client `txn_id`.

    Re-scores with the red-flag count and decides again: any red-flag answer means HOLD (and an
    analyst alert); the decision is never lower than the models' first one. 400 unless every
    question asked is answered (and nothing else); 404 when there are no open questions for this
    customer's payment (or after an hour); 409 COACH_ALREADY_ANSWERED on a second answer.
    """
    if registry is None:
        raise scoring.model_unavailable()
    return await scam_coach.answer(
        txn_id, body.answers, customer, session, request.app.state.sessionmaker, redis,
        registry, rules.rules, settings,
    )  # fmt: skip


@router.get("/{txn_ref}/explain", response_model=ExplainResponse, responses=ERRORS)
async def explanation(
    txn_ref: str,
    viewer: Annotated[User, Depends(require_role(Role.ANALYST, Role.ADMIN, Role.CUSTOMER))],
    session: SessionDep,
    lang: Language | None = None,
) -> ExplainResponse:
    """Top 10 SHAP values with plain-language text, and the explanation sentence.

    `txn_ref` is the client txn_id or the transaction UUID. Customers see only their own
    payments (404 otherwise). `lang` defaults to the language of the stored sentence.
    """
    return await explain.explanation_for(txn_ref, viewer, session, lang)


@router.get("", response_model=Page[TxnItem], responses=ERRORS)
async def list_transactions(
    viewer: Annotated[User, Depends(require_role(Role.ANALYST, Role.ADMIN, Role.CUSTOMER))],
    session: SessionDep,
    mine: bool = False,
    decision: Decision | None = None,
    channel: Channel | None = None,
    user_id: Annotated[str | None, Query(max_length=64)] = None,
    payee_vpa: Annotated[str | None, Query(max_length=255)] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    min_risk: Annotated[float | None, Query(ge=0, le=1)] = None,
    page: PageNo = 1,
    page_size: PageSize = 50,
) -> Page[TxnItem]:
    """Scored payments, newest first. `from` inclusive, `to` exclusive (ISO-8601);
    `user_id` is the customer's external id or UUID. `mine=true` = only the caller's payments;
    customers always get only their own (their Home, T4.5), whatever `user_id` says."""
    if mine or viewer.role == Role.CUSTOMER:
        user_id = str(viewer.id)
    return await txn_views.list_transactions(
        session, decision=decision, channel=channel, user_ref=user_id, payee_vpa=payee_vpa,
        date_from=date_from, date_to=date_to, min_risk=min_risk, page=page, page_size=page_size,
    )  # fmt: skip


@router.get("/{txn_ref}", response_model=TxnDetail, responses=ERRORS)
async def get_transaction(
    txn_ref: str,
    viewer: Annotated[User, Depends(require_role(Role.ANALYST, Role.ADMIN, Role.CUSTOMER))],
    session: SessionDep,
) -> TxnDetail:
    """One payment by txn_id or UUID: scores, reasons (viewer's language), explanation,
    context and device. Customers see only their own payments (404 otherwise)."""
    return await txn_views.get_transaction(session, txn_ref, viewer)
