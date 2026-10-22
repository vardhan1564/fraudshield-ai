"""Alerts and cases (spec 04 endpoints 11, 13, 14): the analyst's queue and case detail."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AlertStatus, CaseLabel, Channel, Decision, Severity
from app.schemas.transactions import Reason


class AlertItem(BaseModel):
    alert_id: uuid.UUID
    case_id: uuid.UUID | None
    txn_id: str  # the client's id
    severity: Severity
    status: AlertStatus
    assigned_to: uuid.UUID | None
    risk_score: float | None
    decision: Decision | None
    amount: float
    channel: Channel
    top_reasons: list[Reason]
    created_at: datetime


class CaseCustomer(BaseModel):
    id: uuid.UUID
    external_id: str | None
    name: str
    phone: str | None  # masked, 98xxxxxx21


class CaseTransaction(BaseModel):
    id: uuid.UUID
    txn_id: str
    amount: float
    currency: str
    channel: Channel
    merchant_category: str | None
    payee_vpa: str
    risk_score: float | None
    decision: Decision | None
    action: str | None
    model_version: str | None
    ts: datetime
    customer: CaseCustomer


class CaseDetail(BaseModel):
    case_id: uuid.UUID
    alert_id: uuid.UUID
    severity: Severity
    status: AlertStatus
    assigned_to: uuid.UUID | None
    label: CaseLabel | None
    notes: str | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime
    transaction: CaseTransaction
    top_reasons: list[Reason]
    explanation: str | None


class CaseUpdate(BaseModel):
    """PATCH /cases/{id} (spec 04 §2.4). `status` is the alert's; label and notes the case's.
    Labelling resolves the case; a case needs a label before it can be closed. `severity` is the
    alert's: Escalate raises it to high and puts the alert back in the queue."""

    model_config = ConfigDict(extra="forbid")

    status: AlertStatus | None = None
    label: CaseLabel | None = None
    notes: str | None = Field(default=None, max_length=4000)
    assigned_to: uuid.UUID | None = None
    severity: Severity | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "CaseUpdate":
        if not self.model_fields_set:
            raise ValueError("send at least one of status, label, notes, assigned_to, severity")
        return self
