"""GET /transactions (filters, paging) and GET /transactions/{id} (spec 04 endpoints 7-8)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.models import User
from app.models.enums import Role
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

LIST = "/api/v1/transactions"


async def three_payments(client: AsyncClient, user: User) -> list[str]:  # noqa: F811
    """APPROVE, STEP_UP and HOLD with the stub models (risk = amount / 100k), oldest first."""
    start = datetime.now(UTC) - timedelta(minutes=10)
    ids = []
    for i, amount in enumerate((1_000.0, 20_000.0, 80_000.0)):
        body = payment(user, amount, timestamp=(start + timedelta(minutes=i)).isoformat())
        assert (await client.post(URL, json=body, headers=PARTNER)).status_code == 200
        ids.append(body["txn_id"])
    return ids


async def test_list_filters_and_paging(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    ids = await three_payments(client, customer)
    auth = bearer(app, await make_user(app, role=Role.ANALYST))

    async def txn_ids(**params: object) -> list[str]:
        r = await client.get(LIST, params={"user_id": customer.external_id, **params}, headers=auth)
        assert r.status_code == 200, r.text
        return [t["txn_id"] for t in r.json()["items"]]

    assert await txn_ids() == ids[::-1]  # newest first
    assert await txn_ids(decision="HOLD") == [ids[2]]
    assert await txn_ids(min_risk=0.15) == [ids[2], ids[1]]
    assert await txn_ids(channel="CARD") == []
    assert await txn_ids(user_id=str(customer.id)) == ids[::-1]  # UUID works too
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert await txn_ids(**{"from": later}) == []
    page = (await client.get(LIST, params={"user_id": customer.external_id, "page_size": 2,
                                           "page": 2}, headers=auth)).json()  # fmt: skip
    assert page["total"] == 3 and [t["txn_id"] for t in page["items"]] == [ids[0]]
    item = page["items"][0]
    assert item["decision"] == "APPROVE" and item["payee_vpa"].endswith("@okhdfc")
    assert item["customer_external_id"] == customer.external_id

    admin = bearer(app, await make_user(app, role=Role.ADMIN))
    assert (await client.get(LIST, headers=admin)).status_code == 200
    assert (await client.get(LIST, headers=bearer(app, customer))).status_code == 403
    bad = await client.get(LIST, params={"min_risk": 2}, headers=auth)
    assert bad.status_code == 400


async def test_detail(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    ids = await three_payments(client, customer)
    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    r = await client.get(f"{LIST}/{ids[2]}", headers=analyst)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["decision"] == "HOLD"
    assert detail["model_scores"]["transaction"] == pytest.approx(0.8)  # stored as real (float4)
    assert detail["top_reasons"][0]["text"] == "Amount ₹80,000"
    assert detail["device"]["fingerprint"] == "D-test" and detail["ip"] == "103.21.44.10"
    assert detail["context"]["on_call"] is False and "behaviour" in detail["context"]

    by_uuid = await client.get(f"{LIST}/{detail['id']}", headers=analyst)
    assert by_uuid.json()["txn_id"] == ids[2]
    mine = await client.get(f"{LIST}/{ids[0]}", headers=bearer(app, customer))
    assert mine.status_code == 200 and mine.json()["decision"] == "APPROVE"
    stranger = bearer(app, await make_user(app))
    assert (await client.get(f"{LIST}/{ids[0]}", headers=stranger)).status_code == 404
    assert (await client.get(f"{LIST}/{uuid.uuid4()}", headers=analyst)).status_code == 404
    assert (await client.get(f"{LIST}/{ids[0]}")).status_code == 401
