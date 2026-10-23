"""/graph (spec 04 endpoints 15-16, §2.5): data for the Graph Explorer."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import RedisDep, SessionDep, require_role
from app.models import User
from app.models.enums import RingStatus, Role
from app.schemas.common import Page, PageNo, PageSize
from app.schemas.errors import ErrorResponse
from app.schemas.graph import Neighbourhood, RingItem
from app.services import graph_views

router = APIRouter(prefix="/graph", tags=["graph"])
ERRORS: dict[int | str, dict[str, Any]] = {
    c: {"model": ErrorResponse} for c in (400, 401, 403, 404)
}
Analyst = Annotated[User, Depends(require_role(Role.ANALYST))]


@router.get("/entity/{node_id}", response_model=Neighbourhood, responses=ERRORS)
async def entity(
    _: Analyst,
    session: SessionDep,
    redis: RedisDep,
    node_id: str,
    depth: Annotated[int, Query(ge=1, le=graph_views.MAX_DEPTH)] = 2,
) -> Neighbourhood:
    """A node and everything within `depth` hops over the last 30 days (at most 200 nodes).
    `node_id` is a graph id: `A:{user_id}`, `P:{vpa}`, `D:{fingerprint}` or `I:{ip}`
    (URL-encode it). Labels are masked; `risk` and `flags` come from the last graph refresh."""
    return await graph_views.neighbourhood(session, redis, node_id, depth)


@router.get("/rings", response_model=Page[RingItem], responses=ERRORS)
async def rings(
    _: Analyst,
    session: SessionDep,
    status: RingStatus | None = None,
    detector: str | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=1)] = None,
    page: PageNo = 1,
    page_size: PageSize = 50,
) -> Page[RingItem]:
    """Suspected mule rings, highest score first."""
    return await graph_views.list_rings(session, status, detector, min_score, page, page_size)
