"""Graph model (spec 03 §1 `graph_model`, spec 01 F3): GraphSAGE mule-account classifier.

    python -m ml.models.gnn
    python -m ml.models.gnn --no-mlflow

Nodes are the accounts, payees, devices and IPs of a 30-day graph snapshot, exactly what the
worker's graph_refresh builds (ml/features/graph.py). Node features: the spec 03 §2 graph group
(degree, PageRank, hops to confirmed fraud, community fraud ratio) plus account aggregates (in/out
counts and amounts, distinct senders, most senders in 24 h, share forwarded within 1 h, account
age, accounts sharing its phone, payments received while new). Two SAGEConv layers (PyTorch
Geometric) give P(mule) per account; that is `graph_score`.

Training uses separate simulator worlds (config `graph.train_seeds`, `graph.val_seed`), so the 20
rings in ml/data/simulated are never seen in training. Reported there:
- ring recall: a ring counts as found when >= half its accounts score above the threshold, on the
  snapshot taken 1 h after the ring's last payment (plus an "early" snapshot 6 h in);
- the point-in-time graph_score of every validation/test payment's sender, from snapshots taken
  every `history_every_hours` before the payment, written for the meta-model (no look-ahead).

Offline there are no analyst labels; payees with >= `fraud_min_reports` earlier reports stand in
for confirmed fraud (hops_to_fraud, community fraud ratio).
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import yaml
from sklearn.metrics import average_precision_score
from torch import nn
from torch_geometric.nn import SAGEConv

from ml.features.graph import DAY, HOUR, NO_PATH_HOPS, Edge, GraphStats, compute, is_account
from ml.pipelines.metrics import threshold_for_fpr
from ml.pipelines.split import time_split
from ml.pipelines.train import DEFAULT_CONFIG, ROOT, load_dataset, mlflow_client
from ml.simulator.config import load_config
from ml.simulator.run import simulate

ARTIFACT = ROOT / "ml" / "artifacts" / "graph_model.pt"
HISTORY = ROOT / "ml" / "data" / "features" / "graph_scores.parquet"
RAW = ROOT / "ml" / "data" / "simulated"

NODE_TYPES = ("A", "P", "D", "I")
NODE_FEATURES = [
    *(f"type_{t}" for t in NODE_TYPES),
    "graph_degree",
    "graph_pagerank",
    "hops_to_fraud",
    "community_fraud_ratio",
    "in_count",
    "out_count",
    "in_amount",
    "out_amount",
    "distinct_senders",
    "senders_24h",
    "forwarded_share",
    "account_age",
    "device_accounts",
    "young_incoming",
]
AGE_CAP_DAYS = 365


# --- node features -----------------------------------------------------------------------------


def node_features(stats: GraphStats, now: float) -> tuple[list[str], np.ndarray]:
    """One row per graph node (order = `nodes`), columns = NODE_FEATURES, before scaling.
    Counts and amounts are log1p; PageRank is scaled by the node count (1 = average)."""
    nodes = list(stats.whole.nodes)
    in_n: Counter[str] = Counter()
    out_n: Counter[str] = Counter()
    in_amt: defaultdict[str, float] = defaultdict(float)
    out_amt: defaultdict[str, float] = defaultdict(float)
    senders: defaultdict[str, set[str]] = defaultdict(set)
    for e in stats.sent:
        in_n[e.dst] += 1
        out_n[e.src] += 1
        in_amt[e.dst] += e.amount
        out_amt[e.src] += e.amount
        senders[e.dst].add(e.src)
    n_flow = max(len(stats.pagerank), 1)
    x = np.zeros((len(nodes), len(NODE_FEATURES)), dtype="float32")
    for i, node in enumerate(nodes):
        account = is_account(node)
        created = stats.created.get(node)
        age = AGE_CAP_DAYS if created is None else min(max(now - created, 0) / DAY, AGE_CAP_DAYS)
        x[i, NODE_TYPES.index(node[0]) if node[0] in NODE_TYPES else 0] = 1.0
        x[i, 4:] = (
            np.log1p(stats.whole.degree(node)),
            np.log1p(stats.pagerank.get(node, 0.0) * n_flow),
            stats.hops.get(node, NO_PATH_HOPS) / NO_PATH_HOPS,
            stats.node_ratio(node),
            np.log1p(in_n[node]),
            np.log1p(out_n[node]),
            np.log1p(in_amt[node]),
            np.log1p(out_amt[node]),
            np.log1p(len(senders[node])),
            np.log1p(stats.senders_24h.get(node, 0)),
            stats.forwarded.get(node, (0.0, 0.0))[1],
            age / AGE_CAP_DAYS if account else 0.0,
            np.log1p(stats.device_accounts.get(node, 0)),
            np.log1p(stats.young_incoming.get(node, 0)),
        )
    return nodes, x


def edge_index(stats: GraphStats, nodes: Sequence[str]) -> torch.Tensor:
    """Both directions of every relation: money, phones and networks all carry evidence."""
    pos = {n: i for i, n in enumerate(nodes)}
    pairs = np.array([(pos[a], pos[b]) for a, b in stats.whole.edges], dtype="int64")
    if len(pairs) == 0:
        return torch.zeros((2, 0), dtype=torch.long)
    both = np.concatenate([pairs, pairs[:, ::-1]])
    return torch.from_numpy(np.ascontiguousarray(both.T))


class GraphSAGE(nn.Module):
    def __init__(self, n_in: int, hidden: int, dropout: float) -> None:
        super().__init__()
        self.conv1 = SAGEConv(n_in, hidden)
        self.conv2 = SAGEConv(hidden, hidden)
        self.out = nn.Linear(hidden, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, ei: torch.Tensor) -> torch.Tensor:
        h = self.dropout(torch.relu(self.conv1(x, ei)))
        h = torch.relu(self.conv2(h, ei))
        return self.out(h).squeeze(-1)  # logits


@dataclass
class GraphModel:
    net: GraphSAGE
    mean: np.ndarray
    std: np.ndarray
    threshold: float  # graph_score above this = mule suspect (FPR target on validation)
    meta: dict[str, Any] = field(default_factory=dict)

    def probabilities(self, x: np.ndarray, ei: torch.Tensor) -> np.ndarray:
        self.net.eval()
        with torch.no_grad():
            z = torch.from_numpy((x - self.mean) / self.std)
            return torch.sigmoid(self.net(z, ei)).numpy()

    def score(self, stats: GraphStats, now: float) -> dict[str, float]:
        """graph_score per account node of one refresh."""
        nodes, x = node_features(stats, now)
        if not nodes:
            return {}
        p = self.probabilities(x, edge_index(stats, nodes))
        return {n: float(v) for n, v in zip(nodes, p, strict=True) if is_account(n)}


def save(model: GraphModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state": model.net.state_dict(),
            "n_in": len(NODE_FEATURES),
            "hidden": model.net.conv1.out_channels,
            "features": NODE_FEATURES,
            "mean": model.mean,
            "std": model.std,
            "threshold": model.threshold,
            "meta": model.meta,
        },
        path,
    )


def load(path: Path = ARTIFACT) -> GraphModel:
    # weights_only=False: the file holds numpy arrays and a dict. Only load files we wrote.
    blob = torch.load(path, weights_only=False)
    if blob["features"] != NODE_FEATURES:
        raise ValueError(f"{path} was trained on other node features; retrain the graph model")
    net = GraphSAGE(blob["n_in"], blob["hidden"], dropout=0.0)
    net.load_state_dict(blob["state"])
    return GraphModel(net, blob["mean"], blob["std"], float(blob["threshold"]), blob["meta"])


# --- simulator snapshots -----------------------------------------------------------------------


def _epoch(ts: pd.Series) -> np.ndarray:
    return (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds().to_numpy()


@dataclass
class World:
    """A simulator run as the worker would see it: graph_edges rows written per payment."""

    edges: list[Edge]  # time order
    ts: np.ndarray  # edge times (epoch s), for slicing windows
    created: dict[str, float]
    aliases: dict[str, str]
    report_vpas: np.ndarray  # reports in time order
    report_ts: np.ndarray
    ring_of: dict[str, str]  # mule account node -> ring id
    txns: pd.DataFrame  # txn_id, sender node, epoch s, ring_id (time order)

    @classmethod
    def from_tables(cls, tables: Mapping[str, pd.DataFrame]) -> "World":
        txns, users, devices = tables["transactions"], tables["users"], tables["devices"]
        reports = tables["reports"].sort_values("ts", kind="stable")
        fingerprint = devices.set_index("device_id")["fingerprint"]
        t = txns.sort_values("ts", kind="stable")
        epoch = _epoch(t["ts"])
        src = ("A:" + t["user_id"]).to_numpy()
        edges: list[Edge] = []
        for s, vpa, amount, dev, ip, ts in zip(
            src, t["payee_vpa"], t["amount"], t["device_id"].map(fingerprint), t["ip"], epoch,
            strict=True,
        ):  # fmt: skip  # the graph_edges consumer writes these three per payment
            edges.append(Edge("SENT", s, f"P:{vpa}", float(ts), float(amount)))
            if isinstance(dev, str):
                edges.append(Edge("USED_DEVICE", s, f"D:{dev}", float(ts)))
            if isinstance(ip, str):
                edges.append(Edge("LOGGED_FROM", s, f"I:{ip}", float(ts)))
        mules = users[users["ring_id"].notna()]
        return cls(
            edges=edges,
            ts=np.array([e.ts for e in edges]),
            created={
                f"A:{u}": pd.Timestamp(c).timestamp()
                for u, c in zip(users["user_id"], users["account_created_ts"], strict=True)
            },
            aliases={
                f"P:{v}": f"A:{u}" for u, v in zip(users["user_id"], users["vpa"], strict=True)
            },  # fmt: skip
            report_vpas=reports["payee_vpa"].to_numpy(),
            report_ts=_epoch(reports["ts"]),
            ring_of=dict(zip("A:" + mules["user_id"], mules["ring_id"], strict=True)),
            txns=pd.DataFrame(
                {
                    "txn_id": t["txn_id"].to_numpy(),
                    "src": src,
                    "t": epoch,
                    "ring_id": t["ring_id"].to_numpy(),
                }
            ),  # fmt: skip
        )

    def stats(self, t: float, window_days: int, min_reports: int) -> GraphStats:
        """The graph a refresh at time t would build: edges in [t - window, t)."""
        lo, hi = np.searchsorted(self.ts, [t - window_days * DAY, t])
        seen = Counter(self.report_vpas[: np.searchsorted(self.report_ts, t)])
        fraud = {f"P:{v}" for v, n in seen.items() if n >= min_reports}
        return compute(self.edges[lo:hi], self.created, fraud, self.aliases)


@dataclass
class Snapshot:
    nodes: list[str]
    x: np.ndarray
    ei: torch.Tensor
    y: np.ndarray  # 1 mule, 0 ordinary account, -1 not an account (not scored)


def snapshot(world: World, t: float, gcfg: dict[str, Any]) -> Snapshot:
    stats = world.stats(t, gcfg["window_days"], gcfg["fraud_min_reports"])
    nodes, x = node_features(stats, t)
    y = np.array(
        [(1 if n in world.ring_of else 0) if is_account(n) else -1 for n in nodes], dtype="int8"
    )
    return Snapshot(nodes, x, edge_index(stats, nodes), y)


def training_snapshots(world: World, gcfg: dict[str, Any]) -> list[Snapshot]:
    start, end = world.ts[0] + gcfg["window_days"] * DAY, world.ts[-1]
    times = np.arange(start, end + 1, gcfg["train_every_days"] * DAY)
    return [snapshot(world, float(t), gcfg) for t in times]


# --- training ----------------------------------------------------------------------------------


def _scores_and_labels(
    model: GraphModel, snaps: Sequence[Snapshot]
) -> tuple[np.ndarray, np.ndarray]:
    p = [model.probabilities(s.x, s.ei)[s.y >= 0] for s in snaps]
    y = [s.y[s.y >= 0] for s in snaps]
    return np.concatenate(p), np.concatenate(y)


def flag_threshold(y: np.ndarray, p: np.ndarray, max_fpr: float) -> float:
    """The FPR-limited threshold, raised as far as it goes without losing any recall: ordinary
    accounts below the lowest flagged mule's score are not flagged for nothing."""
    t = threshold_for_fpr(y, p, max_fpr)
    caught = p[(y == 1) & (p > t)]
    if caught.size == 0:
        return t
    below = p[(y == 0) & (p < caught.min())]
    return float(max(t, below.max())) if below.size else t


