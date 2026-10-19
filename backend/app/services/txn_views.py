"""Reading scored payments (spec 04 endpoints 7-8): the analyst list and one payment's detail."""

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Device, ModelScore, Payee, Transaction, User
from app.models.enums import Channel, Decision, Role
from app.schemas.common import Page
from app.schemas.txn_views import TxnDetail, TxnDevice, TxnItem, TxnScores
from app.services import explain
from app.services.lookups import by_ref, user_filter


def _item(txn: Transaction, user: User, payee: Payee) -> dict[str, Any]:
    return {
        "id": txn.id,
        "txn_id": txn.txn_id,
        "user_id": user.id,
        "customer_external_id": user.external_id,
        "amount": float(txn.amount),
        "currency": txn.currency,
        "channel": txn.channel,
        "merchant_category": txn.merchant_category,
        "payee_vpa": payee.vpa_or_account,
        "risk_score": txn.risk_score,
        "decision": txn.decision,
        "action": txn.action,
        "model_version": txn.model_version,
        "latency_ms": txn.latency_ms,
        "ts": txn.ts,
    }


def _base() -> Select[Transaction, User, Payee]:
    return (
        select(Transaction, User, Payee)
        .join(User, User.id == Transaction.user_id)
        .join(Payee, Payee.id == Transaction.payee_id)
    )


async def list_transactions(
    session: AsyncSession,
    *,
    decision: Decision | None,
    channel: Channel | None,
    user_ref: str | None,
    payee_vpa: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    min_risk: float | None,
    page: int,
    page_size: int,
) -> Page[TxnItem]:
    """Newest first. `date_from` inclusive, `date_to` exclusive."""
    conditions: list[ColumnElement[bool]] = []
    if decision is not None:
        conditions.append(Transaction.decision == decision)
    if channel is not None:
        conditions.append(Transaction.channel == channel)
    if user_ref:
        conditions.append(user_filter(user_ref))
    if payee_vpa:
        conditions.append(Payee.vpa_or_account == payee_vpa.strip().lower())
    if date_from is not None:
        conditions.append(Transaction.ts >= date_from)
    if date_to is not None:
        conditions.append(Transaction.ts < date_to)
    if min_risk is not None:
        conditions.append(Transaction.risk_score >= min_risk)
    query = _base().where(*conditions)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.execute(
        query.order_by(Transaction.ts.desc(), Transaction.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [TxnItem(**_item(txn, user, payee)) for txn, user, payee in rows]
    return Page(items=items, page=page, page_size=page_size, total=total)


async def get_transaction(session: AsyncSession, txn_ref: str, viewer: User) -> TxnDetail:
    """Analysts and admins see any payment; a customer only their own (404 otherwise)."""
    row = (
        await session.execute(
            _base()
            .add_columns(ModelScore, Device)
            .outerjoin(ModelScore, ModelScore.txn_id == Transaction.id)
            .outerjoin(Device, Device.id == Transaction.device_id)
            .where(by_ref(txn_ref))
        )
    ).first()
    if row is None or (viewer.role == Role.CUSTOMER and row[0].user_id != viewer.id):
        raise AppError(404, "NOT_FOUND", "Transaction not found")
    txn, user, payee, scores, device = row
    return TxnDetail(
        **_item(txn, user, payee),
        model_scores=None
        if scores is None
        else TxnScores(
            transaction=scores.transaction_score,
            anomaly=scores.anomaly_score,
            graph=scores.graph_score,
            social_eng=scores.social_eng_score,
            meta=scores.meta_score,
        ),
        top_reasons=explain.top_reasons(
            (scores.shap_json or {}).get("top", []) if scores else [], viewer.language
        ),
        explanation=None if scores is None else scores.explanation,
        context=txn.context_json,
        device=None
        if device is None
        else TxnDevice(
            fingerprint=device.fingerprint, is_rooted=device.is_rooted, first_seen=device.first_seen
        ),
        ip=None if txn.ip is None else str(txn.ip),
        geo_lat=txn.geo_lat,
        geo_lon=txn.geo_lon,
    )
