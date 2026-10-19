"""Realtime push over Redis pub/sub (spec 02 §6). The API's WebSocket endpoints (T3.7)
subscribe to these channels and forward each message as-is, so this works with any number of
API workers. Message shape: `{"type": ..., "data": {...}}` (spec 04 §3)."""

import json
import uuid
from typing import Any

from redis.asyncio import Redis

ANALYSTS = "ws:analysts"


def user_channel(user_id: uuid.UUID | str) -> str:
    return f"ws:user:{user_id}"


async def push(redis: Redis, channel: str, kind: str, data: dict[str, Any]) -> int:
    """Publish one message; returns how many subscribers got it (0 is fine: nobody online)."""
    count: int = await redis.publish(channel, json.dumps({"type": kind, "data": data}, default=str))
    return count


async def to_analysts(redis: Redis, kind: str, data: dict[str, Any]) -> int:
    return await push(redis, ANALYSTS, kind, data)


async def to_user(redis: Redis, user_id: uuid.UUID | str, kind: str, data: dict[str, Any]) -> int:
    return await push(redis, user_channel(user_id), kind, data)
