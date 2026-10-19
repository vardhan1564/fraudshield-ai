"""/audit-log (spec 04 endpoint 27): the append-only trail for admins, as JSON or CSV."""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select

from app.api.deps import SessionDep, require_role
from app.models import AuditLog, User
from app.models.enums import Role
from app.schemas.common import Page, PageNo, PageSize
from app.schemas.errors import ErrorResponse

router = APIRouter(prefix="/audit-log", tags=["audit"])
ERRORS: dict[int | str, dict[str, Any]] = {c: {"model": ErrorResponse} for c in (400, 401, 403)}
CSV_MAX_ROWS = 50_000
CSV_COLUMNS = ("id", "ts", "actor_id", "action", "entity", "entity_id", "ip", "before", "after")


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    actor_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str | None
    ip: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None


def entry(row: AuditLog) -> AuditEntry:
    return AuditEntry(
        id=row.id, ts=row.ts, actor_id=row.actor_id, action=row.action, entity=row.entity,
        entity_id=row.entity_id, ip=None if row.ip is None else str(row.ip),
        before_json=row.before_json, after_json=row.after_json,
    )  # fmt: skip


def safe_cell(value: object) -> str:
    """No spreadsheet formulas from user-controlled text (CSV injection)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("", response_model=Page[AuditEntry], responses=ERRORS)
async def audit_log(
    _admin: Annotated[User, Depends(require_role(Role.ADMIN))],
    session: SessionDep,
    entity: Annotated[str | None, Query(max_length=64)] = None,
    actor: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=64)] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    format: Literal["json", "csv"] = "json",  # noqa: A002  # the spec's parameter name
    page: PageNo = 1,
    page_size: PageSize = 50,
) -> Any:
    """Newest first. Filters: `entity` (case, rule, user, report, model_version), `actor` (user
    id), `action` (e.g. case.update), `from`/`to`. `?format=csv` downloads every match (at most
    50,000 rows) instead of one page."""
    where: list[ColumnElement[bool]] = []
    if entity:
        where.append(AuditLog.entity == entity)
    if actor is not None:
        where.append(AuditLog.actor_id == actor)
    if action:
        where.append(AuditLog.action == action)
    if date_from is not None:
        where.append(AuditLog.ts >= date_from)
    if date_to is not None:
        where.append(AuditLog.ts < date_to)
    query = select(AuditLog).where(*where).order_by(AuditLog.ts.desc(), AuditLog.id.desc())

    if format == "csv":
        rows = (await session.scalars(query.limit(CSV_MAX_ROWS))).all()

        async def lines() -> AsyncIterator[str]:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(CSV_COLUMNS)
            for r in rows:
                writer.writerow([safe_cell(v) for v in (
                    r.id, r.ts.isoformat(), r.actor_id, r.action, r.entity, r.entity_id, r.ip,
                    json.dumps(r.before_json) if r.before_json is not None else None,
                    json.dumps(r.after_json) if r.after_json is not None else None,
                )])  # fmt: skip
                if buffer.tell() > 64_000:
                    yield buffer.getvalue()
                    buffer.seek(0)
                    buffer.truncate()
            yield buffer.getvalue()

        return StreamingResponse(
            lines(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
        )

    total = await session.scalar(select(func.count()).select_from(AuditLog).where(*where)) or 0
    rows = (await session.scalars(query.offset((page - 1) * page_size).limit(page_size))).all()
    return Page(items=[entry(r) for r in rows], page=page, page_size=page_size, total=total)
