"""services/persist.py: the worker saves each scored payment exactly once (T3.9)."""

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.models import Device, ModelScore, Transaction, User
from app.services import persist
from app.streaming import bus
from tests.test_scoring import (  # noqa: F401  # pytest fixtures
    PARTNER,
    URL,
    app,
    customer,
    payment,
)


async def test_concurrent_saves_write_once(app: FastAPI, customer: User) -> None:  # noqa: F811
    body = payment(customer, 80_000.0, device_id=f"D-{customer.external_id}")
    # A plain client: nothing saves the rows here, so we can race the savers ourselves.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as plain:
        assert (await plain.post(URL, json=body, headers=PARTNER)).status_code == 200
    redis: Redis = app.state.redis
    event = next(
        e
        for e in (bus.decode(f) for _i, f in await redis.xrange(app.state.settings.stream_scored))
        if e["txn_id"] == body["txn_id"]
    )
    async with app.state.sessionmaker() as session:
        assert (
            await session.scalar(select(func.count()).where(Transaction.txn_id == body["txn_id"]))
            == 0
        )

    # txn_writer, alert_pusher and llm_explainer may all try at the same moment.
    await asyncio.gather(*(persist.ensure_saved(app.state.sessionmaker, event) for _ in range(4)))

    async with app.state.sessionmaker() as session:
        txn = await session.scalar(select(Transaction).where(Transaction.txn_id == body["txn_id"]))
        assert txn is not None and str(txn.id) == event["id"] and txn.latency_ms is not None
        assert float(txn.amount) == 80_000 and txn.decision.value == "HOLD"
        scores = await session.get(ModelScore, txn.id)
        assert scores is not None and scores.shap_json["top"][0]["feature"] == "amount"
        devices = await session.scalar(
            select(func.count()).where(Device.user_id == customer.id,
                                       Device.fingerprint == body["device_id"])
        )  # fmt: skip
    assert devices == 1
