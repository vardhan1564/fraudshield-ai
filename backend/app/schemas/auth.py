"""Request/response bodies for /auth (spec 04 endpoints 1-4)."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Language, Role

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _check_password(value: str) -> str:
    if len(value.encode()) > 72:  # bcrypt only reads the first 72 bytes
        raise ValueError("password must be at most 72 bytes")
    return value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(pattern=r"^[6-9]\d{9}$", description="10-digit Indian mobile number")
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8)
    language: Language = Language.EN

    _password = field_validator("password")(_check_password)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.lower()


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=2048)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserResponse(BaseModel):
    """The current user. The phone number is masked (98xxxxxx21) like everywhere in the API."""

    id: uuid.UUID
    name: str
    phone: str
    email: str | None
    role: Role
    language: Language
