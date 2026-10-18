"""Scam Coach, alerts and cases, reports, trusted contacts, rules, models, audit (spec 05 §1)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk
from app.models.enums import (
    AlertStatus,
    ApprovalStatus,
    CaseLabel,
    RuleAction,
    Severity,
    pg_enum,
)


class CoachSession(UUIDPk, Base):
    __tablename__ = "coach_sessions"

    txn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), index=True)
    questions: Mapped[list[Any]]
    answers: Mapped[list[Any] | None]
    red_flags: Mapped[int] = mapped_column(default=0, server_default="0")
    outcome: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None]


class Alert(UUIDPk, Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_status_created_at", "status", "created_at"),)

    txn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), index=True)
    severity: Mapped[Severity] = mapped_column(pg_enum(Severity, "alert_severity"))
    status: Mapped[AlertStatus] = mapped_column(
        pg_enum(AlertStatus, "alert_status"),
        default=AlertStatus.OPEN,
        server_default=AlertStatus.OPEN.value,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Case(UUIDPk, Base):
    __tablename__ = "cases"

    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id"), unique=True)
    label: Mapped[CaseLabel | None] = mapped_column(pg_enum(CaseLabel, "case_label"))
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None]


class Report(UUIDPk, Base):
    __tablename__ = "reports"

    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    payee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payees.id"), index=True)
    phone: Mapped[str | None] = mapped_column(Text)
    scam_type: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(Text)


class TrustedContact(UUIDPk, Base):
    __tablename__ = "trusted_contacts"
    __table_args__ = (UniqueConstraint("user_id", "contact_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    contact_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    approval_limit: Mapped[Decimal]
    active: Mapped[bool] = mapped_column(default=True, server_default="true")


class Approval(UUIDPk, Base):
    __tablename__ = "approvals"

    txn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trusted_contacts.id"))
    status: Mapped[ApprovalStatus] = mapped_column(
        pg_enum(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    expires_at: Mapped[datetime]
    responded_at: Mapped[datetime | None]


class Rule(UUIDPk, Base):
    __tablename__ = "rules"

    name: Mapped[str] = mapped_column(Text, unique=True)
    # {"all": [{"field": "amount", "op": ">", "value": 50000}, ...]}
    condition_json: Mapped[dict[str, Any]]
    action: Mapped[RuleAction] = mapped_column(pg_enum(RuleAction, "rule_action"))
    priority: Mapped[int] = mapped_column(default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class ModelVersion(UUIDPk, Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    mlflow_run_id: Mapped[str | None] = mapped_column(Text)
    metrics_json: Mapped[dict[str, Any] | None]
    is_active: Mapped[bool] = mapped_column(default=False, server_default="false")
    deployed_at: Mapped[datetime | None]


class AuditLog(Base):
    """Append-only: the migration adds a trigger that rejects UPDATE and DELETE."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text)
    entity: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before_json: Mapped[dict[str, Any] | None]
    after_json: Mapped[dict[str, Any] | None]
    ip: Mapped[str | None] = mapped_column(INET)
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
