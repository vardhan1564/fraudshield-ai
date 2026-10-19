"""POST /reports and GET /payees/{vpa}/reputation (spec 04 endpoints 17-18, spec 01 F7)."""

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

RiskLevel = Literal["low", "medium", "high"]


class ScamType(StrEnum):
    """What customers can report (the simulator's scams + the message scanner's labels)."""

    DIGITAL_ARREST = "digital_arrest"
    FAKE_SUPPORT_REFUND = "fake_support_refund"
    INVESTMENT_TASK = "investment_task"
    FAKE_KYC = "fake_kyc"
    LOTTERY_PRIZE = "lottery_prize"
    JOB_TASK = "job_task"
    PHISHING = "phishing"
    OTHER = "other"


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payee_vpa: str = Field(min_length=3, max_length=255)
    phone: str | None = Field(default=None, max_length=20)
    scam_type: ScamType
    description: str | None = Field(default=None, max_length=2000)
    evidence_url: HttpUrl | None = None

    @field_validator("payee_vpa")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.lower()

    @field_validator("phone")
    @classmethod
    def _indian_mobile(cls, value: str | None) -> str | None:
        """+91 98123 45621 / 09812345621 / 9812345621 -> 9812345621."""
        if value is None:
            return None
        digits = re.sub(r"[\s-]", "", value).removeprefix("+91").removeprefix("0")
        if not re.fullmatch(r"[6-9]\d{9}", digits):
            raise ValueError("must be a 10-digit Indian mobile number")
        return digits


class ReportCreated(BaseModel):
    report_id: uuid.UUID
    payee_vpa: str
    report_count: int
    reputation_score: float
    risk_level: RiskLevel


class ReportView(BaseModel):
    report_id: uuid.UUID
    scam_type: str
    description: str | None
    phone: str | None  # masked
    created_at: datetime


class PayeeReputation(BaseModel):
    vpa: str
    known: bool  # False: we have never seen this payee
    reputation_score: float
    risk_level: RiskLevel
    report_count: int
    confirmed_fraud_count: int
    graph_flag: bool
    account_age_days: int | None
    reports_by_type: dict[str, int]
    recent_reports: list[ReportView] | None  # analysts and admins only
