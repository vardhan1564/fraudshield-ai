"""People, devices, payees and payments (spec 05 §1)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, ForeignKey, Index, SmallInteger, Text, desc
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk
from app.models.enums import Channel, Decision, Language, Role, pg_enum


class User(UUIDPk, Base):
    __tablename__ = "users"

    external_id: Mapped[str | None] = mapped_column(Text, unique=True)  # simulator id, e.g. U1029
    name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)  # stored full, masked in the API
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)  # bcrypt
    role: Mapped[Role] = mapped_column(pg_enum(Role, "user_role"))
    language: Mapped[Language] = mapped_column(
        pg_enum(Language, "user_language"), default=Language.EN, server_default=Language.EN.value
    )
    # Customer profile; null for staff accounts.
    city: Mapped[str | None] = mapped_column(Text)
    city_tier: Mapped[int | None] = mapped_column(SmallInteger)
    age_band: Mapped[str | None] = mapped_column(Text)  # 18-25 ... 60+
    kyc_level: Mapped[int | None] = mapped_column(SmallInteger)
    avg_txn_amount: Mapped[Decimal | None]  # refreshed nightly


class Device(UUIDPk, Base):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(Text)
    os: Mapped[str | None] = mapped_column(Text)
    is_rooted: Mapped[bool] = mapped_column(default=False, server_default="false")
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]


class Payee(UUIDPk, Base):
    __tablename__ = "payees"

    vpa_or_account: Mapped[str] = mapped_column(Text, unique=True)
    # Set when the payee is also a customer account (mule pass-through detection).
    account_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    display_name: Mapped[str | None] = mapped_column(Text)
    account_age_days: Mapped[int | None]
    report_count: Mapped[int] = mapped_column(default=0, server_default="0")
    confirmed_fraud_count: Mapped[int] = mapped_column(default=0, server_default="0")
    reputation_score: Mapped[float] = mapped_column(default=0.0, server_default="0")
    graph_flag: Mapped[bool] = mapped_column(default=False, server_default="false")


class Transaction(UUIDPk, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_id_ts", "user_id", desc("ts")),
        Index("ix_transactions_payee_id_ts", "payee_id", desc("ts")),
    )

    txn_id: Mapped[str] = mapped_column(Text, unique=True)  # client id
    # Indexed by the (user_id, ts) and (payee_id, ts) composites above.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    payee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payees.id"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id"))
    amount: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(CHAR(3), default="INR", server_default="INR")
    channel: Mapped[Channel] = mapped_column(pg_enum(Channel, "txn_channel"))
    merchant_category: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)
    geo_lat: Mapped[float | None]
    geo_lon: Mapped[float | None]
    context_json: Mapped[dict[str, Any] | None]  # call / screen-share signals
    ts: Mapped[datetime] = mapped_column(index=True)
    # Filled once the payment is scored.
    risk_score: Mapped[float | None]
    decision: Mapped[Decision | None] = mapped_column(pg_enum(Decision, "txn_decision"), index=True)
    action: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    scenario: Mapped[str | None] = mapped_column(Text)  # simulator ground truth, never in the UI
    latency_ms: Mapped[int | None]


class ModelScore(Base):
    __tablename__ = "model_scores"

    txn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), primary_key=True)
    transaction_score: Mapped[float | None]
    anomaly_score: Mapped[float | None]
    graph_score: Mapped[float | None]
    social_eng_score: Mapped[float | None]
    meta_score: Mapped[float | None]
    shap_json: Mapped[dict[str, Any] | None]
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_lang: Mapped[Language | None] = mapped_column(pg_enum(Language, "user_language"))
