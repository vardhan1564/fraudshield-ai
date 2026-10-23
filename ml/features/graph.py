"""Payment-graph statistics (spec 01 F3, spec 03 §2 graph group, spec 05 §3). Pure: no I/O.

Shared by training (ml/models/gnn.py builds point-in-time snapshots from the simulator) and
serving (the worker's graph_refresh job), so a node's features are computed one way only.

Node ids are spec 05's `A:{user}`, `P:{vpa}`, `D:{fingerprint}`, `I:{ip}`; `aliases` maps a
payee to its customer account (`P:{vpa}` -> `A:{user}`), so money paid to a mule's VPA arrives
at the mule's account. The four rule detectors are thresholds on the per-account statistics.
"""

from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import networkx as nx

HOUR = 3600
DAY = 24 * HOUR

FAN_IN_SENDERS = 20  # > 20 distinct senders in 24 h
PASS_THROUGH_SHARE = 0.8  # > 80% of incoming forwarded within 1 h
PASS_THROUGH_MIN_INR = 5_000.0  # ignore accounts that received only small change
SHARED_DEVICE_ACCOUNTS = 3  # >= 3 accounts on one device
NEW_ACCOUNT_DAYS = 7  # account < 7 days old ...
NEW_ACCOUNT_INCOMING = 10  # ... with > 10 incoming payments
NO_PATH_HOPS = 10  # hops_to_fraud when no confirmed-fraud node is within reach
SEED = 42  # Louvain is randomised; fixed so refreshes are stable

DETECTORS = ("fan_in", "pass_through", "shared_device", "new_account_burst")


@dataclass(frozen=True, slots=True)
class Edge:
    kind: str  # SENT | USED_DEVICE | LOGGED_FROM
    src: str
    dst: str
    ts: float  # epoch seconds
    amount: float = 0.0


def is_account(node: str) -> bool:
    return node.startswith("A:")


# --- per-account statistics behind the rule detectors ------------------------------------------


def max_senders_24h(sent: Sequence[Edge]) -> dict[str, int]:
    """Most distinct senders an account received from within any 24 h (accounts only)."""
    incoming: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for e in sent:
        if is_account(e.dst):
            incoming[e.dst].append((e.ts, e.src))
    best: dict[str, int] = {}
    for node, events in incoming.items():
        events.sort()
        window: Counter[str] = Counter()
        lo, top = 0, 0
        for ts, sender in events:
            window[sender] += 1
            while events[lo][0] <= ts - DAY:
                old = events[lo][1]
                window[old] -= 1
                if not window[old]:
                    del window[old]
                lo += 1
            top = max(top, len(window))
        best[node] = top
    return best


def forwarded_share(incoming: Iterable[tuple[float, float]], outgoing: Iterable[tuple[float, float]]
                    ) -> tuple[float, float]:  # fmt: skip
    """(total received, share of it paid on within 1 h). Oldest money is forwarded first;
    money not forwarded within the hour counts as kept."""
    events = sorted([(t, 0, a) for t, a in incoming] + [(t, 1, a) for t, a in outgoing])
    pending: deque[list[float]] = deque()  # [received at, amount not yet forwarded]
    total = forwarded = 0.0
    for ts, kind, amount in events:  # at equal times money arrives before it leaves
        if kind == 0:
            total += amount
            pending.append([ts, amount])
            continue
        while pending and pending[0][0] < ts - HOUR:
            pending.popleft()
        left = amount
        while pending and left > 0:
            take = min(left, pending[0][1])
            pending[0][1] -= take
            forwarded += take
            left -= take
            if pending[0][1] <= 0:
                pending.popleft()
    return total, (forwarded / total if total else 0.0)


def forwarding(sent: Sequence[Edge]) -> dict[str, tuple[float, float]]:
    """(total received, share forwarded within 1 h) per account that received money."""
    incoming: dict[str, list[tuple[float, float]]] = defaultdict(list)
    outgoing: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in sent:
        incoming[e.dst].append((e.ts, e.amount))
        outgoing[e.src].append((e.ts, e.amount))
    return {
        node: forwarded_share(events, outgoing.get(node, ()))
        for node, events in incoming.items()
        if is_account(node)
    }


def device_accounts(used: Sequence[Edge]) -> dict[str, int]:
    """Per account: the most accounts on any one of its devices (itself included)."""
    accounts: dict[str, set[str]] = defaultdict(set)
    for e in used:
        accounts[e.dst].add(e.src)
    out: dict[str, int] = {}
    for group in accounts.values():
        for a in group:
            out[a] = max(out.get(a, 0), len(group))
    return out


def young_incoming(sent: Sequence[Edge], created: Mapping[str, float]) -> dict[str, int]:
    """Payments an account received while it was < NEW_ACCOUNT_DAYS old."""
    return dict(
        Counter(
            e.dst
            for e in sent
            if e.dst in created and 0 <= e.ts - created[e.dst] < NEW_ACCOUNT_DAYS * DAY
        )
    )


# --- the four rule detectors (spec 01 F3) ------------------------------------------------------


def _fan_in(senders_24h: Mapping[str, int]) -> set[str]:
    return {n for n, k in senders_24h.items() if k > FAN_IN_SENDERS}


