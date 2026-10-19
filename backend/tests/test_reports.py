"""POST /reports and GET /payees/{vpa}/reputation (spec 04 endpoints 17-18, spec 01 F7)."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select

from app.models import AuditLog, Payee, User
from app.models.enums import Role
from app.services.reputation import level, score
from app.streaming import bus
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

REPORTS = "/api/v1/reports"


def vpa() -> str:
    return f"scam.{uuid.uuid4().hex[:8]}@okbank"


def report(payee: str, **extra: object) -> dict[str, object]:
    return {"payee_vpa": payee, "scam_type": "fake_support_refund",
            "description": "Called as bank support", **extra}  # fmt: skip


@pytest.mark.parametrize(
    ("reports", "fraud", "flag", "expected", "band"),
    [(0, 0, False, 0.0, "low"), (2, 0, False, 0.2, "low"), (3, 0, False, 0.3, "medium"),
     (7, 0, False, 0.7, "medium"), (2, 1, False, 0.7, "medium"), (2, 1, True, 1.0, "high"),
     (20, 0, False, 1.0, "high")],
)  # fmt: skip
def test_f7_formula(reports: int, fraud: int, flag: bool, expected: float, band: str) -> None:
    value = score(reports, fraud, flag)
    assert value == pytest.approx(expected) and level(value) == band


async def test_report_updates_everything(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    payee = vpa().upper()  # stored lower-case
    r = await client.post(
        REPORTS, json=report(payee, phone="+91 98123 45621"), headers=bearer(app, customer)
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["payee_vpa"] == payee.lower() and out["report_count"] == 1
    assert out["reputation_score"] == pytest.approx(0.1) and out["risk_level"] == "low"

    redis: Redis = app.state.redis
    assert await redis.hget(f"rep:{payee.lower()}", "report_count") == "1"  # scoring sees it
    events = [bus.decode(f) for _id, f in await redis.xrange(app.state.settings.stream_reports)]
    assert events[-1]["payee_vpa"] == payee.lower() and events[-1]["report_count"] == 1
    async with app.state.sessionmaker() as session:
        row = await session.scalar(select(Payee).where(Payee.vpa_or_account == payee.lower()))
        assert row is not None and row.report_count == 1
        audit_row = await session.scalar(
            select(AuditLog).where(AuditLog.entity_id == out["report_id"])
        )
    assert audit_row is not None and audit_row.action == "report.create"
    assert audit_row.actor_id == customer.id and "phone" not in (audit_row.after_json or {})

    again = await client.post(REPORTS, json=report(payee), headers=bearer(app, customer))
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_REPORTED"


async def test_five_reports_force_a_warning(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    payee = vpa()
    small = payment(customer, 1_000.0, payee_vpa=payee)  # risk 0.01: APPROVE on the model alone
    for n in range(1, 6):
        reporter = await make_user(app)
        r = await client.post(REPORTS, json=report(payee), headers=bearer(app, reporter))
        assert r.status_code == 201 and r.json()["report_count"] == n
    assert r.json()["risk_level"] == "medium"
    scored = (await client.post(URL, json=small, headers=PARTNER)).json()
    assert scored["decision"] == "STEP_UP" and scored["action"] == "SCAM_COACH"
    assert scored["top_reasons"][0]["text"] == "Payee reported by 5 users"


@pytest.mark.parametrize(
    "bad",
    [{"phone": "12345"}, {"scam_type": "spam"}, {"evidence_url": "not a url"},
     {"payee_vpa": "x"}, {"surprise": 1}],
)  # fmt: skip
async def test_bad_reports_are_400(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
    bad: dict[str, object],
) -> None:
    r = await client.post(REPORTS, json={**report(vpa()), **bad}, headers=bearer(app, customer))
    assert r.status_code == 400


async def test_only_customers_report(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    analyst = await make_user(app, role=Role.ANALYST)
    assert (
        await client.post(REPORTS, json=report(vpa()), headers=bearer(app, analyst))
    ).status_code == 403
    assert (await client.post(REPORTS, json=report(vpa()))).status_code == 401


async def test_reputation_lookup(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    payee = vpa()
    await client.post(
        REPORTS, json=report(payee, phone="9812345621"), headers=bearer(app, customer)
    )
    other = await make_user(app)
    await client.post(
        REPORTS, json=report(payee, scam_type="digital_arrest"), headers=bearer(app, other)
    )
    url = f"/api/v1/payees/{payee}/reputation"

    mine = (await client.get(url, headers=bearer(app, customer))).json()
    assert mine["known"] is True and mine["report_count"] == 2
    assert mine["reputation_score"] == pytest.approx(0.2) and mine["risk_level"] == "low"
    assert mine["reports_by_type"] == {"digital_arrest": 1, "fake_support_refund": 1}
    assert mine["recent_reports"] is None  # customers don't see other people's reports

    staff = (
        await client.get(url, headers=bearer(app, await make_user(app, role=Role.ANALYST)))
    ).json()
    assert len(staff["recent_reports"]) == 2
    assert {r["phone"] for r in staff["recent_reports"]} == {"98xxxxxx21", None}  # masked

    assert (await client.get(url, headers=PARTNER)).status_code == 200  # partners may look up
    unknown = (await client.get(f"/api/v1/payees/{vpa()}/reputation", headers=PARTNER)).json()
    assert unknown["known"] is False and unknown["risk_level"] == "low"
    assert (await client.get(url)).status_code == 401
