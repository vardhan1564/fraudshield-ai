"""Shared FastAPI dependencies: settings, DB session, Redis, current user and role guards.

Every router declares who may call it (spec 08):
    @router.get("/alerts", dependencies=[Depends(require_role(Role.ANALYST))])
or takes the user: `user: Annotated[User, Depends(require_role(Role.ANALYST))]`.
Partner systems send `X-API-Key` instead of a JWT; `require_principal` accepts both.
"""

import hmac
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import get_session
from app.core.errors import AppError
from app.core.security import decode_token, unauthorized
from app.models import User
from app.models.enums import Role

bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
ApiKeyDep = Annotated[str | None, Depends(api_key_header)]


async def get_current_user(
    credentials: BearerDep, session: SessionDep, settings: SettingsDep
) -> User:
    """The user behind a valid access token, else 401."""
    if credentials is None:
        raise unauthorized("Missing bearer token")
    claims = decode_token(credentials.credentials, "access", settings)
    user = await session.get(User, claims.user_id)
    if user is None:
        raise unauthorized("Account no longer exists")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def forbidden() -> AppError:
    return AppError(403, "FORBIDDEN", "Your role cannot do this")


def require_role(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Dependency: the current user if their role is one of `roles`, else 403."""

    async def guard(user: CurrentUser) -> User:
        if user.role not in roles:
            raise forbidden()
        return user

    return guard


@dataclass(frozen=True)
class Principal:
    """Who is calling: a logged-in user, or a partner system (API key, no user row)."""

    role: Role
    user: User | None = None
    partner: str | None = None

    @property
    def user_id(self) -> uuid.UUID | None:
        return None if self.user is None else self.user.id


def partner_for_key(key: str, settings: Settings) -> str | None:
    for known, name in settings.partner_keys().items():
        if hmac.compare_digest(key.encode(), known.encode()):  # constant time
            return name
    return None


def require_principal(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    """Dependency for endpoints that partners may also call, e.g. /transactions/score."""

    async def guard(
        api_key: ApiKeyDep,
        credentials: BearerDep,
        session: SessionDep,
        settings: SettingsDep,
    ) -> Principal:
        if api_key is not None:
            partner = partner_for_key(api_key, settings)
            if partner is None:
                raise AppError(401, "UNAUTHORIZED", "Invalid API key")
            principal = Principal(role=Role.PARTNER, partner=partner)
        else:
            user = await get_current_user(credentials, session, settings)
            principal = Principal(role=user.role, user=user)
        if principal.role not in roles:
            raise forbidden()
        return principal

    return guard
