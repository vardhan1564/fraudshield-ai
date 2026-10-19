"""/cases (spec 04 endpoints 13-14): case detail and the analyst's decision."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import RedisDep, SessionDep, SettingsDep, require_role
from app.models import User
from app.models.enums import Role
from app.schemas.cases import CaseDetail, CaseUpdate
from app.schemas.errors import ErrorResponse
from app.services import cases

router = APIRouter(prefix="/cases", tags=["cases"])
ERRORS: dict[int | str, dict[str, Any]] = {
    c: {"model": ErrorResponse} for c in (400, 401, 403, 404)
}
Analyst = Annotated[User, Depends(require_role(Role.ANALYST))]


@router.get("/{case_id}", response_model=CaseDetail, responses=ERRORS)
async def get_case(case_id: uuid.UUID, analyst: Analyst, session: SessionDep) -> CaseDetail:
    """The case with its alert, payment, customer (masked phone), reasons and explanation."""
    return await cases.get_case(session, case_id, analyst.language)


@router.patch("/{case_id}", response_model=CaseDetail, responses=ERRORS)
async def update_case(
    case_id: uuid.UUID,
    body: CaseUpdate,
    request: Request,
    analyst: Analyst,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> CaseDetail:
    """Assign, change status, label (fraud / legit) and add notes. Every change is audited;
    a new label is published to `stream:cases.labeled`. Closing needs a label (400)."""
    ip = request.client.host if request.client else None
    return await cases.update_case(session, redis, settings, case_id, body, analyst, ip)