def train_graph_model(
    train: Sequence[Snapshot], val: Sequence[Snapshot], gcfg: dict[str, Any], seed: int
) -> tuple[GraphModel, dict[str, Any]]:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    allx = np.concatenate([s.x for s in train])
    mean = allx.mean(axis=0)
    std = np.where(allx.std(axis=0) > 1e-6, allx.std(axis=0), 1.0).astype("float32")
    labels = np.concatenate([s.y[s.y >= 0] for s in train])
    pos_weight = torch.tensor((labels == 0).sum() / max((labels == 1).sum(), 1))

    net = GraphSAGE(len(NODE_FEATURES), gcfg["hidden"], gcfg["dropout"])
    model = GraphModel(net, mean, std, threshold=0.5)
    opt = torch.optim.Adam(net.parameters(), lr=gcfg["lr"], weight_decay=gcfg["weight_decay"])
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    tensors = [
        (torch.from_numpy((s.x - mean) / std), s.ei, torch.from_numpy(s.y.astype("float32")),
         torch.from_numpy(s.y >= 0))
        for s in train
    ]  # fmt: skip
    best, best_state, stale, history = -1.0, None, 0, []
    for epoch in range(gcfg["epochs"]):
        net.train()
        total = 0.0
        for i in rng.permutation(len(tensors)):
            x, ei, y, mask = tensors[i]
            opt.zero_grad()
            loss = loss_fn(net(x, ei)[mask], y[mask])
            loss.backward()
            opt.step()
            total += loss.item()
        p, y = _scores_and_labels(model, val)
        ap = float(average_precision_score(y, p))
        history.append({"epoch": epoch, "loss": total / len(tensors), "val_pr_auc": ap})
        if ap > best:
            best, stale = ap, 0
            best_state = {k: v.clone() for k, v in net.state_dict().items()}
        else:
            stale += 1
            if stale >= gcfg["patience"]:
                break
    assert best_state is not None
    net.load_state_dict(best_state)
    p, y = _scores_and_labels(model, val)
    model.threshold = flag_threshold(y, p, gcfg["target_fpr"])
    flagged = p > model.threshold
    val_metrics = {
        "pr_auc": float(average_precision_score(y, p)),
        "recall": float(flagged[y == 1].mean()),
        "fpr": float(flagged[y == 0].mean()),
        "epochs": len(history),
        "best_epoch": int(max(history, key=lambda h: h["val_pr_auc"])["epoch"]),
    }
    return model, val_metrics


