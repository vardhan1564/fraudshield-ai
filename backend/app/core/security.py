"""Passwords (bcrypt, cost 12) and JWTs (HS256: 30-min access, 7-day refresh), spec 08."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
import jwt

from app.core.config import Settings
from app.core.errors import AppError

BCRYPT_ROUNDS = 12
ALGORITHM = "HS256"
TokenKind = Literal["access", "refresh"]

# Checked against when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def hash_password(password: str, rounds: int = BCRYPT_ROUNDS) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    role: str
    kind: TokenKind
    jti: str  # unique token id; refresh tokens are tracked in Redis by it
    expires_at: datetime


def create_token(
    user_id: uuid.UUID,
    role: str,
    kind: TokenKind,
    settings: Settings,
    now: datetime | None = None,
) -> tuple[str, TokenClaims]:
    issued = now or datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.access_token_expire_minutes)
        if kind == "access"
        else timedelta(days=settings.refresh_token_expire_days)
    )
    claims = TokenClaims(user_id, role, kind, uuid.uuid4().hex, issued + lifetime)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": kind,
        "jti": claims.jti,
        "iat": issued,
        "exp": claims.expires_at,
    }
    token = jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=ALGORITHM)
    return token, claims


def decode_token(token: str, kind: TokenKind, settings: Settings) -> TokenClaims:
    """The token's claims, or 401 if it is invalid, expired or the wrong kind."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[ALGORITHM],
            options={"require": ["sub", "type", "jti", "exp"]},
        )
        if payload["type"] != kind:
            raise jwt.InvalidTokenError("wrong token type")
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            role=str(payload.get("role", "")),
            kind=kind,
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Token expired") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise unauthorized("Invalid token") from exc


def unauthorized(message: str) -> AppError:
    return AppError(401, "UNAUTHORIZED", message, headers={"WWW-Authenticate": "Bearer"})
