"""Graph Explorer reads (spec 04 endpoints 15-16): a node's neighbourhood and the ring list.

The API has no in-memory graph (that lives in the worker), so the neighbourhood is a few indexed
`graph_edges` queries, one per hop. Ids are merged as in graph_refresh: `A:{uuid}` and a mule's
`P:{vpa}` are shown as the one account node `A:{external_id}`.
"""

import contextlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.privacy import mask_ip, mask_phone, mask_vpa
from app.jobs.graph_refresh import WINDOW, account_node
from app.models import GraphEdge, Payee, Ring, User
from app.models.enums import RingStatus
from app.schemas.common import Page
from app.schemas.graph import (
    GraphEdgeOut,
    GraphNode,
    Neighbourhood,
    NodeType,
    RingItem,
    RingRef,
)

MAX_DEPTH = 3
MAX_NODES = 200
EDGES_PER_HOP = 1000
MAX_RINGS = 20
TYPES: dict[str, NodeType] = {"A": "Account", "P": "Payee", "D": "Device", "I": "IP"}


@dataclass
class _Ids:
    """raw graph_edges id <-> displayed node, plus what is needed to label each node."""

    canon: dict[str, str] = field(default_factory=dict)  # raw id -> node id
    raws: dict[str, set[str]] = field(default_factory=dict)  # node id -> raw ids
    label: dict[str, str] = field(default_factory=dict)
    payee_risk: dict[str, float] = field(default_factory=dict)
    known: set[str] = field(default_factory=set)  # nodes with a users / payees row

    def add(self, raw: str, node: str) -> None:
        self.canon[raw] = node
        self.raws.setdefault(node, {node}).add(raw)


async def _add_users(session: AsyncSession, ids: _Ids, users: list[User]) -> None:
    for user in users:
        node = account_node(user)
        ids.add(f"A:{user.id}", node)
        ids.add(node, node)
        ids.label[node] = mask_phone(user.phone) or node
        ids.known.add(node)
    if users:
        rows = await session.execute(
            select(Payee.vpa_or_account, Payee.account_user_id).where(
                Payee.account_user_id.in_([u.id for u in users])
            )
        )
        by_id = {u.id: account_node(u) for u in users}
        for vpa, owner in rows:
            if owner is not None:
                ids.add(f"P:{vpa}", by_id[owner])


async def _resolve(session: AsyncSession, ids: _Ids, raws: set[str]) -> None:
    todo = raws - ids.canon.keys()
    refs = [r[2:] for r in todo if r.startswith("A:")]
    uuids = []
    for ref in refs:
        with contextlib.suppress(ValueError):  # an external id, not a UUID
            uuids.append(uuid.UUID(ref))
    if refs:
        users = list(await session.scalars(
            select(User).where(or_(User.external_id.in_(refs), User.id.in_(uuids)))
        ))  # fmt: skip
        await _add_users(session, ids, users)
    vpas = [r[2:] for r in todo if r.startswith("P:")]
    if vpas:
        rows = (await session.execute(
            select(Payee, User).outerjoin(User, User.id == Payee.account_user_id)
            .where(Payee.vpa_or_account.in_(vpas))
        )).all()  # fmt: skip
        owners: list[User | None] = [u for _, u in rows]
        await _add_users(session, ids, [u for u in owners if u is not None])
        for (payee, _), owner in zip(rows, owners, strict=True):
            if owner is None:  # a shop or outside VPA, not one of our customers
                node = f"P:{payee.vpa_or_account}"
                ids.add(node, node)
                ids.payee_risk[node] = payee.reputation_score
                ids.known.add(node)
    for raw in todo - ids.canon.keys():  # devices, IPs, payees we hold no row for
        ids.add(raw, raw)
    for node in {ids.canon[r] for r in todo} - ids.label.keys():
        ids.label[node] = _label(node)


def _label(node: str) -> str:
    kind, ref = node[0], node[2:]
    if kind == "P":
        return mask_vpa(ref)
    if kind == "D":
        return f"Device …{ref[-4:]}"
    if kind == "I":
        return mask_ip(ref)
    return mask_phone(ref) or ref


