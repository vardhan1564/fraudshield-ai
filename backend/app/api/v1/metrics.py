"""/metrics/overview (spec 04 endpoint 26): dashboard KPIs and charts."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep, require_role
from app.models import User
from app.models.enums import Role
from app.schemas.errors import ErrorResponse
from app.schemas.metrics import MetricsOverview
from app.services import metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])
ERRORS: dict[int | str, dict[str, Any]] = {c: {"model": ErrorResponse} for c in (400, 401, 403)}


@router.get("/overview", response_model=MetricsOverview, responses=ERRORS)
async def overview(
    _staff: Annotated[User, Depends(require_role(Role.ANALYST, Role.ADMIN))],
    session: SessionDep,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
) -> MetricsOverview:
    """KPIs for `from` (inclusive) to `to` (exclusive); default the last 24 hours, at most 92
    days. Definitions of each number are in the service docstring and spec 04 §2.8."""
    return await metrics.overview(session, date_from, date_to, datetime.now(UTC))
