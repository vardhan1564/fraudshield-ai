"""graph_refresh (spec 05 §3): rebuild the payment graph, cache per-account graph features in
Redis `graph:{account_id}` and keep the `rings` table up to date.

The worker runs it every GRAPH_REFRESH_MINUTES. Loading and saving are async; the NetworkX work
runs in a thread so the stream consumers keep going meanwhile.

Accounts are keyed by external id when the user has one (that is what partners send as
`user_id`), else by UUID; the cache is written under both so scoring finds it either way.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Alert, Case, GraphEdge, Payee, Ring, Transaction, User
from app.models.enums import CaseLabel, RingStatus
from app.services.graph_service import Edge, FoundRing, GraphResult, analyse

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=30)
CACHE_TTL_S = 600  # spec 05: graph:{account_id} lives 10 min, refreshed every 5
SAME_RING = 0.5  # Jaccard overlap at which a new finding updates an existing ring
WRITE_BATCH = 1000


@dataclass(frozen=True)
class GraphInput:
    edges: list[Edge]
    created: dict[str, float]  # account node -> opened at (epoch s)
    fraud: set[str]  # confirmed-fraud nodes
    aliases: dict[str, str]  # A:{uuid} and P:{vpa} -> the canonical account node
    uuid_of: dict[str, str]  # canonical account node -> user UUID (second cache key)


@dataclass(frozen=True)
class RefreshSummary:
    edges: int
    accounts: int
    rings_new: int
    rings_updated: int
    seconds: float


def account_node(user: User) -> str:
    return f"A:{user.external_id or user.id}"


async def load(session: AsyncSession, now: datetime) -> GraphInput:
    aliases: dict[str, str] = {}
    created: dict[str, float] = {}
    uuid_of: dict[str, str] = {}
    for user in await session.scalars(select(User)):
        node = account_node(user)
        created[node] = user.created_at.timestamp()
        uuid_of[node] = str(user.id)
        if f"A:{user.id}" != node:
            aliases[f"A:{user.id}"] = node
    by_uuid = {uid: node for node, uid in uuid_of.items()}

    payees = (await session.execute(
        select(Payee.vpa_or_account, Payee.account_user_id, Payee.confirmed_fraud_count)
    )).all()  # fmt: skip
    fraud: set[str] = set()
    for vpa, account_user_id, confirmed in payees:
        if account_user_id is not None and str(account_user_id) in by_uuid:
            aliases[f"P:{vpa}"] = by_uuid[str(account_user_id)]
        if confirmed:
            fraud.add(f"P:{vpa}")
    # The payee of a payment an analyst labelled fraud is a confirmed-fraud node.
    fraud.update(
        f"P:{vpa}"
        for vpa in await session.scalars(
            select(Payee.vpa_or_account)
            .join(Transaction, Transaction.payee_id == Payee.id)
            .join(Alert, Alert.txn_id == Transaction.id)
            .join(Case, Case.alert_id == Alert.id)
            .where(Case.label == CaseLabel.FRAUD)
        )
    )
    for members in await session.scalars(
        select(Ring.member_ids).where(Ring.status == RingStatus.CONFIRMED)
    ):
        fraud.update(members)

    rows = await session.execute(
        select(GraphEdge.edge_type, GraphEdge.src_id, GraphEdge.dst_id, GraphEdge.ts,
               GraphEdge.amount).where(GraphEdge.ts >= now - WINDOW)
    )  # fmt: skip
    edges = [
        Edge(kind.value, src, dst, ts.timestamp(), float(amount or 0))
        for kind, src, dst, ts, amount in rows
    ]
    return GraphInput(edges, created, fraud, aliases, uuid_of)


async def write_cache(redis: Redis, result: GraphResult, uuid_of: dict[str, str]) -> None:
    items = list(result.accounts.items())
    for i in range(0, len(items), WRITE_BATCH):
        pipe = redis.pipeline(transaction=False)
        for node, stats in items[i : i + WRITE_BATCH]:
            fields: dict[FieldT, EncodableT] = {k: v for k, v in stats.redis_fields().items()}
            keys = {node.removeprefix("A:"), uuid_of.get(node, node.removeprefix("A:"))}
            for key in keys:
                pipe.delete(f"graph:{key}")  # no stale flags from the last refresh
                pipe.hset(f"graph:{key}", mapping=fields)
                pipe.expire(f"graph:{key}", CACHE_TTL_S)
        await pipe.execute()


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


async def save_rings(
    session: AsyncSession, found: list[FoundRing], now: datetime
) -> tuple[int, int]:
    """Insert new rings; update the best-overlapping recent ring instead of duplicating it.
    Confirmed and dismissed rings keep their status (a dismissed ring is not raised again)."""
    existing = list(await session.scalars(
        select(Ring).where(Ring.last_seen >= now - WINDOW)
    ))  # fmt: skip
    new = updated = 0
    claimed: set[object] = set()
    for ring in found:
        members = set(ring.members)
        best = max(
            (r for r in existing if r.id not in claimed),
            key=lambda r: jaccard(members, set(r.member_ids)),
            default=None,
        )
        if best is not None and jaccard(members, set(best.member_ids)) >= SAME_RING:
            claimed.add(best.id)
            best.member_ids = list(ring.members)
            best.score = ring.score
            best.detector = ring.detector
            best.last_seen = now
            updated += 1
        else:
            session.add(Ring(member_ids=list(ring.members), score=ring.score,
                             detector=ring.detector, first_seen=now, last_seen=now))  # fmt: skip
            new += 1
    await session.commit()
    return new, updated


async def refresh(
    redis: Redis, sessionmaker: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> RefreshSummary:
    now = now or datetime.now(UTC)
    started = time.perf_counter()
    async with sessionmaker() as session:
        data = await load(session, now)
    result = await asyncio.to_thread(analyse, data.edges, data.created, data.fraud, data.aliases)
    await write_cache(redis, result, data.uuid_of)
    async with sessionmaker() as session:
        new, updated = await save_rings(session, result.rings, now)
    summary = RefreshSummary(
        len(data.edges), len(result.accounts), new, updated,
        round(time.perf_counter() - started, 2),
    )  # fmt: skip
    logger.info("graph refreshed", extra=summary.__dict__)
    return summary
