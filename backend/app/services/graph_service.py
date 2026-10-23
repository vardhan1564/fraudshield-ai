"""Mule-network detection on the payment graph (spec 01 F3, spec 05 §3). Pure: no I/O.

The graph_refresh job loads graph_edges into `Edge`s and calls `analyse`. Node ids are spec 05's
`A:{user}`, `P:{vpa}`, `D:{fingerprint}`, `I:{ip}`; `aliases` maps a payee to its customer
account (`P:{vpa}` -> `A:{user}`), so money paid to a mule's VPA arrives at the mule's account.

Four rule detectors flag accounts. Flagged accounts that paid each other or share a phone are
grouped into rings (Louvain splits groups too large to be one ring). Per account the job caches
degree, PageRank, hops to the nearest confirmed-fraud node and the Louvain community's fraud
ratio. Until the GNN (T5.2) `graph_score` is the rule-based score below.
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
MIN_RING = 3  # smaller groups are not reported as rings
MAX_RING = 60  # larger groups are split by Louvain community
COMMUNITY_MIN_RATIO = 0.3  # a community this fraudulent is a ring by itself
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


@dataclass(frozen=True)
class NodeStats:
    graph_score: float
    graph_degree: float
    graph_pagerank: float
    hops_to_fraud: float
    community_fraud_ratio: float
    flags: tuple[str, ...] = ()

    def redis_fields(self) -> dict[str, str]:
        """The `graph:{account_id}` hash: spec 03 feature names + the detector flags."""
        return {
            "graph_score": f"{self.graph_score:.4f}",
            "graph_degree": f"{self.graph_degree:g}",
            "graph_pagerank": f"{self.graph_pagerank:.6g}",
            "hops_to_fraud": f"{self.hops_to_fraud:g}",
            "community_fraud_ratio": f"{self.community_fraud_ratio:.4f}",
            "flags": ",".join(self.flags),
        }


@dataclass(frozen=True)
class FoundRing:
    members: tuple[str, ...]  # sorted node ids
    score: float
    detector: str


@dataclass
class GraphResult:
    accounts: dict[str, NodeStats] = field(default_factory=dict)
    rings: list[FoundRing] = field(default_factory=list)
    flagged: dict[str, set[str]] = field(default_factory=dict)  # detector -> accounts


def is_account(node: str) -> bool:
    return node.startswith("A:")


# --- rule detectors ---------------------------------------------------------------------------


def fan_in(sent: Sequence[Edge]) -> set[str]:
    """Accounts paid by more than FAN_IN_SENDERS distinct senders within any 24 h.
    Only customer accounts: shops are meant to have many payers."""
    incoming: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for e in sent:
        if is_account(e.dst):
            incoming[e.dst].append((e.ts, e.src))
    flagged = set()
    for node, events in incoming.items():
        if len({s for _, s in events}) <= FAN_IN_SENDERS:
            continue
        events.sort()
        window: Counter[str] = Counter()
        lo = 0
        for ts, sender in events:
            window[sender] += 1
            while events[lo][0] <= ts - DAY:
                old = events[lo][1]
                window[old] -= 1
                if not window[old]:
                    del window[old]
                lo += 1
            if len(window) > FAN_IN_SENDERS:
                flagged.add(node)
                break
    return flagged


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


def pass_through(sent: Sequence[Edge]) -> set[str]:
    incoming: dict[str, list[tuple[float, float]]] = defaultdict(list)
    outgoing: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for e in sent:
        incoming[e.dst].append((e.ts, e.amount))
        outgoing[e.src].append((e.ts, e.amount))
    flagged = set()
    for node in incoming.keys() & outgoing.keys():
        if not is_account(node):
            continue
        total, share = forwarded_share(incoming[node], outgoing[node])
        if total >= PASS_THROUGH_MIN_INR and share > PASS_THROUGH_SHARE:
            flagged.add(node)
    return flagged


def shared_device(used: Sequence[Edge]) -> set[str]:
    accounts: dict[str, set[str]] = defaultdict(set)
    for e in used:
        accounts[e.dst].add(e.src)
    return {a for group in accounts.values() if len(group) >= SHARED_DEVICE_ACCOUNTS for a in group}


def new_account_burst(sent: Sequence[Edge], created: Mapping[str, float]) -> set[str]:
    young = Counter(
        e.dst
        for e in sent
        if e.dst in created and 0 <= e.ts - created[e.dst] < NEW_ACCOUNT_DAYS * DAY
    )
    return {node for node, n in young.items() if n > NEW_ACCOUNT_INCOMING}


# --- graph metrics ----------------------------------------------------------------------------


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


# --- rings ------------------------------------------------------------------------------------


def _ring_groups(flagged: set[str], sent: Sequence[Edge], used: Sequence[Edge]) -> list[set[str]]:
    """Flagged accounts joined when one paid the other or they share a device."""
    links = nx.Graph()
    links.add_nodes_from(flagged)
    for e in sent:
        if e.src in flagged and e.dst in flagged and e.src != e.dst:
            links.add_edge(e.src, e.dst, weight=links.get_edge_data(e.src, e.dst, {"weight": 0})
                           ["weight"] + 1)  # fmt: skip
    by_device: dict[str, list[str]] = defaultdict(list)
    for e in used:
        if e.src in flagged:
            by_device[e.dst].append(e.src)
    for group in by_device.values():
        for a, b in zip(group, group[1:], strict=False):
            if a != b:
                links.add_edge(a, b, weight=1)
    groups: list[set[str]] = []
    for comp in nx.connected_components(links):
        if len(comp) > MAX_RING:
            sub = links.subgraph(comp)
            groups.extend(nx.community.louvain_communities(sub, weight="weight", seed=SEED))
        else:
            groups.append(set(comp))
    return [g for g in groups if len(g) >= MIN_RING]


def analyse(
    edges: Iterable[Edge],
    created: Mapping[str, float] | None = None,
    fraud: Iterable[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> GraphResult:
    """Run every detector and metric. `created`: account node -> opened at (epoch s);
    `fraud`: confirmed-fraud nodes (labelled cases, confirmed rings, confirmed payees)."""
    created = created or {}
    aliases = aliases or {}
    canon = [
        Edge(e.kind, aliases.get(e.src, e.src), aliases.get(e.dst, e.dst), e.ts, e.amount)
        for e in edges
    ]
    sent = [e for e in canon if e.kind == "SENT"]
    used = [e for e in canon if e.kind == "USED_DEVICE"]
    fraud_nodes = {aliases.get(n, n) for n in fraud}

    flagged = {
        "fan_in": fan_in(sent),
        "pass_through": pass_through(sent),
        "shared_device": shared_device(used),
        "new_account_burst": new_account_burst(sent, created),
    }
    flags_of: dict[str, list[str]] = defaultdict(list)
    for name in DETECTORS:
        for node in flagged[name]:
            flags_of[node].append(name)

    # Every node and relation (multi-edges collapsed) for degree and hops.
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
    ratio = {c: bad[c] / sizes[c] for c in sizes}
    hops = hops_to(whole, fraud_nodes)

    result = GraphResult(flagged=flagged)
    member_of: dict[str, float] = {}
    for group in _ring_groups(set(flags_of), sent, used):
        fired = Counter(d for n in group for d in flags_of[n])
        com_ratio = max((ratio.get(community.get(n, -1), 0.0) for n in group), default=0.0)
        score = round(max(len(fired) / len(DETECTORS), com_ratio), 4)
        detector = max(DETECTORS, key=lambda d: (fired[d], -DETECTORS.index(d)))
        result.rings.append(FoundRing(tuple(sorted(group)), score, detector))
        member_of.update(dict.fromkeys(group, score))

    in_rule_ring = set(member_of)
    for c, r in ratio.items():
        if r < COMMUNITY_MIN_RATIO:
            continue
        group = {n for n, cc in community.items() if cc == c and is_account(n)}
        if len(group) >= MIN_RING and not group <= in_rule_ring:
            result.rings.append(FoundRing(tuple(sorted(group)), round(r, 4), "community"))
            for n in group:
                member_of[n] = max(member_of.get(n, 0.0), round(r, 4))

    for node in (n for n in whole if is_account(n)):
        own = len(flags_of.get(node, ())) / len(DETECTORS)
        com = ratio.get(community.get(node, -1), 0.0)
        flags = tuple(flags_of.get(node, ())) + (("mule_suspect",) if node in member_of else ())
        result.accounts[node] = NodeStats(
            graph_score=round(max(own, com, member_of.get(node, 0.0)), 4),
            graph_degree=float(whole.degree(node)),
            graph_pagerank=float(pagerank.get(node, 0.0)),
            hops_to_fraud=float(hops.get(node, NO_PATH_HOPS)),
            community_fraud_ratio=round(com, 4),
            flags=flags,
        )
    return result
