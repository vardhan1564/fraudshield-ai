"""Mule-network detection on the payment graph (spec 01 F3, spec 05 §3). Pure: no I/O.

The graph_refresh job loads graph_edges into `Edge`s and calls `analyse`. The statistics and the
four rule detectors live in ml/features/graph.py (training uses the same code); this module turns
them into rings and the per-account `graph:{account_id}` values.

Flagged accounts that paid each other or share a phone are grouped into rings (Louvain splits
groups too large to be one ring). `graph_score` is the GraphSAGE model's (T5.2) when a scorer is
given, else the rule-based score; accounts it scores above its threshold get the `gnn` flag and
can join rings like the rule detectors.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from ml.features.graph import (
    DAY,
    DETECTORS,
    HOUR,
    NO_PATH_HOPS,
    SEED,
    Edge,
    GraphStats,
    compute,
    fan_in,
    forwarded_share,
    is_account,
    new_account_burst,
    pass_through,
    shared_device,
)

__all__ = [
    "DAY",
    "HOUR",
    "NO_PATH_HOPS",
    "Edge",
    "FoundRing",
    "GraphResult",
    "NodeStats",
    "analyse",
    "fan_in",
    "forwarded_share",
    "new_account_burst",
    "pass_through",
    "shared_device",
]

MIN_RING = 3  # smaller groups are not reported as rings
MAX_RING = 60  # larger groups are split by Louvain community
COMMUNITY_MIN_RATIO = 0.3  # a community this fraudulent is a ring by itself

# GraphStats -> {account node: graph_score}; the GNN (ml.models.gnn.GraphModel.score).
Scorer = Callable[[GraphStats], Mapping[str, float]]
FLAGS = (*DETECTORS, "gnn")  # ring detector names, ties broken in this order


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
    scored_by: str = "rules"  # or "gnn"


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
    scorer: Scorer | None = None,
    threshold: float = 0.5,
) -> GraphResult:
    """Run every detector and metric. `created`: account node -> opened at (epoch s);
    `fraud`: confirmed-fraud nodes (labelled cases, confirmed rings, confirmed payees);
    `scorer` / `threshold`: the GNN and the score above which it flags an account."""
    stats = compute(edges, created, fraud, aliases)
    learned = dict(scorer(stats)) if scorer is not None else None
    flagged = dict(stats.flagged)
    if learned is not None:
        flagged["gnn"] = {n for n, p in learned.items() if p > threshold}
    flags_of: dict[str, tuple[str, ...]] = {}
    for d in (f for f in FLAGS if f in flagged):
        for n in flagged[d]:
            flags_of[n] = (*flags_of.get(n, ()), d)

    result = GraphResult(flagged=flagged, scored_by="rules" if learned is None else "gnn")
    member_of: dict[str, float] = {}
    for group in _ring_groups(set(flags_of), stats.sent, stats.used):
        fired = Counter(d for n in group for d in flags_of[n])
        com_ratio = max((stats.node_ratio(n) for n in group), default=0.0)
        rules = sum(1 for d in DETECTORS if fired[d]) / len(DETECTORS)
        gnn = float(np.median([learned.get(n, 0.0) for n in group])) if learned else 0.0
        score = round(max(rules, com_ratio, gnn), 4)
        detector = max((f for f in FLAGS if fired[f]), key=lambda d: (fired[d], -FLAGS.index(d)))
        result.rings.append(FoundRing(tuple(sorted(group)), score, detector))
        member_of.update(dict.fromkeys(group, score))

    in_rule_ring = set(member_of)
    for c, r in stats.community_ratio.items():
        if r < COMMUNITY_MIN_RATIO:
            continue
        group = {n for n, cc in stats.community.items() if cc == c and is_account(n)}
        if len(group) >= MIN_RING and not group <= in_rule_ring:
            result.rings.append(FoundRing(tuple(sorted(group)), round(r, 4), "community"))
            for n in group:
                member_of[n] = max(member_of.get(n, 0.0), round(r, 4))

    for node in (n for n in stats.whole if is_account(n)):
        rule_flags = [f for f in flags_of.get(node, ()) if f in DETECTORS]
        com = stats.node_ratio(node)
        rule_score = max(len(rule_flags) / len(DETECTORS), com, member_of.get(node, 0.0))
        score = learned.get(node, 0.0) if learned is not None else rule_score
        flags = flags_of.get(node, ()) + (("mule_suspect",) if node in member_of else ())
        result.accounts[node] = NodeStats(
            graph_score=round(score, 4),
            graph_degree=float(stats.whole.degree(node)),
            graph_pagerank=float(stats.pagerank.get(node, 0.0)),
            hops_to_fraud=float(stats.hops.get(node, NO_PATH_HOPS)),
            community_fraud_ratio=round(com, 4),
            flags=flags,
        )
    return result
