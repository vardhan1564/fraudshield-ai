"""Graph edges, suspected mule rings and the Redis outbox (spec 05 §1, §3)."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPk
from app.models.enums import EdgeType, RingStatus, pg_enum


class GraphEdge(Base):
    """Written by the worker's graph_edges consumer for every scored transaction."""

    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    edge_type: Mapped[EdgeType] = mapped_column(pg_enum(EdgeType, "edge_type"))
    src_id: Mapped[str] = mapped_column(Text, index=True)  # e.g. A:{user_id}
    dst_id: Mapped[str] = mapped_column(Text, index=True)  # e.g. P:{vpa}
    amount: Mapped[Decimal | None]
    txn_id: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(index=True)


class Ring(UUIDPk, Base):
    __tablename__ = "rings"

    member_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    score: Mapped[float]
    # fan_in, pass_through, shared_device, new_account_burst, gnn, community
    detector: Mapped[str] = mapped_column(Text)
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
    status: Mapped[RingStatus] = mapped_column(
        pg_enum(RingStatus, "ring_status"),
        default=RingStatus.SUSPECTED,
        server_default=RingStatus.SUSPECTED.value,
    )


class Outbox(Base):
    """Fallback when Redis is down: the worker replays rows with sent_at IS NULL."""

    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_unsent", "id", postgresql_where=text("sent_at IS NULL")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    stream: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    sent_at: Mapped[datetime | None]