def _pass_through(forwarded: Mapping[str, tuple[float, float]]) -> set[str]:
    return {
        n
        for n, (total, share) in forwarded.items()
        if total >= PASS_THROUGH_MIN_INR and share > PASS_THROUGH_SHARE
    }


def _shared_device(per_account: Mapping[str, int]) -> set[str]:
    return {a for a, k in per_account.items() if k >= SHARED_DEVICE_ACCOUNTS}


def _new_account_burst(young: Mapping[str, int]) -> set[str]:
    return {n for n, k in young.items() if k > NEW_ACCOUNT_INCOMING}


def fan_in(sent: Sequence[Edge]) -> set[str]:
    """Accounts paid by more than FAN_IN_SENDERS distinct senders within any 24 h.
    Only customer accounts: shops are meant to have many payers."""
    return _fan_in(max_senders_24h(sent))


def pass_through(sent: Sequence[Edge]) -> set[str]:
    return _pass_through(forwarding(sent))


def shared_device(used: Sequence[Edge]) -> set[str]:
    return _shared_device(device_accounts(used))


def new_account_burst(sent: Sequence[Edge], created: Mapping[str, float]) -> set[str]:
    return _new_account_burst(young_incoming(sent, created))


# --- graph metrics -----------------------------------------------------------------------------


def hops_to(graph: nx.Graph, sources: set[str]) -> dict[str, int]:
    """Undirected hops from each node to the nearest source (sources themselves are 0)."""
    hops = dict.fromkeys(sources & set(graph), 0)
    frontier = list(hops)
    depth = 0
    while frontier and depth < NO_PATH_HOPS - 1:
        depth += 1
        nxt = []
        for node in frontier:
            for nb in graph[node]:
                if nb not in hops:
                    hops[nb] = depth
                    nxt.append(nb)
        frontier = nxt
    return hops


def communities(money: nx.Graph) -> dict[str, int]:
    """Louvain community id per node of the (undirected) SENT graph."""
    if money.number_of_edges() == 0:
        return {}
    groups = nx.community.louvain_communities(money, weight="weight", seed=SEED)
    return {node: i for i, group in enumerate(groups) for node in group}


@dataclass
class GraphStats:
    """Everything one refresh (or one training snapshot) knows about the graph."""

    whole: nx.Graph  # every node and relation, multi-edges collapsed
    sent: list[Edge]
    used: list[Edge]
    created: dict[str, float]
    pagerank: dict[str, float]
    community: dict[str, int]
    community_ratio: dict[int, float]  # community id -> share of confirmed-fraud nodes
    hops: dict[str, int]
    senders_24h: dict[str, int]
    forwarded: dict[str, tuple[float, float]]
    device_accounts: dict[str, int]
    young_incoming: dict[str, int]
    flagged: dict[str, set[str]] = field(default_factory=dict)  # detector -> accounts

    def flags_of(self, node: str) -> tuple[str, ...]:
        return tuple(d for d in DETECTORS if node in self.flagged[d])

    def node_ratio(self, node: str) -> float:
        return self.community_ratio.get(self.community.get(node, -1), 0.0)


def compute(
    edges: Iterable[Edge],
    created: Mapping[str, float] | None = None,
    fraud: Iterable[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> GraphStats:
    """Statistics, metrics and detector flags. `created`: account node -> opened at (epoch s);
    `fraud`: confirmed-fraud nodes (labelled cases, confirmed rings, confirmed payees)."""
    created = dict(created or {})
    aliases = aliases or {}
    canon = [
        Edge(e.kind, aliases.get(e.src, e.src), aliases.get(e.dst, e.dst), e.ts, e.amount)
        for e in edges
    ]
    sent = [e for e in canon if e.kind == "SENT"]
    used = [e for e in canon if e.kind == "USED_DEVICE"]
    fraud_nodes = {aliases.get(n, n) for n in fraud}

    whole = nx.Graph()
    whole.add_edges_from((e.src, e.dst) for e in canon if e.src != e.dst)
    # Money graph for PageRank (directed) and Louvain (undirected), weighted by amount.
    flow = nx.DiGraph()
    for e in sent:
        if e.src != e.dst:
            w = flow.get_edge_data(e.src, e.dst, {"weight": 0.0})["weight"]
            flow.add_edge(e.src, e.dst, weight=w + max(e.amount, 1.0))
    pagerank = nx.pagerank(flow, weight="weight") if flow.number_of_nodes() else {}
    community = communities(flow.to_undirected())
    sizes = Counter(community.values())
    bad = Counter(community[n] for n in fraud_nodes if n in community)

    stats = GraphStats(
        whole=whole,
        sent=sent,
        used=used,
        created=created,
        pagerank=pagerank,
        community=community,
        community_ratio={c: bad[c] / sizes[c] for c in sizes},
        hops=hops_to(whole, fraud_nodes),
        senders_24h=max_senders_24h(sent),
        forwarded=forwarding(sent),
        device_accounts=device_accounts(used),
        young_incoming=young_incoming(sent, created),
    )
    stats.flagged = {
        "fan_in": _fan_in(stats.senders_24h),
        "pass_through": _pass_through(stats.forwarded),
        "shared_device": _shared_device(stats.device_accounts),
        "new_account_burst": _new_account_burst(stats.young_incoming),
    }
    return stats