# --- evaluation on the main dataset ------------------------------------------------------------


def ring_recall(model: GraphModel, world: World, gcfg: dict[str, Any], offset_s: float,
                from_start: bool) -> dict[str, Any]:  # fmt: skip
    """Score each ring on a snapshot `offset_s` after its last (or first) payment."""
    rows, legit_flags = [], []
    rings = world.txns.dropna(subset=["ring_id"]).groupby("ring_id")["t"].agg(["min", "max"])
    for ring_id, (t_min, t_max) in rings.iterrows():
        t = (t_min if from_start else t_max) + offset_s
        stats = world.stats(float(t), gcfg["window_days"], gcfg["fraud_min_reports"])
        scores = model.score(stats, float(t))
        members = [n for n, r in world.ring_of.items() if r == ring_id and n in scores]
        hit = [scores[n] > model.threshold for n in members]
        legit_flags += [s > model.threshold for n, s in scores.items() if n not in world.ring_of]
        rows.append({"ring": ring_id, "members": len(members), "flagged": int(sum(hit))})
    frame = pd.DataFrame(rows)
    share = frame["flagged"] / frame["members"].clip(lower=1)
    return {
        "ring_recall": float((share >= 0.5).mean()),
        "mule_recall": float(frame["flagged"].sum() / max(frame["members"].sum(), 1)),
        "ordinary_fpr": float(np.mean(legit_flags)) if legit_flags else 0.0,
        "rings": rows,
    }


