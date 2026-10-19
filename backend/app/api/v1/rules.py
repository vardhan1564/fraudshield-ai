"""/rules (spec 04 endpoint 23): admin rules CRUD."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import RedisDep, SessionDep, require_role
from app.models import User
from app.models.enums import Role
from app.schemas.common import Page, PageNo, PageSize
from app.schemas.errors import ErrorResponse
from app.schemas.rules import RuleIn, RuleOut
from app.services import rules_admin

router = APIRouter(prefix="/rules", tags=["rules"])
ERRORS: dict[int | str, dict[str, Any]] = {
    c: {"model": ErrorResponse} for c in (400, 401, 403, 404, 409)
}
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=Page[RuleOut], responses=ERRORS)
async def list_rules(
    _admin: Admin,
    session: SessionDep,
    is_active: bool | None = None,
    page: PageNo = 1,
    page_size: PageSize = 50,
) -> Page[RuleOut]:
    """All rules (or only active / inactive), lowest priority number first: it runs first."""
    return await rules_admin.list_rules(session, is_active, page, page_size)


@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_rule(
    body: RuleIn, request: Request, admin: Admin, session: SessionDep, redis: RedisDep
) -> RuleOut:
    """Conditions use spec 05's format with real feature names, a known op and a value of the
    feature's type (400 otherwise). Names are unique (409 RULE_NAME_TAKEN). Takes effect on the
    next payment in every API process."""
    return await rules_admin.create_rule(
        request.app, session, redis, body, admin, client_ip(request)
    )


@router.put("/{rule_id}", response_model=RuleOut, responses=ERRORS)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleIn,
    request: Request,
    admin: Admin,
    session: SessionDep,
    redis: RedisDep,
) -> RuleOut:
    """Replace a rule (set `is_active: false` to pause it)."""
    return await rules_admin.update_rule(
        request.app, session, redis, rule_id, body, admin, client_ip(request)
    )


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def delete_rule(
    rule_id: uuid.UUID, request: Request, admin: Admin, session: SessionDep, redis: RedisDep
) -> Response:
    """Delete a rule; the audit log keeps what it was."""
    await rules_admin.delete_rule(request.app, session, redis, rule_id, admin, client_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
