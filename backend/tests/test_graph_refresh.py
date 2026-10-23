"""graph_refresh (spec 05 §3) against the test database and Redis db 13: a small mule ring in
graph_edges -> graph:{account_id} hashes and one rings row that later refreshes update."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import any_, literal, select, update

from app.jobs.graph_refresh import CACHE_TTL_S, refresh
from app.models import GraphEdge, Payee, Ring, User
from app.models.enums import EdgeType, RingStatus
from tests.test_scoring import app, make_user  # noqa: F401  # pytest fixtures


@dataclass
class MiniRing:
    collector: User
    layer: list[User]  # two mules the collector pays
    victims: list[User]
    scam_vpa: str  # a payee with a confirmed fraud case; victims[0] paid it

    @property
    def members(self) -> list[str]:
        return sorted(f"A:{u.external_id}" for u in [self.collector, *self.layer])


async def build_ring(app: FastAPI) -> MiniRing:  # noqa: F811
    """22 victims pay the collector's VPA within 20 min; it forwards 90% to two mules within the
    hour, and they cash out. The three mules share a phone. The collector opened today."""
    tag = uuid.uuid4().hex[:8]
    collector = await make_user(app)
    layer = [await make_user(app), await make_user(app)]
    victims = [await make_user(app) for _ in range(22)]
    now = datetime.now(UTC) - timedelta(hours=2)
    vpa = {u.id: f"mule{i}-{tag}@ybl" for i, u in enumerate([collector, *layer])}
    scam = f"scam-{tag}@ybl"

    def edge(kind: EdgeType, src: str, dst: str, minutes: float, amount: float | None = None
             ) -> GraphEdge:  # fmt: skip
        money = None if amount is None else Decimal(str(amount))
        return GraphEdge(edge_type=kind, src_id=src, dst_id=dst, amount=money,
                         ts=now + timedelta(minutes=minutes),
                         txn_id=f"G-{tag}-{uuid.uuid4().hex[:6]}")  # fmt: skip

    rows = [edge(EdgeType.SENT, f"A:{v.external_id}", f"P:{vpa[collector.id]}", i, 5_000)
            for i, v in enumerate(victims)]  # fmt: skip
    rows.append(edge(EdgeType.SENT, f"A:{victims[0].external_id}", f"P:{scam}", 0, 900))
    for i, mule in enumerate(layer):
        rows.append(edge(EdgeType.SENT, f"A:{collector.external_id}", f"P:{vpa[mule.id]}",
                         30 + i, 49_500))  # fmt: skip
        # The scoring API may name an account by UUID: graph_refresh must merge it.
        rows.append(edge(EdgeType.SENT, f"A:{mule.id}", f"P:cash-{tag}@paytm", 50 + i, 48_000))
    for mule in [collector, *layer]:
        rows.append(edge(EdgeType.USED_DEVICE, f"A:{mule.external_id}", f"D:phone-{tag}", 0))
    async with app.state.sessionmaker() as session:
        session.add_all([Payee(vpa_or_account=v, account_user_id=uid) for uid, v in vpa.items()])
        session.add(Payee(vpa_or_account=scam, confirmed_fraud_count=1))
        session.add_all(rows)
        await session.execute(  # the mules opened their accounts yesterday
            update(User).where(User.id.in_(vpa)).values(created_at=now - timedelta(days=1))
        )
        await session.commit()
    return MiniRing(collector, layer, victims, scam)


async def rings_with(app: FastAPI, node: str) -> list[Ring]:  # noqa: F811
    async with app.state.sessionmaker() as session:
        found = await session.scalars(select(Ring).where(literal(node) == any_(Ring.member_ids)))
        return list(found)


async def test_refresh_writes_cache_and_rings(app: FastAPI) -> None:  # noqa: F811
    ring = await build_ring(app)
    summary = await refresh(app.state.redis, app.state.sessionmaker)
    assert summary.edges >= 29 and summary.accounts >= 25

    redis: Redis = app.state.redis
    col = await redis.hgetall(f"graph:{ring.collector.external_id}")
    assert set(col["flags"].split(",")) == {"fan_in", "pass_through", "shared_device",
                                            "new_account_burst", "mule_suspect"}  # fmt: skip
    assert float(col["graph_score"]) == 1.0
    assert float(col["graph_degree"]) == 22 + 2 + 1
    assert 0 < await redis.ttl(f"graph:{ring.collector.external_id}") <= CACHE_TTL_S
    # Also under the UUID, and the UUID-named mule was merged into its account.
    assert await redis.hget(f"graph:{ring.collector.id}", "graph_score") == col["graph_score"]
    mule = await redis.hgetall(f"graph:{ring.layer[1].external_id}")
    assert "mule_suspect" in mule["flags"].split(",")
    assert float(mule["hops_to_fraud"]) == 3  # mule - collector - victims[0] - scam payee
    victim = await redis.hgetall(f"graph:{ring.victims[0].external_id}")
    assert victim["flags"] == "" and float(victim["hops_to_fraud"]) == 1  # paid the scam payee

    found = await rings_with(app, f"A:{ring.collector.external_id}")
    assert len(found) == 1
    assert found[0].member_ids == ring.members
    assert found[0].status == RingStatus.SUSPECTED and found[0].score == 1.0


async def test_refresh_updates_rings_and_keeps_dismissals(app: FastAPI) -> None:  # noqa: F811
    ring = await build_ring(app)
    node = f"A:{ring.collector.external_id}"
    await refresh(app.state.redis, app.state.sessionmaker)
    first = (await rings_with(app, node))[0]

    later = datetime.now(UTC) + timedelta(minutes=5)
    await refresh(app.state.redis, app.state.sessionmaker, now=later)
    again = await rings_with(app, node)
    assert [r.id for r in again] == [first.id]  # updated, not duplicated
    assert again[0].last_seen > first.last_seen and again[0].first_seen == first.first_seen

    async with app.state.sessionmaker() as session:
        await session.execute(
            update(Ring).where(Ring.id == first.id).values(status=RingStatus.DISMISSED)
        )
        await session.commit()
    await refresh(app.state.redis, app.state.sessionmaker)
    after = await rings_with(app, node)
    assert [(r.id, r.status) for r in after] == [(first.id, RingStatus.DISMISSED)]