async def neighbourhood(
    session: AsyncSession, redis: Redis, node_id: str, depth: int
) -> Neighbourhood:
    if node_id[:2] not in {f"{k}:" for k in TYPES} or len(node_id) < 3:
        raise AppError(404, "NOT_FOUND", "Unknown graph node")
    ids = _Ids()
    await _resolve(session, ids, {node_id})
    start = ids.canon[node_id]
    since = datetime.now(UTC) - WINDOW
    visited, frontier = {start}, {start}
    edges: dict[int, GraphEdgeOut] = {}
    truncated = False
    for _ in range(depth):
        raws = set().union(*(ids.raws[n] for n in frontier))
        touching = or_(GraphEdge.src_id.in_(raws), GraphEdge.dst_id.in_(raws))
        rows = list(await session.scalars(
            select(GraphEdge).where(touching, GraphEdge.ts >= since)
            .order_by(GraphEdge.ts.desc()).limit(EDGES_PER_HOP + 1)
        ))  # fmt: skip
        truncated |= len(rows) > EDGES_PER_HOP
        rows = rows[:EDGES_PER_HOP]
        await _resolve(session, ids, {r.src_id for r in rows} | {r.dst_id for r in rows})
        nxt: set[str] = set()
        for row in rows:
            src, dst = ids.canon[row.src_id], ids.canon[row.dst_id]
            for node in (src, dst):
                if node not in visited:
                    if len(visited) >= MAX_NODES:
                        truncated = True
                        continue
                    visited.add(node)
                    nxt.add(node)
            if src in visited and dst in visited:
                edges[row.id] = GraphEdgeOut(
                    source=src, target=dst, type=row.edge_type,
                    amount=float(row.amount) if row.amount is not None else None, ts=row.ts,
                )  # fmt: skip
        frontier = nxt
        if not frontier:
            break
    if not edges and start not in ids.known:
        raise AppError(404, "NOT_FOUND", "Unknown graph node")

    accounts = sorted(n for n in visited if n.startswith("A:"))
    pipe = redis.pipeline(transaction=False)
    for node in accounts:
        pipe.hmget(f"graph:{node[2:]}", ["graph_score", "flags"])
    cached = dict(zip(accounts, await pipe.execute(), strict=True))
    nodes = []
    for node in sorted(visited):
        score, flags = cached.get(node, (None, None))
        risk = float(score) if score is not None else ids.payee_risk.get(node)
        nodes.append(GraphNode(id=node, type=TYPES[node[0]], label=ids.label[node], risk=risk,
                               flags=[f for f in (flags or "").split(",") if f]))  # fmt: skip

    rings = []
    if accounts:
        found = await session.scalars(
            select(Ring)
            .where(Ring.member_ids.overlap(accounts), Ring.status != RingStatus.DISMISSED)
            .order_by(Ring.score.desc(), Ring.last_seen.desc())
            .limit(MAX_RINGS)
        )
        rings = [RingRef(id=r.id, members=r.member_ids, score=r.score) for r in found]
    return Neighbourhood(nodes=nodes, edges=list(edges.values()), rings=rings, truncated=truncated)


async def list_rings(
    session: AsyncSession,
    status: RingStatus | None,
    detector: str | None,
    min_score: float | None,
    page: int,
    page_size: int,
) -> Page[RingItem]:
    where = []
    if status is not None:
        where.append(Ring.status == status)
    if detector is not None:
        where.append(Ring.detector == detector)
    if min_score is not None:
        where.append(Ring.score >= min_score)
    total = await session.scalar(select(func.count()).select_from(Ring).where(*where)) or 0
    rows = await session.scalars(
        select(Ring).where(*where)
        .order_by(Ring.score.desc(), Ring.last_seen.desc(), Ring.id)
        .offset((page - 1) * page_size).limit(page_size)
    )  # fmt: skip
    items = [
        RingItem(id=r.id, members=r.member_ids, size=len(r.member_ids), score=r.score,
                 detector=r.detector, status=r.status, first_seen=r.first_seen,
                 last_seen=r.last_seen)
        for r in rows
    ]  # fmt: skip
    return Page(items=items, page=page, page_size=page_size, total=total)
