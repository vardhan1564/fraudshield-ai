"""Register, log in and rotate tokens (spec 04 endpoints 1-3, spec 08 security checklist).

Redis keys (spec 05 §2):
- `refresh:{jti}` -> user id, TTL = refresh lifetime. A refresh token works once: using it
  deletes the key (GETDEL), so a stolen token that was already used gets 401.
- `rl:login:{ip}` -> login attempts in the current minute.
"""

import asyncio

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    unauthorized,
    verify_password,
)
from app.models import User
from app.models.enums import Role
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.services import audit

LOGIN_WINDOW_S = 60


async def issue_tokens(user: User, redis: Redis, settings: Settings) -> TokenResponse:
    access, _ = create_token(user.id, user.role.value, "access", settings)
    refresh, claims = create_token(user.id, user.role.value, "refresh", settings)
    ttl = settings.refresh_token_expire_days * 86_400
    await redis.set(f"refresh:{claims.jti}", str(user.id), ex=ttl)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def register(
    body: RegisterRequest,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    client_ip: str | None = None,
) -> TokenResponse:
    """Create a customer account and log it in. Staff accounts are made by admins/seed."""
    email_taken = AppError(409, "EMAIL_TAKEN", "An account with this email already exists")
    if await session.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise email_taken
    user = User(
        name=body.name,
        phone=body.phone,
        email=body.email,
        password_hash=await asyncio.to_thread(hash_password, body.password),
        role=Role.CUSTOMER,
        language=body.language,
    )
    session.add(user)
    try:
        await session.flush()  # user.id for the audit row
        audit.record(
            session,
            actor_id=user.id,
            action="auth.register",
            entity="user",
            entity_id=user.id,
            after={"role": user.role.value, "language": user.language.value},
            ip=client_ip,
        )
        await session.commit()
    except IntegrityError as exc:  # same email registered at the same moment
        await session.rollback()
        raise email_taken from exc
    return await issue_tokens(user, redis, settings)


async def check_login_rate(client_ip: str, redis: Redis, settings: Settings) -> None:
    key = f"rl:login:{client_ip}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, LOGIN_WINDOW_S, nx=True)
        attempts, _ = await pipe.execute()
    if int(attempts) > settings.login_rate_limit_per_minute:
        retry = max(int(await redis.ttl(key)), 1)
        raise AppError(
            429,
            "RATE_LIMITED",
            "Too many login attempts, try again in a minute",
            headers={"Retry-After": str(retry)},
        )


async def login(
    body: LoginRequest,
    client_ip: str,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
) -> TokenResponse:
    await check_login_rate(client_ip, redis, settings)
    user = await session.scalar(select(User).where(User.email == body.email.strip().lower()))
    ok = await asyncio.to_thread(
        verify_password, body.password, None if user is None else user.password_hash
    )
    if user is None or not ok:
        audit.record(session, actor_id=None, action="auth.login_failed", entity="user",
                     entity_id=None if user is None else user.id, ip=client_ip)  # fmt: skip
        await session.commit()
        raise unauthorized("Wrong email or password")  # same answer for both: no user probing
    audit.record(session, actor_id=user.id, action="auth.login", entity="user",
                 entity_id=user.id, ip=client_ip)  # fmt: skip
    await session.commit()
    return await issue_tokens(user, redis, settings)


async def refresh(
    refresh_token: str, session: AsyncSession, redis: Redis, settings: Settings
) -> TokenResponse:
    claims = decode_token(refresh_token, "refresh", settings)
    if await redis.getdel(f"refresh:{claims.jti}") is None:
        raise unauthorized("Refresh token already used or revoked")
    user = await session.get(User, claims.user_id)
    if user is None:
        raise unauthorized("Account no longer exists")
    return await issue_tokens(user, redis, settings)
