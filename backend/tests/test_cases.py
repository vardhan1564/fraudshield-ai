"""Alert queue, case detail and PATCH /cases (spec 04 endpoints 11, 13, 14) + the audit log.

A HOLD is scored through the API (stub models), then the worker's alert_pusher makes the alert
and its case, exactly as in production.
"""

import json
import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select

from app.models import Alert, AuditLog, Case, User
from app.models.enums import AlertStatus, CaseLabel, Role
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


async def held_case(app: FastAPI, client: AsyncClient, user: User) -> dict[str, Any]:  # noqa: F811
    body = payment(user, 80_000.0)
    assert (await client.post(URL, json=body, headers=PARTNER)).json()["decision"] == "HOLD"
    redis: Redis = app.state.redis
    entries = await redis.xrange(app.state.settings.stream_scored)
    event = next(
        e for e in map(lambda m: bus.decode(m[1]), entries) if e["txn_id"] == body["txn_id"]
    )
    deps = Deps(app.state.settings, redis, app.state.sessionmaker, [TemplateProvider()])
    await alert_pusher(event, deps)
    await alert_pusher(event, deps)  # a retry never makes a second alert or case
    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(
                select(Alert, Case)
                .join(Case, Case.alert_id == Alert.id)
                .where(Alert.txn_id == uuid.UUID(event["id"]))
            )
        ).all()
    assert len(rows) == 1
    alert, case = rows[0]
    return {"txn_id": body["txn_id"], "alert_id": str(alert.id), "case_id": str(case.id)}


