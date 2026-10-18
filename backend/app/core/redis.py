"""Shared async Redis client (live features, rate limits, refresh tokens, streams)."""

from redis.asyncio import Redis

from app.core.config import Settings


def make_redis(settings: Settings) -> Redis:
    # Connects lazily, so the app starts even if Redis is not up yet.
    return Redis.from_url(settings.redis_url, decode_responses=True)
