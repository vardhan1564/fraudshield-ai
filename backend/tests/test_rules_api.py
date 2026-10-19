"""Rules CRUD (spec 04 endpoint 23): validation, audit, and live effect on scoring in every
API process (rules:version)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AuditLog, User
from app.models.enums import Role
from app.schemas.rules import check_condition
from app.services.rules import RuleCache
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

RULES = "/api/v1/rules"
FUEL = {"all": [{"field": "amount", "op": ">", "value": 1500},
                {"field": "merchant_category", "op": "==", "value": "FUEL"}]}  # fmt: skip


@pytest.mark.parametrize(
    "condition",
    [
        {"all": [{"field": "amount", "op": ">", "value": 50000}]},
        {"any": [{"field": "payee_is_new", "op": "==", "value": True},
                 {"field": "txn_count_1h", "op": ">=", "value": 5}]},
        FUEL,
    ],
)  # fmt: skip
def test_good_conditions(condition: dict[str, Any]) -> None:
    assert check_condition(condition) == condition


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        ({"all": []}, "1 to 20"),
        ({"all": [], "any": []}, "exactly one"),
        ({"some": []}, "exactly one"),
        ({"all": [{"field": "amonut", "op": ">", "value": 1}]}, "unknown feature"),
        ({"all": [{"field": "amount", "op": "~", "value": 1}]}, "op must be"),
        ({"all": [{"field": "amount", "op": ">", "value": "big"}]}, "needs a number"),
        ({"all": [{"field": "amount", "op": ">", "value": True}]}, "needs a number"),
        ({"all": [{"field": "is_vpn", "op": ">", "value": True}]}, "true/false"),
        ({"all": [{"field": "is_vpn", "op": "==", "value": 1}]}, "true/false"),
        ({"all": [{"field": "merchant_category", "op": "==", "value": "CASINO"}]}, "one of"),
        ({"all": [{"field": "amount", "op": ">"}]}, "exactly"),
    ],
)
def test_bad_conditions(condition: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_condition(condition)


def rule(name: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "condition_json": FUEL, "action": "HOLD", "priority": 5, **extra}


async def test_crud_audit_and_live_effect(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    admin = await make_user(app, role=Role.ADMIN)
    auth = bearer(app, admin)
    name = f"test fuel {uuid.uuid4().hex[:6]}"

    async def decision() -> str:
        body = payment(customer, 2_000.0, merchant_category="FUEL")  # model alone: APPROVE
        return str((await client.post(URL, json=body, headers=PARTNER)).json()["decision"])

    created = await client.post(RULES, json=rule(name), headers=auth)
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    try:
        assert created.json()["updated_by"] == str(admin.id)
        assert await decision() == "HOLD"  # this process reloaded at once

        app.state.rules = RuleCache()  # another API process, still on the old rules ...
        assert await decision() == "HOLD"  # ... reloads on its next payment (rules:version)

        dup = await client.post(RULES, json=rule(name), headers=auth)
        assert dup.status_code == 409 and dup.json()["error"]["code"] == "RULE_NAME_TAKEN"
        paused = await client.put(f"{RULES}/{rule_id}", json=rule(name, is_active=False),
                                  headers=auth)  # fmt: skip
        assert paused.status_code == 200 and paused.json()["is_active"] is False
        assert await decision() == "APPROVE"

        inactive = (await client.get(RULES, params={"is_active": False}, headers=auth)).json()
        assert rule_id in [r["id"] for r in inactive["items"]]
        active = (await client.get(RULES, params={"is_active": True}, headers=auth)).json()
        assert rule_id not in [r["id"] for r in active["items"]]
    finally:
        deleted = await client.delete(f"{RULES}/{rule_id}", headers=auth)
    assert deleted.status_code == 204
    assert (
        await client.put(f"{RULES}/{rule_id}", json=rule(name), headers=auth)
    ).status_code == 404

    async with app.state.sessionmaker() as session:
        actions = (
            await session.scalars(
                select(AuditLog.action).where(AuditLog.entity_id == rule_id).order_by(AuditLog.id)
            )
        ).all()
    assert actions == ["rule.create", "rule.update", "rule.delete"]


async def test_bad_rules_and_roles(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    auth = bearer(app, await make_user(app, role=Role.ADMIN))
    bad_condition = rule("x", condition_json={"all": [{"field": "nope", "op": ">", "value": 1}]})
    r = await client.post(RULES, json=bad_condition, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (
        await client.post(RULES, json=rule("x", action="BLOCK"), headers=auth)
    ).status_code == 400
    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    assert (await client.get(RULES, headers=analyst)).status_code == 403
    assert (await client.post(RULES, json=rule("x"), headers=analyst)).status_code == 403
    assert (await client.delete(f"{RULES}/{uuid.uuid4()}", headers=auth)).status_code == 404
