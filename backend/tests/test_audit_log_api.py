"""GET /audit-log (spec 04 endpoint 27): filters, paging, CSV export without formula injection."""

import csv
import io
import uuid

from fastapi import FastAPI
from httpx import AsyncClient

from app.models import User
from app.models.enums import Role
from app.services import audit
from tests.test_scoring import app, bearer, client, customer, make_user  # noqa: F401

AUDIT = "/api/v1/audit-log"


async def test_filters_paging_and_csv(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    for _ in range(2):
        login = {"email": customer.email, "password": "irrelevant-pw"}
        assert (await client.post("/api/v1/auth/login", json=login)).status_code == 200
    marker = f'=HYPERLINK("http://evil")-{uuid.uuid4().hex[:6]}'
    async with app.state.sessionmaker() as session:
        audit.record(session, actor_id=customer.id, action="test.note", entity="test",
                     entity_id=marker)  # fmt: skip
        await session.commit()
    auth = bearer(app, await make_user(app, role=Role.ADMIN))

    logins = await client.get(
        AUDIT, params={"actor": str(customer.id), "action": "auth.login"}, headers=auth
    )
    assert logins.status_code == 200, logins.text
    page = logins.json()
    assert page["total"] == 2 and all(e["action"] == "auth.login" for e in page["items"])
    assert page["items"][0]["id"] > page["items"][1]["id"]  # newest first
    one = await client.get(AUDIT, params={"actor": str(customer.id), "page_size": 1}, headers=auth)
    assert one.json()["total"] == 3 and len(one.json()["items"]) == 1
    by_entity = await client.get(
        AUDIT, params={"entity": "test", "actor": str(customer.id)}, headers=auth
    )
    assert [e["entity_id"] for e in by_entity.json()["items"]] == [marker]

    r = await client.get(AUDIT, params={"actor": str(customer.id), "format": "csv"}, headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["id", "ts", "actor_id", "action", "entity", "entity_id", "ip", "before",
                       "after"]  # fmt: skip
    assert len(rows) == 4
    assert rows[1][5] == "'" + marker  # a formula would run in Excel: neutralised

    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    assert (await client.get(AUDIT, headers=analyst)).status_code == 403
    assert (await client.get(AUDIT, params={"format": "xml"}, headers=auth)).status_code == 400
