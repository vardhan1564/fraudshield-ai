"""Graph detectors, metrics and rings (spec 01 F3) on tiny hand-made graphs and on the simulator.

The simulator test is T5.1's done-when: of the 20 injected mule rings, >= 17 must be found.
"""

import pandas as pd
import pytest

from app.services.graph_service import (
    DAY,
    HOUR,
    NO_PATH_HOPS,
    Edge,
    analyse,
    fan_in,
    forwarded_share,
    new_account_burst,
    pass_through,
    shared_device,
)
from ml.simulator.config import load_config
from ml.simulator.run import simulate

T0 = 1_780_000_000.0


def sent(src: str, dst: str, ts: float, amount: float = 1_000.0) -> Edge:
    return Edge("SENT", src, dst, ts, amount)


def victims(dst: str, n: int, spread_s: float, prefix: str = "V") -> list[Edge]:
    return [sent(f"A:{prefix}{i}", dst, T0 + i * spread_s / n, 5_000) for i in range(n)]


def test_fan_in_needs_more_than_20_senders_within_24h() -> None:
    assert fan_in(victims("A:M", 21, 20 * HOUR)) == {"A:M"}
    assert fan_in(victims("A:M", 20, HOUR)) == set()  # 20 is not > 20
    assert fan_in(victims("A:M", 21, 3 * DAY)) == set()  # never 21 in one day
    assert fan_in(victims("P:shop@okaxis", 50, HOUR)) == set()  # shops have many payers
    repeat = [sent("A:V1", "A:M", T0 + i) for i in range(30)]  # one sender, many payments
    assert fan_in(repeat) == set()


def test_forwarded_share_matches_money_within_the_hour() -> None:
    assert forwarded_share([(T0, 10_000)], [(T0 + 600, 9_000)]) == (10_000, 0.9)
    assert forwarded_share([(T0, 10_000)], [(T0 + 2 * HOUR, 9_000)]) == (10_000, 0.0)
    # Money paid out before it arrived does not count as forwarded.
    assert forwarded_share([(T0, 10_000)], [(T0 - 60, 9_000)]) == (10_000, 0.0)
    # Two payments in, one out covering both.
    total, share = forwarded_share([(T0, 4_000), (T0 + 300, 6_000)], [(T0 + 900, 9_500)])
    assert (total, share) == (10_000, 0.95)


def test_pass_through() -> None:
    mule = [sent("A:V", "A:M", T0, 10_000), sent("A:M", "A:N", T0 + 1_200, 9_000)]
    assert pass_through(mule) == {"A:M"}
    slow = [sent("A:V", "A:M", T0, 10_000), sent("A:M", "A:N", T0 + 3 * HOUR, 9_000)]
    assert pass_through(slow) == set()
    kept = [sent("A:V", "A:M", T0, 10_000), sent("A:M", "A:N", T0 + 600, 7_000)]
    assert pass_through(kept) == set()  # 70% is not > 80%
    small = [sent("A:V", "A:M", T0, 1_000), sent("A:M", "A:N", T0 + 60, 1_000)]
    assert pass_through(small) == set()  # under the minimum amount


def test_shared_device_and_new_account_burst() -> None:
    used = [Edge("USED_DEVICE", f"A:{u}", "D:phone1", T0) for u in ("a", "b", "c")]
    used.append(Edge("USED_DEVICE", "A:d", "D:phone2", T0))
    used.append(Edge("USED_DEVICE", "A:e", "D:phone2", T0))
    assert shared_device(used) == {"A:a", "A:b", "A:c"}

    created = {"A:M": T0 - DAY, "A:OLD": T0 - 90 * DAY}
    assert new_account_burst(victims("A:M", 11, HOUR), created) == {"A:M"}
    assert new_account_burst(victims("A:M", 10, HOUR), created) == set()
    assert new_account_burst(victims("A:OLD", 30, HOUR), created) == set()


def mini_ring() -> list[Edge]:
    """22 victims pay a collector, who forwards to two mules that cash out; all 3 share a phone."""
    edges = victims("P:col@ybl", 22, 20 * 60)
    edges += [sent("A:C", "A:L1", T0 + 1_800, 50_000), sent("A:C", "A:L2", T0 + 1_900, 50_000)]
    edges += [sent(f"A:{m}", "P:cashout@paytm", T0 + 3_000, 48_000) for m in ("L1", "L2")]
    edges += [Edge("USED_DEVICE", f"A:{m}", "D:shared", T0) for m in ("C", "L1", "L2")]
    edges += [Edge("LOGGED_FROM", "A:V0", "I:49.36.10.7", T0)]
    return edges


