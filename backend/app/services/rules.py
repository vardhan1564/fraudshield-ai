"""Admin rules (rules table, spec 05) cached in memory: the scoring path never queries for them.

Every rules API change bumps the Redis counter `rules:version`. The scoring path reads it in the
Redis round trip it makes anyway and reloads the cache when it moved, so all API processes pick
up a change on their next payment.
"""

from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Rule
from app.models.enums import Decision
from app.services.decision import ActiveRule

VERSION_KEY = "rules:version"


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


@dataclass
class RuleCache:
    rules: list[ActiveRule] = field(default_factory=list)
    version: str | None = None  # the rules:version these rules were loaded at

    async def refresh(self, session: AsyncSession, version: str | None) -> None:
        self.rules = await load_active_rules(session)
        self.version = version


async def bump(redis: Redis) -> str:
    """Tell every API process the rules changed."""
    return str(await redis.incr(VERSION_KEY))
