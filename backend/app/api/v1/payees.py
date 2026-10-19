"""/reports and /payees (spec 04 endpoints 17-18): crowd reports and payee reputation."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, status

from app.api.deps import (
    Principal,
    RedisDep,
    SessionDep,
    SettingsDep,
    require_principal,
    require_role,
)
from app.models import User
from app.models.enums import Role
from app.schemas.errors import ErrorResponse
from app.schemas.payees import PayeeReputation, ReportCreate, ReportCreated
from app.services import reputation

router = APIRouter(tags=["payees"])
ERRORS: dict[int | str, dict[str, Any]] = {
    c: {"model": ErrorResponse} for c in (400, 401, 403, 409)
}
ANY_ROLE = (Role.CUSTOMER, Role.ANALYST, Role.ADMIN, Role.PARTNER)


@router.post(
    "/reports", response_model=ReportCreated, status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
)  # fmt: skip
async def create_report(
    body: ReportCreate,
    request: Request,
    reporter: Annotated[User, Depends(require_role(Role.CUSTOMER))],
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> ReportCreated:
    """Report a scam payee. Raises its report count and reputation at once (5 reports ->
    payments to it get at least a STEP_UP). One report per customer per payee (409)."""
    ip = request.client.host if request.client else None
    return await reputation.file_report(session, redis, settings, body, reporter, ip)


@router.get("/payees/{vpa}/reputation", response_model=PayeeReputation, responses=ERRORS)
async def payee_reputation(
    vpa: Annotated[str, Path(min_length=3, max_length=255)],
    who: Annotated[Principal, Depends(require_principal(*ANY_ROLE))],
    session: SessionDep,
) -> PayeeReputation:
    """F7 score and level (low < 0.3 <= medium <= 0.7 < high), counts by scam type; analysts
    and admins also get the 10 latest reports. Unknown payees answer `known: false`."""
    return await reputation.lookup(session, vpa, reputation.is_staff(who.role))