async def test_alert_queue(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    first = await held_case(app, client, customer)
    second = await held_case(app, client, customer)
    analyst = await make_user(app, role=Role.ANALYST)
    r = await client.get("/api/v1/alerts", headers=bearer(app, analyst))
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["page"] == 1 and page["page_size"] == 50 and page["total"] >= 2
    mine = [a for a in page["items"] if a["txn_id"] in (first["txn_id"], second["txn_id"])]
    assert [a["txn_id"] for a in mine] == [first["txn_id"], second["txn_id"]]  # oldest first
    item = mine[0]
    assert item["case_id"] == first["case_id"] and item["status"] == "open"
    assert item["severity"] == "medium" and item["decision"] == "HOLD" and item["amount"] == 80000
    assert item["top_reasons"][0]["text"] == "Amount ₹80,000"

    small = await client.get(
        "/api/v1/alerts", params={"page_size": 1}, headers=bearer(app, analyst)
    )
    assert len(small.json()["items"]) == 1
    closed = await client.get(
        "/api/v1/alerts", params={"status": "closed"}, headers=bearer(app, analyst)
    )
    assert all(a["status"] == "closed" for a in closed.json()["items"])
    assert (await client.get("/api/v1/alerts", headers=bearer(app, customer))).status_code == 403
    assert (await client.get("/api/v1/alerts")).status_code == 401


async def test_case_detail(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    held = await held_case(app, client, customer)
    analyst = await make_user(app, role=Role.ANALYST)
    r = await client.get(f"/api/v1/cases/{held['case_id']}", headers=bearer(app, analyst))
    assert r.status_code == 200, r.text
    case = r.json()
    assert case["alert_id"] == held["alert_id"] and case["label"] is None
    txn = case["transaction"]
    assert txn["txn_id"] == held["txn_id"] and txn["decision"] == "HOLD"
    assert txn["customer"]["phone"] == "98xxxxxx21"  # masked (spec 08)
    assert case["top_reasons"][0]["feature"] == "amount" and case["explanation"]
    missing = await client.get(f"/api/v1/cases/{uuid.uuid4()}", headers=bearer(app, analyst))
    assert missing.status_code == 404
    other = await client.get(f"/api/v1/cases/{held['case_id']}", headers=bearer(app, customer))
    assert other.status_code == 403


async def test_label_close_audit_stream_and_live_update(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    held = await held_case(app, client, customer)
    analyst = await make_user(app, role=Role.ANALYST)
    redis: Redis = app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe("ws:analysts")

    url = f"/api/v1/cases/{held['case_id']}"
    body = {"status": "closed", "label": "fraud", "notes": "Confirmed with customer by phone"}
    r = await client.patch(url, json=body, headers=bearer(app, analyst))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["label"] == "fraud" and out["status"] == "closed"
    assert out["notes"] == body["notes"] and out["resolved_by"] == str(analyst.id)

    async with app.state.sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.entity_id == held["case_id"]).order_by(AuditLog.id)
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "case.update" and row.entity == "case" and row.actor_id == analyst.id
    assert row.before_json == {"status": "open", "label": None, "notes": None}
    assert row.after_json == {"status": "closed", "label": "fraud", "notes": body["notes"]}

    labeled = [bus.decode(f) for _id, f in await redis.xrange(app.state.settings.stream_cases)]
    event = next(e for e in labeled if e["case_id"] == held["case_id"])
    assert event["label"] == "fraud" and event["previous_label"] is None
    assert event["txn_id"] == held["txn_id"] and event["labeled_by"] == str(analyst.id)
    assert event["payee_vpa"].endswith("@okhdfc")

    msg = None
    for _ in range(50):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if msg is not None:
            break
    await pubsub.aclose()
    assert msg is not None
    assert json.loads(msg["data"]) == {
        "type": "case.updated",
        "data": {"case_id": held["case_id"], "status": "closed", "label": "fraud"},
    }

    # Same label again: nothing changes, so no audit row and no second event.
    again = await client.patch(url, json={"label": "fraud"}, headers=bearer(app, analyst))
    assert again.status_code == 200
    labeled = [bus.decode(f) for _id, f in await redis.xrange(app.state.settings.stream_cases)]
    assert sum(e["case_id"] == held["case_id"] for e in labeled) == 1
    async with app.state.sessionmaker() as session:
        count = len((await session.scalars(
            select(AuditLog).where(AuditLog.entity_id == held["case_id"]))).all())  # fmt: skip
    assert count == 1


async def test_assign_and_rules(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    held = await held_case(app, client, customer)
    analyst = await make_user(app, role=Role.ANALYST)
    url, auth = f"/api/v1/cases/{held['case_id']}", bearer(app, analyst)

    r = await client.patch(url, json={"assigned_to": str(analyst.id)}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "assigned"
    assert r.json()["assigned_to"] == str(analyst.id)

    for bad in (
        {"status": "closed"},  # no label yet
        {"assigned_to": str(customer.id)},  # customers can't own a case
        {},
        {"label": "maybe"},
        {"surprise": 1},
    ):
        resp = await client.patch(url, json=bad, headers=auth)
        assert resp.status_code == 400, bad
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    r = await client.patch(url, json={"assigned_to": None, "label": "legit"}, headers=auth)
    assert r.json()["assigned_to"] is None and r.json()["label"] == "legit"
    assert (await client.patch(url, json={"status": "closed"}, headers=auth)).status_code == 200
    async with app.state.sessionmaker() as session:
        case = await session.get(Case, uuid.UUID(held["case_id"]))
        assert case is not None and case.label == CaseLabel.LEGIT
        alert = await session.get(Alert, uuid.UUID(held["alert_id"]))
        assert alert is not None and alert.status == AlertStatus.CLOSED
    staff_only = await client.patch(url, json={"notes": "x"}, headers=bearer(app, customer))
    assert staff_only.status_code == 403
    missing = await client.patch(f"/api/v1/cases/{uuid.uuid4()}", json={"notes": "x"}, headers=auth)
    assert missing.status_code == 404


async def test_escalate_raises_severity_and_requeues(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    held = await held_case(app, client, customer)
    analyst = await make_user(app, role=Role.ANALYST)
    url, auth = f"/api/v1/cases/{held['case_id']}", bearer(app, analyst)
    await client.patch(url, json={"severity": "low", "assigned_to": str(analyst.id)}, headers=auth)

    note = "Needs a second look"
    escalate = {"severity": "high", "status": "open", "assigned_to": None, "notes": note}
    r = await client.patch(url, json=escalate, headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["severity"], out["status"], out["assigned_to"]) == ("high", "open", None)
    queue = (await client.get("/api/v1/alerts", headers=auth)).json()["items"]
    assert next(a for a in queue if a["case_id"] == held["case_id"])["severity"] == "high"
    async with app.state.sessionmaker() as session:
        row = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.entity_id == held["case_id"])
                .order_by(AuditLog.id.desc())
            )
        ).first()
    assert row is not None
    assert row.before_json == {"severity": "low", "status": "assigned",
                               "assigned_to": str(analyst.id), "notes": None}  # fmt: skip
    assert row.after_json == {"severity": "high", "status": "open", "assigned_to": None,
                              "notes": note}  # fmt: skip
    bad = await client.patch(url, json={"severity": "urgent"}, headers=auth)
    assert bad.status_code == 400


async def test_login_and_register_are_audited(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    ok = await client.post(
        "/api/v1/auth/login", json={"email": customer.email, "password": "irrelevant-pw"}
    )
    assert ok.status_code == 200
    bad = await client.post(
        "/api/v1/auth/login", json={"email": customer.email, "password": "wrong-password"}
    )
    assert bad.status_code == 401
    email = f"new-{uuid.uuid4().hex[:8]}@test.local"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"name": "New", "phone": "9876543210", "email": email, "password": "long-enough-pw"},
    )
    assert reg.status_code == 201, reg.text

    async with app.state.sessionmaker() as session:
        mine = (
            await session.scalars(
                select(AuditLog).where(AuditLog.entity_id == str(customer.id)).order_by(AuditLog.id)
            )
        ).all()
        new_user = await session.scalar(select(User).where(User.email == email))
        assert new_user is not None
        registered = await session.scalar(
            select(AuditLog).where(AuditLog.entity_id == str(new_user.id))
        )
    assert [(r.action, r.actor_id) for r in mine] == [
        ("auth.login", customer.id),
        ("auth.login_failed", None),
    ]
    assert all(r.before_json is None and r.after_json is None for r in mine)  # nothing secret
    assert registered is not None and registered.action == "auth.register"
    assert registered.after_json == {"role": "customer", "language": "en"}