def graph_score_history(model: GraphModel, world: World, start: float, gcfg: dict[str, Any]
                        ) -> pd.DataFrame:  # fmt: skip
    """Each payment from `start` on gets its sender's graph_score from the latest snapshot
    taken before it (snapshots every `history_every_hours`; senders not in the graph get 0)."""
    step = gcfg["history_every_hours"] * HOUR
    t0 = np.floor(start / step) * step
    txns = world.txns[world.txns["t"] >= start]
    out = []
    times = np.arange(t0, txns["t"].max() + step, step)
    for i, t in enumerate(times):
        batch = txns[(txns["t"] >= t) & (txns["t"] < t + step)]
        if batch.empty:
            continue
        scores = model.score(world.stats(float(t), gcfg["window_days"],
                                         gcfg["fraud_min_reports"]), float(t))  # fmt: skip
        out.append(pd.DataFrame({"txn_id": batch["txn_id"],
                                 "graph_score": batch["src"].map(scores).fillna(0.0)}))  # fmt: skip
        if i % 24 == 0:
            print(f"  history snapshot {i + 1}/{len(times)}", flush=True)
    return pd.concat(out, ignore_index=True)


@dataclass
class GnnResult:
    model: GraphModel
    val: dict[str, Any]
    at_end: dict[str, Any]
    early: dict[str, Any]
    sizes: dict[str, int]


