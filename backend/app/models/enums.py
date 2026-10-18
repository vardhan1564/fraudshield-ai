"""Postgres enum types from spec 05. The database stores each member's value."""

import enum

from sqlalchemy import Enum


class Role(enum.StrEnum):
    CUSTOMER = "customer"
    ANALYST = "analyst"
    ADMIN = "admin"
    PARTNER = "partner"


class Language(enum.StrEnum):
    EN = "en"
    HI = "hi"
    MR = "mr"


class Channel(enum.StrEnum):
    UPI = "UPI"
    CARD = "CARD"
    WALLET = "WALLET"


class Decision(enum.StrEnum):
    APPROVE = "APPROVE"
    STEP_UP = "STEP_UP"
    HOLD = "HOLD"


class RuleAction(enum.StrEnum):
    STEP_UP = "STEP_UP"
    HOLD = "HOLD"


class Severity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AlertStatus(enum.StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    CLOSED = "closed"


class CaseLabel(enum.StrEnum):
    FRAUD = "fraud"
    LEGIT = "legit"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class EdgeType(enum.StrEnum):
    SENT = "SENT"
    USED_DEVICE = "USED_DEVICE"
    LOGGED_FROM = "LOGGED_FROM"


class RingStatus(enum.StrEnum):
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


def pg_enum(cls: type[enum.StrEnum], name: str) -> Enum:
    """A named Postgres enum that stores values (`customer`), not member names (`CUSTOMER`)."""
    return Enum(cls, name=name, values_callable=lambda members: [m.value for m in members])
