"""Rules CRUD for admins (spec 04 endpoint 23). Each change is audited, bumps rules:version so
every API process reloads, and refreshes this process's cache straight away."""

import uuid
from typing import Any

from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Rule, User
from app.schemas.common import Page
from app.schemas.rules import RuleIn, RuleOut
from app.services import audit
from app.services.rules import RuleCache, bump


def view(rule: Rule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "condition_json": rule.condition_json,
        "action": rule.action.value,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }


def out(rule: Rule) -> RuleOut:
    return RuleOut(id=rule.id, updated_by=rule.updated_by, created_at=rule.created_at,
                   **view(rule))  # fmt: skip


async def list_rules(
    session: AsyncSession, is_active: bool | None, page: int, page_size: int
) -> Page[RuleOut]:
    where = [] if is_active is None else [Rule.is_active == is_active]
    total = await session.scalar(select(func.count()).select_from(Rule).where(*where)) or 0
    rows = await session.scalars(
        select(Rule).where(*where).order_by(Rule.priority, Rule.name)
        .offset((page - 1) * page_size).limit(page_size)
    )  # fmt: skip
    return Page(items=[out(r) for r in rows], page=page, page_size=page_size, total=total)


async def _get(session: AsyncSession, rule_id: uuid.UUID) -> Rule:
    rule = await session.get(Rule, rule_id, with_for_update=True)
    if rule is None:
        raise AppError(404, "NOT_FOUND", "Rule not found")
    return rule


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(409, "RULE_NAME_TAKEN", "A rule with this name already exists") from exc


async def _publish(app: FastAPI, session: AsyncSession, redis: Redis) -> None:
    """Other processes reload on their next payment; this one reloads now."""
    cache: RuleCache = app.state.rules
    try:
        version: str | None = await bump(redis)
    except RedisError:  # others keep their rules until Redis is back and the next change
        version = cache.version
    await cache.refresh(session, version)


async def create_rule(
    app: FastAPI, session: AsyncSession, redis: Redis, body: RuleIn, admin: User, ip: str | None
) -> RuleOut:
    rule = Rule(**body.model_dump(), updated_by=admin.id)
    session.add(rule)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(409, "RULE_NAME_TAKEN", "A rule with this name already exists") from exc
    audit.record(session, actor_id=admin.id, action="rule.create", entity="rule",
                 entity_id=rule.id, after=view(rule), ip=ip)  # fmt: skip
    await _commit(session)
    await _publish(app, session, redis)
    return out(rule)


async def update_rule(
    app: FastAPI,
    session: AsyncSession,
    redis: Redis,
    rule_id: uuid.UUID,
    body: RuleIn,
    admin: User,
    ip: str | None,
) -> RuleOut:
    rule = await _get(session, rule_id)
    before = view(rule)
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    rule.updated_by = admin.id
    audit.record(session, actor_id=admin.id, action="rule.update", entity="rule",
                 entity_id=rule.id, before=before, after=view(rule), ip=ip)  # fmt: skip
    await _commit(session)
    await _publish(app, session, redis)
    return out(rule)


async def delete_rule(
    app: FastAPI, session: AsyncSession, redis: Redis, rule_id: uuid.UUID, admin: User,
    ip: str | None,
) -> None:  # fmt: skip
    rule = await _get(session, rule_id)
    audit.record(session, actor_id=admin.id, action="rule.delete", entity="rule",
                 entity_id=rule.id, before=view(rule), ip=ip)  # fmt: skip
    await session.delete(rule)
    await session.commit()
    await _publish(app, session, redis)
