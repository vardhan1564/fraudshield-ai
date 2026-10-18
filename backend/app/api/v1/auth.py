"""/auth: register, login, refresh, me (spec 04 endpoints 1-4)."""

from typing import Any

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, RedisDep, SessionDep, SettingsDep
from app.core.privacy import mask_phone
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.errors import ErrorResponse
from app.services import auth as service

router = APIRouter(prefix="/auth", tags=["auth"])
ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 409, 429)
}


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED,
    responses=ERRORS,
)  # fmt: skip
async def register(
    body: RegisterRequest, session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> TokenResponse:
    """Create a customer account and return its tokens (409 EMAIL_TAKEN if the email exists)."""
    return await service.register(body, session, redis, settings)


@router.post("/login", response_model=TokenResponse, responses=ERRORS)
async def login(
    body: LoginRequest,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> TokenResponse:
    """Access + refresh token. At most LOGIN_RATE_LIMIT_PER_MINUTE attempts per IP (429)."""
    client_ip = request.client.host if request.client else "unknown"
    return await service.login(body, client_ip, session, redis, settings)


@router.post("/refresh", response_model=TokenResponse, responses=ERRORS)
async def refresh(
    body: RefreshRequest, session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> TokenResponse:
    """Swap a refresh token for a new pair. Each refresh token works once (rotation)."""
    return await service.refresh(body.refresh_token, session, redis, settings)


@router.get("/me", response_model=UserResponse, responses=ERRORS)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        phone=mask_phone(user.phone) or "",
        email=user.email,
        role=user.role,
        language=user.language,
    )
