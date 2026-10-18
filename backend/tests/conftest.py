import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]
IN_CI = os.environ.get("CI") == "true"


def unavailable(service: str, exc: Exception) -> None:
    """Locally a missing service skips the test; in CI (CI=true) it fails."""
    if IN_CI:
        pytest.fail(f"{service} not reachable in CI: {exc}")
    pytest.skip(f"{service} not running (start infra with docker compose): {exc}")


# --- a separate Postgres database for tests, never the dev data -------------------------------


def test_db_url() -> str:
    url = make_url(Settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


test_db_url.__test__ = False  # type: ignore[attr-defined]  # a helper, not a test


async def ensure_test_db(url: str) -> None:
    admin = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    name = make_url(url).database
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("select 1 from pg_database where datname = :n"), {"n": name}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except (OSError, OperationalError, InterfaceError) as exc:
        unavailable("Postgres", exc)
    finally:
        await admin.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    """The test database, migrated to head."""
    url = test_db_url()
    asyncio.run(ensure_test_db(url))
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def settings() -> Settings:
    # Ignore the developer's .env so tests behave the same everywhere (and in CI).
    return Settings(_env_file=None, app_env="test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: let the 500 handler answer instead of re-raising into the test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
