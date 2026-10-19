"""/alerts (spec 04 endpoint 11): the analyst's alert queue."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep, require_role
from app.models import User
from app.models.enums import AlertStatus, Role
from app.schemas.cases import AlertItem, Page
from app.schemas.errors import ErrorResponse
from app.services import cases

router = APIRouter(prefix="/alerts", tags=["alerts"])
ERRORS: dict[int | str, dict[str, Any]] = {c: {"model": ErrorResponse} for c in (400, 401, 403)}


@router.get("", response_model=Page[AlertItem], responses=ERRORS)
async def list_alerts(
    analyst: Annotated[User, Depends(require_role(Role.ANALYST))],
    session: SessionDep,
    status: AlertStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AlertItem]:
    """Open and assigned alerts (or one `status`), most severe first, then oldest first.
    Reasons are in the analyst's language. New alerts also arrive on `/ws/alerts`."""
    return await cases.list_alerts(session, analyst.language, status, page, page_size)
