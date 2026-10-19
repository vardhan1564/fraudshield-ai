"""GET /metrics/overview (spec 04 §2.8) on a known set of payments in a window of its own."""

import random
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select

from app.models import Alert, Case, Transaction, User
from app.models.enums import Role
from app.services.llm.template import TemplateProvider
from app.streaming import bus
from app.streaming.consumers import Deps, alert_pusher
from tests.test_scoring import (  # noqa: F401  # pytest fixtures
    PARTNER,
    URL,
    app,
    bearer,
    client,
    customer,
    make_user,
    payment,
)

IST = timezone(timedelta(hours=5, minutes=30))
OVERVIEW = "/api/v1/metrics/overview"


async def test_overview_numbers(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    # A day of its own (the test database keeps old runs), payments at 10:xx IST.
    day = datetime(2031, 1, 1, tzinfo=IST) + timedelta(days=random.randrange(3000))
    at = [day + timedelta(hours=10, minutes=m) for m in range(4)]
    amounts = [1_000.0, 20_000.0, 80_000.0, 90_000.0]  # APPROVE, STEP_UP, HOLD, HOLD
    txn_ids = []
    for ts, amount in zip(at, amounts, strict=True):
        body = payment(customer, amount, timestamp=ts.isoformat())
        assert (await client.post(URL, json=body, headers=PARTNER)).status_code == 200
        txn_ids.append(body["txn_id"])

    redis: Redis = app.state.redis
    deps = Deps(app.state.settings, redis, app.state.sessionmaker, [TemplateProvider()])
    entries = await redis.xrange(app.state.settings.stream_scored)
    events = {e["txn_id"]: e for e in (bus.decode(fields) for _id, fields in entries)}
    for txn_id in txn_ids[2:]:  # the worker's alert + case for each HOLD
        await alert_pusher(events[txn_id], deps)
    async with app.state.sessionmaker() as session:
        case_ids = [
            await session.scalar(
                select(Case.id)
                .join(Alert, Alert.id == Case.alert_id)
                .join(Transaction, Transaction.id == Alert.txn_id)
                .where(Transaction.txn_id == txn_id)
            )
            for txn_id in txn_ids[2:]
        ]
    auth = bearer(app, await make_user(app, role=Role.ANALYST))
    for case_id, label in zip(case_ids, ("legit", "fraud"), strict=True):
        r = await client.patch(f"/api/v1/cases/{case_id}", json={"label": label}, headers=auth)
        assert r.status_code == 200

    window = {"from": day.isoformat(), "to": (day + timedelta(days=1)).isoformat()}
    r = await client.get(OVERVIEW, params=window, headers=auth)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["txn_count"] == 4
    assert m["fraud_caught"] == 2  # the STEP_UP and the fraud HOLD; the legit HOLD is not
    assert m["amount_saved_inr"] == 90_000  # only HOLDs stop money (the fraud one)
    assert m["false_positive_rate"] == 0.5  # 1 legit of 2 labels
    assert m["p95_latency_ms"] is not None and m["p95_latency_ms"] >= 0
    assert len(m["by_hour"]) == 24
    assert m["by_hour"][10] == {"hour": 10, "total": 4, "flagged": 2}
    assert sum(h["total"] for h in m["by_hour"]) == 4
    assert m["by_type"] == [{"scenario": "unknown", "count": 2}]
    assert m["by_city"] == [{"city": "unknown", "flagged": 2}]


async def test_window_rules_and_roles(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    auth = bearer(app, await make_user(app, role=Role.ADMIN))
    default = (await client.get(OVERVIEW, headers=auth)).json()  # last 24 hours
    assert len(default["by_hour"]) == 24 and default["txn_count"] >= 0
    now = datetime.now(IST)
    backwards = {"from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()}
    assert (await client.get(OVERVIEW, params=backwards, headers=auth)).status_code == 400
    too_long = {"from": (now - timedelta(days=100)).isoformat(), "to": now.isoformat()}
    assert (await client.get(OVERVIEW, params=too_long, headers=auth)).status_code == 400
    assert (await client.get(OVERVIEW, headers=bearer(app, customer))).status_code == 403
