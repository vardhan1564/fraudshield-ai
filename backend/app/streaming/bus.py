"""Publishing events to Redis Streams (spec 02 §4), with the Postgres outbox as fallback (§7).

Every event is one stream entry with a single field, `data`, holding the JSON payload.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.typing import EncodableT, FieldT
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Outbox

logger = logging.getLogger(__name__)

STREAM_MAXLEN = 100_000  # approximate cap per stream
DEAD_LETTER = "stream:dead"
REPLAY_BATCH = 500


def encode(payload: dict[str, Any]) -> dict[FieldT, EncodableT]:
    return {"data": json.dumps(payload, default=str)}


def decode(fields: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(fields["data"])
    return payload


async def publish(
    redis: Redis, session: AsyncSession, stream: str, payload: dict[str, Any]
) -> bool:
    """XADD, or an outbox row in `session` when Redis is down (the caller commits).
    True when the event reached Redis."""
    try:
        await redis.xadd(stream, encode(payload), maxlen=STREAM_MAXLEN, approximate=True)
        return True
    except RedisError:
        logger.warning("redis down, event kept in outbox", extra={"stream": stream})
        session.add(Outbox(stream=stream, payload=payload))
        return False


async def replay_outbox(redis: Redis, sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Send unsent outbox rows in order; stops at the first Redis error. Returns rows sent."""
    sent = 0
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(Outbox)
            .where(Outbox.sent_at.is_(None))
            .order_by(Outbox.id)
            .limit(REPLAY_BATCH)
            .with_for_update(skip_locked=True)  # two workers never send the same row
        )
        for row in rows:
            try:
                await redis.xadd(
                    row.stream, encode(row.payload), maxlen=STREAM_MAXLEN, approximate=True
                )
            except RedisError:
                break
            row.sent_at = datetime.now(UTC)
            sent += 1
        await session.commit()
    if sent:
        logger.info("outbox replayed", extra={"rows": sent})
    return sent
