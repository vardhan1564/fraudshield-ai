"""Auth (spec 04 endpoints 1-4, spec 08): tokens, rotation, role guards, rate limit, API keys.

Runs against the test database and Redis db 15 (flushed per test).
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, require_principal, require_role
from app.core.config import Settings
from app.core.privacy import mask_phone
from app.core.security import create_token, hash_password
from app.main import create_app
from app.models import User
from app.models.enums import Role
from tests.conftest import unavailable

PASSWORD = "correct-horse-battery"


@pytest.fixture
def settings(db_url: str) -> Settings:
    redis_url = Settings().redis_url.rsplit("/", 1)[0] + "/15"
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=db_url,
        redis_url=redis_url,
        login_rate_limit_per_minute=3,
        partner_api_keys="bank-a:test-partner-key",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    redis: Redis = app.state.redis
    try:
        await redis.flushdb()
    except (OSError, RedisConnectionError) as exc:
        unavailable("Redis", exc)

    # Throw-away routes to exercise the guards the real routers will use.
    probe = APIRouter(prefix="/api/v1/probe")

    @probe.get("/analyst")
    async def analyst_only(
        user: Annotated[User, Depends(require_role(Role.ANALYST))],
    ) -> dict[str, str]:
        return {"role": user.role.value}

    @probe.get("/score")
    async def score(
        who: Annotated[Principal, Depends(require_principal(Role.PARTNER, Role.CUSTOMER))],
    ) -> dict[str, str | None]:
        return {"role": who.role.value, "partner": who.partner}

    app.include_router(probe)
    yield app
    await redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def make_user(app: FastAPI, role: Role) -> User:
    async with app.state.sessionmaker() as session:
        session: AsyncSession
        user = User(
            name=f"Test {role.value}",
            phone="9812345621",
            email=f"{role.value}-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password(PASSWORD, rounds=4),
            role=role,
        )
        session.add(user)
        await session.commit()
        return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    tokens: dict[str, str] = r.json()
    return tokens


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- register / login / me ---------------------------------------------------------------------


async def test_register_then_me_with_masked_phone(client: AsyncClient) -> None:
    email = f"New.User-{uuid.uuid4().hex[:6]}@Example.com"
    body = {"name": "Riya", "phone": "9876543210", "email": email, "password": PASSWORD}
    r = await client.post("/api/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    tokens = r.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 30 * 60

    me = (await client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"]))).json()
    assert me["role"] == "customer" and me["email"] == email.lower()
    assert me["phone"] == "98xxxxxx10"

    again = await client.post("/api/v1/auth/register", json={**body, "email": email.upper()})
    assert again.status_code == 409 and again.json()["error"]["code"] == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "change",
    [{"password": "short"}, {"phone": "12345"}, {"email": "not-an-email"}, {"password": "é" * 40}],
)
async def test_register_validation_is_400(client: AsyncClient, change: dict[str, str]) -> None:
    body = {"name": "A", "phone": "9876543210", "email": "a@b.co", "password": PASSWORD}
    r = await client.post("/api/v1/auth/register", json={**body, **change})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_wrong_password_and_unknown_email_get_the_same_401(
    app: FastAPI, client: AsyncClient
) -> None:
    user = await make_user(app, Role.CUSTOMER)
    assert user.email is not None
    bad = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "nope"})
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@test.local", "password": PASSWORD}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"


async def test_me_needs_a_valid_access_token(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    missing = await client.get("/api/v1/auth/me")
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    garbage = await client.get("/api/v1/auth/me", headers=bearer("not.a.jwt"))
    assert garbage.status_code == 401

    user = await make_user(app, Role.CUSTOMER)
    old = datetime.now(UTC) - timedelta(hours=1)
    expired, _ = create_token(user.id, user.role.value, "access", settings, now=old)
    r = await client.get("/api/v1/auth/me", headers=bearer(expired))
    assert r.status_code == 401 and r.json()["error"]["message"] == "Token expired"

    wrong_key = Settings(_env_file=None, secret_key="another-secret-key-of-32-characters!")
    forged, _ = create_token(user.id, "admin", "access", wrong_key)
    assert (await client.get("/api/v1/auth/me", headers=bearer(forged))).status_code == 401

    assert user.email is not None
    tokens = await login(client, user.email)
    as_access = await client.get("/api/v1/auth/me", headers=bearer(tokens["refresh_token"]))
    assert as_access.status_code == 401  # a refresh token is not an access token


# --- refresh rotation --------------------------------------------------------------------------


async def test_refresh_token_works_once(app: FastAPI, client: AsyncClient) -> None:
    user = await make_user(app, Role.CUSTOMER)
    assert user.email is not None
    first = await login(client, user.email)

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    me = await client.get("/api/v1/auth/me", headers=bearer(second["access_token"]))
    assert me.status_code == 200

    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reused.status_code == 401  # rotation: the old one is spent
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 200


# --- roles, partners, rate limit ---------------------------------------------------------------


async def test_require_role_gives_403_to_other_roles(app: FastAPI, client: AsyncClient) -> None:
    customer, analyst = await make_user(app, Role.CUSTOMER), await make_user(app, Role.ANALYST)
    assert customer.email is not None and analyst.email is not None
    as_customer = await login(client, customer.email)
    r = await client.get("/api/v1/probe/analyst", headers=bearer(as_customer["access_token"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"

    as_analyst = await login(client, analyst.email)
    r = await client.get("/api/v1/probe/analyst", headers=bearer(as_analyst["access_token"]))
    assert r.status_code == 200 and r.json() == {"role": "analyst"}
    assert (await client.get("/api/v1/probe/analyst")).status_code == 401


async def test_partner_api_key(app: FastAPI, client: AsyncClient) -> None:
    r = await client.get("/api/v1/probe/score", headers={"X-API-Key": "test-partner-key"})
    assert r.status_code == 200 and r.json() == {"role": "partner", "partner": "bank-a"}
    r = await client.get("/api/v1/probe/score", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401

    analyst = await make_user(app, Role.ANALYST)
    assert analyst.email is not None
    tokens = await login(client, analyst.email)
    r = await client.get("/api/v1/probe/score", headers=bearer(tokens["access_token"]))
    assert r.status_code == 403  # only partners and customers may score


async def test_login_rate_limit(app: FastAPI, client: AsyncClient) -> None:
    user = await make_user(app, Role.CUSTOMER)
    body = {"email": user.email, "password": "wrong-password"}
    codes = [(await client.post("/api/v1/auth/login", json=body)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]  # limit is 3 per minute in these settings
    blocked = await client.post("/api/v1/auth/login", json={**body, "password": PASSWORD})
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert 0 < int(blocked.headers["retry-after"]) <= 60


# --- helpers -----------------------------------------------------------------------------------


def test_mask_phone() -> None:
    assert mask_phone("9812345621") == "98xxxxxx21"
    assert mask_phone("123") == "xxx" and mask_phone(None) is None


def test_partner_keys_setting() -> None:
    s = Settings(_env_file=None, partner_api_keys="bank-a:k1, bank-b:k2")
    assert s.partner_keys() == {"k1": "bank-a", "k2": "bank-b"}
    assert Settings(_env_file=None).partner_keys() == {}
    with pytest.raises(ValueError, match="name:key"):
        Settings(_env_file=None, partner_api_keys="just-a-key")
