"""GET /transactions and GET /transactions/{id} (spec 04 endpoints 7-8)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import Channel, Decision
from app.schemas.transactions import Reason


class TxnItem(BaseModel):
    id: uuid.UUID
    txn_id: str
    user_id: uuid.UUID
    customer_external_id: str | None
    amount: float
    currency: str
    channel: Channel
    merchant_category: str | None
    payee_vpa: str
    risk_score: float | None
    decision: Decision | None
    action: str | None
    model_version: str | None
    latency_ms: int | None
    ts: datetime


class TxnScores(BaseModel):
    transaction: float | None
    anomaly: float | None
    graph: float | None
    social_eng: float | None
    meta: float | None


class TxnDevice(BaseModel):
    fingerprint: str
    is_rooted: bool
    first_seen: datetime


class TxnDetail(TxnItem):
    model_scores: TxnScores | None
    top_reasons: list[Reason]
    explanation: str | None
    context: dict[str, Any] | None  # call / screen-share / behaviour signals
    device: TxnDevice | None
    ip: str | None
    geo_lat: float | None
    geo_lon: float | None