def world(seed: int, gcfg: dict[str, Any]) -> World:
    """A fresh simulator world; `graph.sim` may shrink it (tests)."""
    print(f"simulating world seed={seed}", flush=True)
    return World.from_tables(simulate(load_config(seed=seed, **gcfg.get("sim", {}))))


def train_gnn(cfg: dict[str, Any], main_world: World) -> GnnResult:
    gcfg, seed = cfg["graph"], cfg["seed"]
    train: list[Snapshot] = []
    for train_seed in gcfg["train_seeds"]:
        train += training_snapshots(world(train_seed, gcfg), gcfg)
    val = training_snapshots(world(gcfg["val_seed"], gcfg), gcfg)
    print(f"training on {len(train)} snapshots", flush=True)
    model, val_metrics = train_graph_model(train, val, gcfg, seed)
    model.meta = {
        "version": date.today().isoformat(),
        "train_seeds": gcfg["train_seeds"],
        "val_seed": gcfg["val_seed"],
        "val": val_metrics,
    }
    at_end = ring_recall(model, main_world, gcfg, HOUR, from_start=False)
    early = ring_recall(model, main_world, gcfg, 6 * HOUR, from_start=True)
    model.meta |= {"ring_recall": at_end["ring_recall"], "mule_recall": at_end["mule_recall"]}
    sizes = {
        "train_snapshots": len(train),
        "val_snapshots": len(val),
        "train_accounts": int(sum((s.y >= 0).sum() for s in train)),
        "train_mules": int(sum((s.y == 1).sum() for s in train)),
    }
    return GnnResult(model, val_metrics, at_end, early, sizes)


