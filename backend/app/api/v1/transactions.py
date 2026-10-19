"""/transactions (spec 04): real-time scoring and explanations."""

from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request

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
from app.models.enums import Language, Role
from app.schemas.errors import ErrorResponse
from app.schemas.transactions import ExplainResponse, ScoreRequest, ScoreResponse
from app.services import explain, scoring
from app.services.decision import ActiveRule

router = APIRouter(prefix="/transactions", tags=["transactions"])
ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 503)
}


def get_registry(request: Request) -> ModelRegistry | None:
    registry: ModelRegistry | None = request.app.state.registry
    return registry


def get_rules(request: Request) -> list[ActiveRule]:
    rules: list[ActiveRule] = request.app.state.rules
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
    rules: Annotated[list[ActiveRule], Depends(get_rules)],
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
