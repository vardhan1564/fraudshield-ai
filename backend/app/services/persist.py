"""Saving one scored payment to Postgres from its stream event (T3.9).

The API no longer writes rows after each response: that ORM/SQL work ran on its event loop and
was ~40% of its CPU per payment. The worker does it instead (consumer `txn_writer`), and
`alert_pusher` / `llm_explainer` call `ensure_saved` first, because consumer groups run
independently and either may see the event before `txn_writer`.

Idempotent: a transaction-scoped advisory lock per txn_id, then "already there? done". Usual case
(known payee and device): 4 statements.
"""

import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Table, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Device, ModelScore, Payee, Transaction
from app.models.enums import Channel, Decision, Language

PAYEES = cast(Table, Payee.__table__)
DEVICES = cast(Table, Device.__table__)
TRANSACTIONS = cast(Table, Transaction.__table__)
MODEL_SCORES = cast(Table, ModelScore.__table__)

DEVICE_TOUCH_S = 60.0  # devices.last_seen is kept to the minute, not to the payment
CACHE_MAX = 100_000
_payee_ids: dict[str, uuid.UUID] = {}
_devices: dict[tuple[uuid.UUID, str], tuple[uuid.UUID, float]] = {}


def _remember(cache: dict[Any, Any], key: Any, value: Any) -> None:
    if len(cache) >= CACHE_MAX:  # simple bound; refills from Postgres
        cache.clear()
    cache[key] = value


async def ensure_saved(
    sessionmaker: async_sessionmaker[AsyncSession], event: dict[str, Any]
) -> None:
    async with sessionmaker() as session:
        await save(session, event)
        await session.commit()


async def save(session: AsyncSession, e: dict[str, Any]) -> None:
    """Write the transaction + model_scores rows of one scored event, once."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": e["txn_id"]})
    exists = await session.scalar(
        select(TRANSACTIONS.c.id).where(TRANSACTIONS.c.txn_id == e["txn_id"])
    )
    if exists is not None:
        return
    user_id = uuid.UUID(e["user_id"])
    ts = datetime.fromisoformat(e["ts"])
    payee_id = await _payee(session, e["payee_vpa"])
    device_pk = await _device(session, user_id, e.get("device_id"), ts)
    txn_pk = uuid.UUID(e["id"])
    await session.execute(
        insert(TRANSACTIONS).values(
            id=txn_pk,
            txn_id=e["txn_id"],
            user_id=user_id,
            payee_id=payee_id,
            device_id=device_pk,
            amount=Decimal(str(e["amount"])),
            currency=e["currency"],
            channel=Channel(e["channel"]),
            merchant_category=e["merchant_category"],
            ip=e.get("ip"),
            geo_lat=e.get("geo_lat"),
            geo_lon=e.get("geo_lon"),
            context_json=e["context"],
            ts=ts,
            risk_score=e["risk_score"],
            decision=Decision(e["decision"]),
            action=e["action"],
            model_version=e["model_version"],
            latency_ms=e["latency_ms"],
        )
    )
    scores = e["model_scores"]
    await session.execute(
        insert(MODEL_SCORES).values(
            txn_id=txn_pk,
            transaction_score=scores["transaction"],
            anomaly_score=scores["anomaly"],
            graph_score=scores["graph"],
            social_eng_score=scores["social_eng"],
            meta_score=e["meta_score"],  # before the F2 rule; risk_score is on the txn
            shap_json={"top": e["shap_top"], "rule_hits": e["rule_hits"]},
            explanation=e["explanation"],
            explanation_lang=Language(e["language"]),
        )
    )


async def _payee(session: AsyncSession, vpa: str) -> uuid.UUID:
    payee_id = _payee_ids.get(vpa) or await session.scalar(
        select(PAYEES.c.id).where(PAYEES.c.vpa_or_account == vpa)
    )
    if payee_id is None:
        payee_id = await session.scalar(
            pg_insert(PAYEES)
            .values(id=uuid.uuid4(), vpa_or_account=vpa)
            .on_conflict_do_nothing(index_elements=["vpa_or_account"])
            .returning(PAYEES.c.id)
        ) or await session.scalar(select(PAYEES.c.id).where(PAYEES.c.vpa_or_account == vpa))
    assert payee_id is not None
    _remember(_payee_ids, vpa, payee_id)
    return payee_id


async def _device(
    session: AsyncSession, user_id: uuid.UUID, fingerprint: str | None, ts: datetime
) -> uuid.UUID | None:
    if not fingerprint:
        return None
    seen = _devices.get((user_id, fingerprint))
    if seen is not None and time.monotonic() - seen[1] < DEVICE_TOUCH_S:
        return seen[0]  # known device, last_seen touched less than a minute ago
    device_pk: uuid.UUID | None = await session.scalar(
        update(DEVICES)
        .where(DEVICES.c.user_id == user_id, DEVICES.c.fingerprint == fingerprint)
        .values(last_seen=func.greatest(DEVICES.c.last_seen, ts))
        .returning(DEVICES.c.id)
    )
    if device_pk is None:
        device_pk = uuid.uuid4()
        await session.execute(
            insert(DEVICES).values(id=device_pk, user_id=user_id, fingerprint=fingerprint,
                                   first_seen=ts, last_seen=ts)
        )  # fmt: skip
    _remember(_devices, (user_id, fingerprint), (device_pk, time.monotonic()))
    return device_pk
