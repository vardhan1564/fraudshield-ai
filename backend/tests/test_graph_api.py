"""GET /graph/entity/{id} and GET /graph/rings (spec 04 endpoints 15-16, §2.5)."""

from urllib.parse import quote

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update

from app.jobs.graph_refresh import refresh
from app.models import Ring
from app.models.enums import RingStatus, Role
from tests.test_graph_refresh import build_ring, rings_with
from tests.test_scoring import app, bearer, client, make_user  # noqa: F401  # pytest fixtures

URL = "/api/v1/graph"


async def test_entity_neighbourhood(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    ring = await build_ring(app)
    await refresh(app.state.redis, app.state.sessionmaker)
    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    col = f"A:{ring.collector.external_id}"

    r = await client.get(f"{URL}/entity/{quote(col)}", headers=analyst)
    assert r.status_code == 200, r.text
    body = r.json()
    nodes = {n["id"]: n for n in body["nodes"]}
    # Depth 2: victims, the two mules, the shared phone, then the cash-out and the scam payee.
    assert {f"A:{u.external_id}" for u in [*ring.layer, *ring.victims]} <= nodes.keys()
    assert f"P:{ring.scam_vpa}" in nodes
    assert nodes[col]["type"] == "Account" and nodes[col]["risk"] == 1.0
    assert "mule_suspect" in nodes[col]["flags"]
    assert nodes[col]["label"] == "98xxxxxx21"  # masked phone
    scam = nodes[f"P:{ring.scam_vpa}"]
    assert scam["type"] == "Payee" and scam["label"].startswith("scxxxx")
    assert not any(n["id"].startswith("P:mule") for n in body["nodes"])  # merged into accounts
    sent = [e for e in body["edges"] if e["type"] == "SENT" and e["target"] == col]
    assert len(sent) == 22 and sent[0]["amount"] == 5_000
    assert [x["members"] for x in body["rings"]] == [ring.members]
    assert body["truncated"] is False

    # The same account by UUID, and by its VPA as a payee, opens the same node.
    for alias in (f"A:{ring.collector.id}", f"P:mule0-{ring.scam_vpa[5:]}"):
        again = (await client.get(f"{URL}/entity/{quote(alias)}?depth=1", headers=analyst)).json()
        assert col in {n["id"] for n in again["nodes"]}
        assert len(again["nodes"]) == 22 + 2 + 1 + 1  # depth 1: victims, mules, phone, itself


async def test_entity_errors(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    customer = await make_user(app)
    node = quote(f"A:{customer.external_id}")
    assert (await client.get(f"{URL}/entity/{node}", headers=analyst)).status_code == 200
    for bad in ("A:U-nobody", "X:what", "D:no-such-phone"):
        r = await client.get(f"{URL}/entity/{quote(bad)}", headers=analyst)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await client.get(f"{URL}/entity/{node}?depth=4", headers=analyst)
    assert r.status_code == 400
    as_customer = bearer(app, customer)
    assert (await client.get(f"{URL}/entity/{node}", headers=as_customer)).status_code == 403
    assert (await client.get(f"{URL}/entity/{node}")).status_code == 401


async def test_rings_list(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    ring = await build_ring(app)
    await refresh(app.state.redis, app.state.sessionmaker)
    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    mine = (await rings_with(app, f"A:{ring.collector.external_id}"))[0]

    r = await client.get(f"{URL}/rings?min_score=0.9&page_size=200", headers=analyst)
    assert r.status_code == 200
    body = r.json()
    item = next(i for i in body["items"] if i["id"] == str(mine.id))
    assert item["members"] == ring.members and item["size"] == 3
    assert item["detector"] == "pass_through" and item["status"] == "suspected"
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True) and min(scores) >= 0.9

    async with app.state.sessionmaker() as session:
        await session.execute(
            update(Ring).where(Ring.id == mine.id).values(status=RingStatus.DISMISSED)
        )
        await session.commit()
    suspected = (await client.get(f"{URL}/rings?status=suspected&page_size=200",
                                  headers=analyst)).json()  # fmt: skip
    assert str(mine.id) not in {i["id"] for i in suspected["items"]}
    other = (await client.get(f"{URL}/rings?detector=gnn", headers=analyst)).json()
    assert other["total"] == 0
    customer = bearer(app, await make_user(app))
    assert (await client.get(f"{URL}/rings", headers=customer)).status_code == 403
