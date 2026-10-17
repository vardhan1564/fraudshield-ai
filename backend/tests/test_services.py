"""Postgres and Redis are reachable with the configured URLs.

Locally these skip when Docker isn't running. In CI (GitHub sets CI=true) they fail
instead, so a broken service container can't pass silently.
"""

import os

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.core.config import Settings
from app.core.db import make_engine

IN_CI = os.environ.get("CI") == "true"


def _unavailable(service: str, exc: Exception) -> None:
    if IN_CI:
        pytest.fail(f"{service} not reachable in CI: {exc}")
    pytest.skip(f"{service} not running (start infra with docker compose): {exc}")


async def test_postgres_reachable() -> None:
    engine = make_engine(Settings())
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("select 1"))).scalar_one() == 1
    except (OSError, OperationalError, InterfaceError) as exc:  # refused / unreachable
        _unavailable("Postgres", exc)
    finally:
        await engine.dispose()


async def test_redis_reachable() -> None:
    client = Redis.from_url(Settings().redis_url)
    try:
        assert await client.ping() is True
    except (OSError, RedisConnectionError) as exc:
        _unavailable("Redis", exc)
    finally:
        await client.aclose()
