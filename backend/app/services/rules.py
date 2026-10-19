"""Admin rules (rules table, spec 05) cached in memory: the scoring path never queries for them.
Loaded at startup; the rules API (T3.8) reloads the cache after every change."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Rule
from app.models.enums import Decision
from app.services.decision import ActiveRule


async def load_active_rules(session: AsyncSession) -> list[ActiveRule]:
    rows = await session.scalars(select(Rule).where(Rule.is_active).order_by(Rule.priority))
    return [
        ActiveRule(
            name=r.name,
            condition=r.condition_json,
            action=Decision(r.action.value),
            priority=r.priority,
        )  # fmt: skip
        for r in rows
    ]
