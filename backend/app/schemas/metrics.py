"""GET /metrics/overview (spec 04 §2.8)."""

from datetime import datetime

from pydantic import BaseModel


class HourCount(BaseModel):
    hour: int  # 0-23, IST
    total: int
    flagged: int


class TypeCount(BaseModel):
    scenario: str
    count: int


class CityCount(BaseModel):
    city: str
    flagged: int


class MetricsOverview(BaseModel):
    date_from: datetime
    date_to: datetime
    txn_count: int
    fraud_caught: int
    amount_saved_inr: float
    false_positive_rate: float | None  # None until analysts label cases
    p95_latency_ms: float | None
    by_hour: list[HourCount]
    by_type: list[TypeCount]
    by_city: list[CityCount]