def test_analyse_finds_the_ring_and_scores_accounts() -> None:
    created = {"A:C": T0 - 2 * DAY}
    result = analyse(mini_ring(), created, fraud={"P:cashout@paytm"},
                     aliases={"P:col@ybl": "A:C"})  # fmt: skip

    assert len(result.rings) == 1
    ring = result.rings[0]
    assert ring.members == ("A:C", "A:L1", "A:L2")
    assert ring.score == 1.0  # all four detectors fired in the ring
    assert ring.detector == "pass_through"  # flagged most members (ties: spec order)

    c = result.accounts["A:C"]
    assert set(c.flags) == {"fan_in", "pass_through", "shared_device", "new_account_burst",
                            "mule_suspect"}  # fmt: skip
    assert c.graph_score == 1.0
    assert c.graph_degree == 22 + 2 + 1  # victims, two mules, the phone
    assert c.hops_to_fraud == 2  # C -> L1 -> cash-out
    assert result.accounts["A:L1"].hops_to_fraud == 1

    victim = result.accounts["A:V5"]
    assert victim.flags == () and victim.graph_score < 0.5
    assert victim.hops_to_fraud == 3  # V5 -> C -> L1 -> cash-out
    assert result.accounts["A:V0"].graph_pagerank < c.graph_pagerank
    fields = c.redis_fields()
    assert set(fields) == {"graph_score", "graph_degree", "graph_pagerank", "hops_to_fraud",
                           "community_fraud_ratio", "flags"}  # fmt: skip


def test_no_path_to_fraud_and_no_ring_for_normal_payments() -> None:
    normal = [sent("A:X", "P:shop@okaxis", T0), sent("A:Y", "P:shop@okaxis", T0 + 60)]
    result = analyse(normal)
    assert result.rings == []
    assert result.accounts["A:X"].hops_to_fraud == NO_PATH_HOPS
    assert result.accounts["A:X"].graph_score == 0.0
    assert "P:shop@okaxis" not in result.accounts  # only accounts are cached
    assert analyse([]).accounts == {}


def test_community_ring_from_confirmed_fraud() -> None:
    """Accounts that trade among themselves with confirmed fraud but trip no rule."""
    edges = [sent(f"A:G{i % 4}", f"A:G{(i + 1) % 4}", T0 + i * DAY, 300) for i in range(8)]
    edges += [sent("A:Z", "P:far@ybl", T0)]
    result = analyse(edges, fraud={"A:G0", "A:G1"})
    assert [r.detector for r in result.rings] == ["community"]
    assert set(result.rings[0].members) == {"A:G0", "A:G1", "A:G2", "A:G3"}
    assert result.accounts["A:G2"].community_fraud_ratio == 0.5


# --- the done-when: >= 17 of the 20 injected rings ---------------------------------------------


def simulator_edges(tables: dict[str, pd.DataFrame]) -> tuple[list[Edge], dict[str, float],
                                                              dict[str, str]]:  # fmt: skip
    """Simulator tables -> graph input, ids as the worker writes them (spec 05 §3)."""
    users, e = tables["users"], tables["edges"]
    prefix = {"SENT": "P:", "USED_DEVICE": "D:", "LOGGED_FROM": "I:"}
    edges = [
        Edge(k, f"A:{s}", f"{prefix[k]}{d}", ts.timestamp(), 0.0 if pd.isna(a) else float(a))
        for k, s, d, a, ts in zip(e.edge_type, e.src_id, e.dst_id, e.amount, e.ts, strict=True)
    ]
    created = {f"A:{u}": pd.Timestamp(t).timestamp()
               for u, t in zip(users.user_id, users.account_created_ts, strict=True)}  # fmt: skip
    aliases = {f"P:{v}": f"A:{u}" for u, v in zip(users.user_id, users.vpa, strict=True)}
    return edges, created, aliases


@pytest.fixture(scope="module")
def simulated() -> dict[str, pd.DataFrame]:
    return simulate(load_config(n_transactions=20_000, n_users=2_000))


def test_simulator_rings_are_found(simulated: dict[str, pd.DataFrame]) -> None:
    users = simulated["users"]
    ring_of = {f"A:{u}": r for u, r in zip(users.user_id, users.ring_id, strict=True)
               if isinstance(r, str)}  # fmt: skip
    sizes = pd.Series(ring_of).value_counts()
    assert len(sizes) == 20

    edges, created, aliases = simulator_edges(simulated)
    result = analyse(edges, created, aliases=aliases)
    found, false_rings = set(), 0
    for ring in result.rings:
        counts = pd.Series([ring_of.get(m, "-") for m in ring.members]).value_counts()
        top, n = counts.index[0], int(counts.iloc[0])
        if top == "-" or n < len(ring.members) / 2:
            false_rings += 1
        elif n >= sizes[top] / 2:  # one ring holds at least half of that ring's mules
            found.add(top)
    print(f"rings found {len(found)}/20, false rings {false_rings}")
    assert len(found) >= 17
    assert false_rings <= 2