def file_hash(path: Path) -> str:
    """Ties the served file to its MLflow run (the model card registers that run)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def saved_meta(r: GnnResult, gcfg: dict[str, Any]) -> dict[str, Any]:
    """graph_model.meta.json: what the model card and reviewers need (the .pt is git-ignored)."""
    return {
        "model": "graph_model",
        "version": r.model.meta["version"],
        "features": NODE_FEATURES,
        "threshold": r.model.threshold,
        "config": {k: v for k, v in gcfg.items() if k != "mlflow_experiment"},
        "sizes": r.sizes,
        "val": r.val,
        "main_dataset": {"at_end": r.at_end, "early": r.early},
    }


def format_table(r: GnnResult, min_recall: float) -> str:
    v, e, a = r.val, r.early, r.at_end
    lines = [
        "graph_model | GraphSAGE (2 x SAGEConv) on 30-day snapshots, "
        f"{len(NODE_FEATURES)} node features",
        f"trained on {r.sizes['train_snapshots']} snapshots ({r.sizes['train_mules']:,} mule / "
        f"{r.sizes['train_accounts']:,} account rows), best epoch {v['best_epoch']} of "
        f"{v['epochs']}",
        f"validation world: PR-AUC {v['pr_auc']:.4f}, threshold {r.model.threshold:.4f} -> "
        f"recall {v['recall']:.2%} at FPR {v['fpr']:.2%}",
        "",
        "main dataset (20 rings, never trained on)   ring recall  mule recall  ordinary FPR",
        f"{'1 h after the last payment':<44}{a['ring_recall']:>11.0%}{a['mule_recall']:>13.1%}"
        f"{a['ordinary_fpr']:>14.2%}",
        f"{'6 h after the first payment (early)':<44}{e['ring_recall']:>11.0%}"
        f"{e['mule_recall']:>13.1%}{e['ordinary_fpr']:>14.2%}",
        f"done-when ring recall >= {min_recall:.0%}: "
        + ("PASS" if a["ring_recall"] >= min_recall else "FAIL"),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the GraphSAGE mule model.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=ARTIFACT)
    parser.add_argument("--history", type=Path, default=HISTORY)
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    gcfg = cfg["graph"]
    started = time.perf_counter()
    raw = {n: pd.read_parquet(RAW / f"{n}.parquet")
           for n in ("transactions", "users", "devices", "reports")}  # fmt: skip
    main_world = World.from_tables(raw)
    result = train_gnn(cfg, main_world)
    table = format_table(result, gcfg["min_recall"])
    print(table)
    save(result.model, args.out)
    meta = saved_meta(result, gcfg) | {"artifact_hash": file_hash(args.out)}
    meta_path = args.out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"\nsaved {args.out}, {meta_path.name}")

    # The meta-model trains on validation and is tested on test: score both, point in time.
    ds = load_dataset("simulated", cfg)
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    start = ds.ts.iloc[split.val].min().timestamp()
    print("point-in-time graph_score for validation + test payments", flush=True)
    history = graph_score_history(result.model, main_world, start, gcfg)
    args.history.parent.mkdir(parents=True, exist_ok=True)
    history.to_parquet(args.history, index=False)
    print(f"saved {args.history} ({len(history):,} payments, "
          f"{(history['graph_score'] > result.model.threshold).mean():.2%} above threshold) "
          f"in {time.perf_counter() - started:.0f} s")  # fmt: skip

    if not args.no_mlflow:
        mlflow = mlflow_client(gcfg["mlflow_experiment"])
        with mlflow.start_run(run_name="graphsage") as run:
            mlflow.set_tags({"model": "graph_model", "artifact_hash": meta["artifact_hash"]})
            mlflow.log_params({k: v for k, v in gcfg.items() if k != "mlflow_experiment"}
                              | {"threshold": result.model.threshold})  # fmt: skip
            mlflow.log_metrics(
                {f"val_{k}": float(v) for k, v in result.val.items()}
                | {f"end_{k}": result.at_end[k] for k in ("ring_recall", "mule_recall",
                                                           "ordinary_fpr")}
                | {f"early_{k}": result.early[k] for k in ("ring_recall", "mule_recall",
                                                            "ordinary_fpr")}
            )  # fmt: skip
            mlflow.log_dict({"features": NODE_FEATURES}, "features.json")
            mlflow.log_text(table, "metrics.txt")
            mlflow.log_artifact(str(args.out))
            mlflow.log_artifact(str(meta_path))
            print(f"MLflow run {run.info.run_id} in experiment '{gcfg['mlflow_experiment']}'")
    return 0 if result.at_end["ring_recall"] >= gcfg["min_recall"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
