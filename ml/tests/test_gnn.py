"""GraphSAGE mule model (ml/models/gnn.py): node features, threshold, save/load, point-in-time
history and a small end-to-end training run on simulator worlds."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from ml.features.graph import DAY, Edge, compute
from ml.models import gnn
from ml.pipelines.train import DEFAULT_CONFIG
from ml.simulator.config import load_config
from ml.simulator.run import simulate

T0 = 1_780_000_000.0
SMALL = {"n_transactions": 20_000, "n_users": 2_000}


def mini_stats() -> Any:
    edges = [Edge("SENT", f"A:V{i}", "P:col@ybl", T0 + i * 60, 5_000) for i in range(22)]
    edges += [Edge("SENT", "A:C", "A:L1", T0 + 1_800, 100_000)]
    edges += [Edge("USED_DEVICE", f"A:{m}", "D:phone", T0) for m in ("C", "L1", "L2")]
    edges += [Edge("LOGGED_FROM", "A:V0", "I:1.2.3.4", T0)]
    return compute(edges, {"A:C": T0 - DAY}, fraud={"A:L1"}, aliases={"P:col@ybl": "A:C"})


def test_node_features() -> None:
    stats = mini_stats()
    nodes, x = gnn.node_features(stats, now=T0 + 2 * DAY)
    assert x.shape == (len(nodes), len(gnn.NODE_FEATURES)) and x.dtype == np.float32
    row = dict(zip(gnn.NODE_FEATURES, x[nodes.index("A:C")], strict=True))
    assert row["type_A"] == 1 and row["type_P"] == 0
    assert row["senders_24h"] == pytest.approx(np.log1p(22))
    assert row["in_amount"] == pytest.approx(np.log1p(110_000))
    assert row["forwarded_share"] == pytest.approx(100_000 / 110_000)
    assert row["account_age"] == pytest.approx(3 / gnn.AGE_CAP_DAYS)
    assert row["device_accounts"] == pytest.approx(np.log1p(3))
    assert row["hops_to_fraud"] == pytest.approx(0.1)  # one hop from the confirmed mule
    device = dict(zip(gnn.NODE_FEATURES, x[nodes.index("D:phone")], strict=True))
    assert device["type_D"] == 1 and device["account_age"] == 0 and device["in_count"] == 0
    ei = gnn.edge_index(stats, nodes)
    assert ei.shape == (2, 2 * stats.whole.number_of_edges())


def test_flag_threshold_does_not_spend_fpr_for_nothing() -> None:
    y = np.array([0] * 100 + [1] * 10)
    p = np.concatenate([np.linspace(0.0, 0.3, 100), np.full(10, 0.9)])
    # 1% FPR alone would sit at the 99th ordinary score (0.297); nothing is lost at 0.3.
    assert gnn.flag_threshold(y, p, 0.01) == pytest.approx(0.3)
    p_mixed = np.concatenate([np.linspace(0.0, 1.0, 100), np.full(10, 0.95)])
    t = gnn.flag_threshold(y, p_mixed, 0.05)
    assert ((p_mixed > t) & (y == 1)).sum() == 10 and ((p_mixed > t) & (y == 0)).sum() <= 5


def test_save_load_round_trip(tmp_path: Path) -> None:
    stats = mini_stats()
    net = gnn.GraphSAGE(len(gnn.NODE_FEATURES), hidden=8, dropout=0.0)
    n = len(gnn.NODE_FEATURES)
    model = gnn.GraphModel(net, np.zeros(n, "float32"), np.ones(n, "float32"), 0.42, {"v": 1})
    path = tmp_path / "graph_model.pt"
    gnn.save(model, path)
    loaded = gnn.load(path)
    assert loaded.threshold == 0.42 and loaded.meta == {"v": 1}
    a, b = model.score(stats, T0), loaded.score(stats, T0)
    assert a.keys() == b.keys() == {n for n in stats.whole if n.startswith("A:")}
    assert all(a[k] == pytest.approx(b[k]) for k in a)


@pytest.fixture(scope="module")
def trained() -> tuple[gnn.GnnResult, gnn.World, dict[str, Any]]:
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    cfg["graph"] |= {"sim": SMALL, "train_seeds": [101], "epochs": 8, "patience": 3,
                     "train_every_days": 4}  # fmt: skip
    world = gnn.World.from_tables(simulate(load_config(**SMALL)))
    return gnn.train_gnn(cfg, world), world, cfg


def test_small_training_run_finds_rings(trained: tuple[gnn.GnnResult, gnn.World, Any]) -> None:
    result, _, _ = trained
    assert result.at_end["ring_recall"] >= 0.85
    assert result.at_end["ordinary_fpr"] <= 0.02
    assert len(result.at_end["rings"]) == 20


def test_history_is_point_in_time(trained: tuple[gnn.GnnResult, gnn.World, Any]) -> None:
    result, world, cfg = trained
    gcfg = cfg["graph"]
    start = float(world.txns["t"].iloc[-1] - 3 * DAY)
    history = gnn.graph_score_history(result.model, world, start, gcfg)
    late = world.txns[world.txns["t"] >= start]
    assert list(history["txn_id"]) == list(late["txn_id"])
    # Re-score three payments from the snapshot just before them: same value, so nothing
    # from the payment itself or later went into its score.
    step = gcfg["history_every_hours"] * 3600
    for _, row in late.sample(3, random_state=0).iterrows():
        t = np.floor(row["t"] / step) * step
        assert t <= row["t"]
        stats = world.stats(float(t), gcfg["window_days"], gcfg["fraud_min_reports"])
        expected = result.model.score(stats, float(t)).get(row["src"], 0.0)
        got = history.set_index("txn_id").loc[row["txn_id"], "graph_score"]
        assert got == pytest.approx(expected, abs=1e-6)
