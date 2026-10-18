"""Anomaly model (spec 03 §1): how unusual a payment is for normal behaviour, as a 0–1 score.

Trained on legitimate payments only, so it needs no fraud labels and can flag patterns nobody
has labelled yet (e.g. an account takeover: new phone, changed SIM, odd typing, bursts).

    python -m ml.models.anomaly_model            # train both, keep the better one on ATO
    python -m ml.models.anomaly_model --no-mlflow

Two candidates: an Isolation Forest baseline and a small PyTorch autoencoder (score = how badly
it reconstructs the row). Both see the behaviour, device and velocity features; the raw score is
min-max scaled on validation to 0–1. The one with the higher account_takeover recall on
validation is saved to ml/artifacts/anomaly_model.pt together with its scaler.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
import torch
import yaml
from sklearn.ensemble import IsolationForest
from torch import nn

from ml.features.schema import FEATURES
from ml.pipelines.datasets import Dataset
from ml.pipelines.metrics import classification_metrics, scenario_recall, threshold_for_fpr
from ml.pipelines.split import time_split
from ml.pipelines.train import DEFAULT_CONFIG, ROOT, data_hash, load_dataset, mlflow_client

ARTIFACT = ROOT / "ml" / "artifacts" / "anomaly_model.pt"
ANOMALY_GROUPS = ("behaviour", "device", "velocity")
ANOMALY_FEATURES = [f.name for f in FEATURES if f.group in ANOMALY_GROUPS]
# Long-tailed columns get log1p before scaling, so a few huge values don't dominate.
LOG_COLUMNS = [
    "session_len_s",
    "login_to_pay_s",
    "ip_distance_km",
    "txn_count_5m",
    "txn_count_1h",
    "txn_count_24h",
    "amount_sum_1h",
    "amount_sum_24h",
    "failed_count_1h",
]
SELECT_ON = "account_takeover"  # the task: keep the model that catches more of these


# --- preprocessing -----------------------------------------------------------------------------


@dataclass
class Preprocessor:
    """log1p on long-tailed columns, then standard scaling, fitted on legit training rows."""

    features: list[str]
    log_columns: list[str]
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, X: pd.DataFrame, features: list[str] = ANOMALY_FEATURES) -> "Preprocessor":
        logged = [c for c in LOG_COLUMNS if c in features]
        values = cls._log(X[features].astype("float64").to_numpy(), features, logged)
        scale = values.std(axis=0)
        return cls(features, logged, values.mean(axis=0), np.where(scale > 0, scale, 1.0))

    @staticmethod
    def _log(values: np.ndarray, features: list[str], logged: list[str]) -> np.ndarray:
        values = values.copy()
        idx = [features.index(c) for c in logged]
        values[:, idx] = np.log1p(np.clip(values[:, idx], 0, None))
        return values

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        values = self._log(
            X[self.features].astype("float64").to_numpy(), self.features, self.log_columns
        )
        return ((values - self.mean) / self.scale).astype("float32")

    def state(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "log_columns": self.log_columns,
            "mean": self.mean,
            "scale": self.scale,
        }


# --- the two candidates ------------------------------------------------------------------------


class Scorer(Protocol):
    kind: str

    def fit(self, train: np.ndarray, val: np.ndarray) -> "Scorer": ...
    def raw(self, x: np.ndarray) -> np.ndarray: ...
    def state(self) -> dict[str, Any]: ...


class IsolationForestScorer:
    """Baseline: rows that random splits isolate quickly are unusual."""

    kind = "isolation_forest"

    def __init__(self, n_estimators: int = 200, max_samples: int = 256, seed: int = 42) -> None:
        self.model = IsolationForest(
            n_estimators=n_estimators, max_samples=max_samples, random_state=seed, n_jobs=-1
        )
        self.info: dict[str, Any] = {"n_estimators": n_estimators, "max_samples": max_samples}

    def fit(self, train: np.ndarray, val: np.ndarray) -> "IsolationForestScorer":
        self.model.fit(train)
        return self

    def raw(self, x: np.ndarray) -> np.ndarray:
        return -self.model.score_samples(x)  # higher = more anomalous

    def state(self) -> dict[str, Any]:
        return {"model": self.model}

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "IsolationForestScorer":
        scorer = cls.__new__(cls)
        scorer.model, scorer.info = state["model"], {}
        return scorer


class Autoencoder(nn.Module):
    """n -> hidden... -> bottleneck -> ...hidden -> n, ReLU between layers."""

    def __init__(self, n_in: int, hidden: list[int]) -> None:
        super().__init__()
        sizes = [n_in, *hidden]
        enc: list[nn.Module] = []
        for a, b in zip(sizes, sizes[1:], strict=False):
            enc += [nn.Linear(a, b), nn.ReLU()]
        dec: list[nn.Module] = []
        back = sizes[::-1]
        for i, (a, b) in enumerate(zip(back, back[1:], strict=False)):
            dec.append(nn.Linear(a, b))
            if i < len(back) - 2:  # no activation on the output layer
                dec.append(nn.ReLU())
        self.net = nn.Sequential(*enc, *dec)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class AutoencoderScorer:
    """Learns to rebuild normal rows; a row it rebuilds badly is unusual."""

    kind = "autoencoder"

    def __init__(
        self,
        hidden: list[int] | None = None,
        epochs: int = 50,
        patience: int = 5,
        batch_size: int = 512,
        lr: float = 1e-3,
        seed: int = 42,
    ) -> None:
        self.hidden = hidden or [8, 4]
        self.epochs, self.patience, self.batch_size, self.lr, self.seed = (
            epochs,
            patience,
            batch_size,
            lr,
            seed,
        )
        self.net: Autoencoder | None = None
        self.info: dict[str, Any] = {}

    def fit(self, train: np.ndarray, val: np.ndarray) -> "AutoencoderScorer":
        torch.manual_seed(self.seed)
        gen = torch.Generator().manual_seed(self.seed)
        net = Autoencoder(train.shape[1], self.hidden)
        opt = torch.optim.Adam(net.parameters(), lr=self.lr)
        x_train, x_val = torch.from_numpy(train), torch.from_numpy(val)
        best, best_state, waited, epochs_run = float("inf"), None, 0, 0
        for epoch in range(1, self.epochs + 1):
            epochs_run = epoch
            net.train()
            for idx in torch.randperm(len(x_train), generator=gen).split(self.batch_size):
                batch = x_train[idx]
                opt.zero_grad()
                loss = nn.functional.mse_loss(net(batch), batch)
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                val_loss = float(nn.functional.mse_loss(net(x_val), x_val))
            if val_loss < best - 1e-6:
                best, waited = val_loss, 0
                best_state = {k: v.clone() for k, v in net.state_dict().items()}
            else:
                waited += 1
                if waited >= self.patience:  # early stopping on validation-legit loss
                    break
        net.load_state_dict(best_state or net.state_dict())
        self.net = net.eval()
        self.info = {"epochs_run": epochs_run, "best_val_loss": best, "hidden": self.hidden}
        return self

    def raw(self, x: np.ndarray) -> np.ndarray:
        assert self.net is not None, "fit() or load() first"
        with torch.no_grad():
            t = torch.from_numpy(np.ascontiguousarray(x, dtype="float32"))
            return ((self.net(t) - t) ** 2).mean(dim=1).numpy().astype("float64")

    def state(self) -> dict[str, Any]:
        assert self.net is not None
        return {
            "state_dict": self.net.state_dict(),
            "n_in": self.net.net[0].in_features,
            "hidden": self.hidden,
        }

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> "AutoencoderScorer":
        scorer = cls(hidden=state["hidden"])
        scorer.net = Autoencoder(state["n_in"], state["hidden"])
        scorer.net.load_state_dict(state["state_dict"])
        scorer.net.eval()
        return scorer


# --- 0-1 score and the ready-to-use model ------------------------------------------------------


@dataclass
class MinMax:
    """raw -> 0-1 with the lowest and highest raw score seen on validation; clipped outside."""

    lo: float
    hi: float

    @classmethod
    def fit(cls, raw: np.ndarray) -> "MinMax":
        return cls(float(np.min(raw)), float(np.max(raw)))

    def apply(self, raw: np.ndarray) -> np.ndarray:
        if self.hi <= self.lo:
            return np.zeros_like(raw, dtype="float64")
        return np.clip((raw - self.lo) / (self.hi - self.lo), 0.0, 1.0)


@dataclass
class AnomalyModel:
    pre: Preprocessor
    scorer: Scorer
    minmax: MinMax
    threshold: float  # 1% FPR on validation (for reports; meta_model makes the decision)

    @property
    def kind(self) -> str:
        return self.scorer.kind

    def score(self, X: pd.DataFrame) -> np.ndarray:
        """anomaly_score in [0, 1] for each row of X (needs ANOMALY_FEATURES columns)."""
        return self.minmax.apply(self.scorer.raw(self.pre.transform(X)))


def save(model: AnomalyModel, path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "kind": model.kind,
            "scorer": model.scorer.state(),
            "preprocessor": model.pre.state(),
            "minmax": {"lo": model.minmax.lo, "hi": model.minmax.hi},
            "threshold": model.threshold,
            "meta": meta,
        },
        path,
    )


def load(path: Path = ARTIFACT) -> AnomalyModel:
    # weights_only=False: the file holds a scikit-learn model too. Only load files we wrote.
    blob = torch.load(path, weights_only=False)
    scorer_cls = AutoencoderScorer if blob["kind"] == "autoencoder" else IsolationForestScorer
    return AnomalyModel(
        pre=Preprocessor(**blob["preprocessor"]),
        scorer=scorer_cls.from_state(blob["scorer"]),
        minmax=MinMax(**blob["minmax"]),
        threshold=blob["threshold"],
    )


# --- training and selection --------------------------------------------------------------------


@dataclass
class Candidate:
    model: AnomalyModel
    metrics: dict[str, dict[str, float]]  # val / test
    recall: dict[str, dict[str, float]]  # val / test, per scenario
    info: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class AnomalyResult:
    winner: Candidate
    candidates: list[Candidate]
    sizes: dict[str, int]
    data_hash: str


def choose(candidates: list[Candidate]) -> Candidate:
    """Higher validation recall on account_takeover; ties go to higher validation PR-AUC."""
    return max(
        candidates,
        key=lambda c: (c.recall["val"].get(SELECT_ON, 0.0), c.metrics["val"]["pr_auc"]),
    )


def train_anomaly(ds: Dataset, cfg: dict[str, Any]) -> AnomalyResult:
    if ds.scenario is None:
        raise ValueError(
            "the anomaly model needs the simulated dataset (behaviour/device features)"
        )
    split = time_split(ds.ts, tuple(cfg["split"][k] for k in ("train", "val", "test")))
    X = ds.X[ANOMALY_FEATURES]
    parts = {
        name: (X.iloc[p], ds.y[p], ds.scenario.iloc[p])
        for name, p in zip(("train", "val", "test"), split, strict=True)
    }
    (x_train, y_train, _), (x_val, y_val, s_val), (x_test, y_test, s_test) = parts.values()
    train_legit, val_legit = x_train[y_train == 0], x_val[y_val == 0]  # legit only

    pre = Preprocessor.fit(train_legit)
    acfg, seed, max_fpr = cfg["anomaly"], cfg["seed"], cfg["target_fpr"]
    scorers: list[Scorer] = [
        IsolationForestScorer(**acfg["isolation_forest"], seed=seed),
        AutoencoderScorer(**acfg["autoencoder"], seed=seed),
    ]
    candidates = []
    for scorer in scorers:
        started = time.perf_counter()
        scorer.fit(pre.transform(train_legit), pre.transform(val_legit))
        minmax = MinMax.fit(scorer.raw(pre.transform(x_val)))  # fitted on validation
        model = AnomalyModel(pre, scorer, minmax, threshold=0.0)
        val_scores, test_scores = model.score(x_val), model.score(x_test)
        model.threshold = threshold_for_fpr(y_val, val_scores, max_fpr)
        candidates.append(
            Candidate(
                model=model,
                metrics={
                    "val": classification_metrics(y_val, val_scores, model.threshold, max_fpr),
                    "test": classification_metrics(y_test, test_scores, model.threshold, max_fpr),
                },
                recall={
                    "val": scenario_recall(s_val, val_scores > model.threshold),
                    "test": scenario_recall(s_test, test_scores > model.threshold),
                },
                info=dict(getattr(scorer, "info", {})),
                seconds=time.perf_counter() - started,
            )
        )
    return AnomalyResult(
        winner=choose(candidates),
        candidates=candidates,
        sizes={"train_legit": len(train_legit), "val": len(y_val), "test": len(y_test)},
        data_hash=data_hash(train_legit, np.zeros(len(train_legit), dtype="int8")),
    )


# --- reporting ---------------------------------------------------------------------------------


def format_table(result: AnomalyResult) -> str:
    cands = result.candidates
    names = [c.model.kind for c in cands]
    s = result.sizes
    lines = [
        f"anomaly_model | {len(ANOMALY_FEATURES)} features (behaviour, device, velocity) | "
        f"trained on {s['train_legit']:,} legit rows | val {s['val']:,} / test {s['test']:,}",
        "",
        f"{'test metric':<24}" + "".join(f"{n:>18}" for n in names),
    ]
    for label, key in (
        ("PR-AUC", "pr_auc"),
        ("ROC-AUC", "roc_auc"),
        ("Recall @ 1% FPR", "recall_at_1pct_fpr"),
        ("FPR @ threshold", "fpr"),
    ):
        lines.append(f"{label:<24}" + "".join(f"{c.metrics['test'][key]:>18.4f}" for c in cands))
    scenarios = sorted({k for c in cands for k in c.recall["test"]})
    lines += ["", f"{'test recall @ 1% FPR':<24}" + "".join(f"{n:>18}" for n in names)]
    for sc in scenarios:
        lines.append(f"{sc:<24}" + "".join(f"{c.recall['test'].get(sc, 0):>18.1%}" for c in cands))
    lines += [
        "",
        f"{'val ' + SELECT_ON + ' recall':<24}"
        + "".join(f"{c.recall['val'].get(SELECT_ON, 0):>18.1%}" for c in cands),
        f"{'train time (s)':<24}" + "".join(f"{c.seconds:>18.1f}" for c in cands),
        "",
        f"kept: {result.winner.model.kind} (better {SELECT_ON} recall on validation)",
    ]
    return "\n".join(lines)


def log_to_mlflow(result: AnomalyResult, cfg: dict[str, Any], table: str, artifact: Path) -> str:
    mlflow = mlflow_client(cfg["anomaly"]["mlflow_experiment"])
    with mlflow.start_run(run_name=f"anomaly-{result.winner.model.kind}") as run:
        mlflow.set_tags(
            {
                "model": "anomaly_model",
                "kept": result.winner.model.kind,
                "data_hash": result.data_hash,
            }
        )
        mlflow.log_params(
            {
                "features": ",".join(ANOMALY_FEATURES),
                "log_columns": ",".join(LOG_COLUMNS),
                **{f"if_{k}": v for k, v in cfg["anomaly"]["isolation_forest"].items()},
                **{f"ae_{k}": v for k, v in cfg["anomaly"]["autoencoder"].items()},
                **{f"n_{k}": v for k, v in result.sizes.items()},
            }
        )
        for c in result.candidates:
            prefix = "if" if c.model.kind == "isolation_forest" else "ae"
            for part in ("val", "test"):
                mlflow.log_metrics({f"{prefix}_{part}_{k}": v for k, v in c.metrics[part].items()})
                mlflow.log_metrics(
                    {f"{prefix}_{part}_recall_{k}": v for k, v in c.recall[part].items()}
                )
        mlflow.log_dict({"features": ANOMALY_FEATURES}, "features.json")
        mlflow.log_text(table, "metrics.txt")
        mlflow.log_artifact(str(artifact))
        return str(run.info.run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the anomaly model (legit data only).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=ARTIFACT)
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    result = train_anomaly(load_dataset("simulated", cfg), cfg)
    table = format_table(result)
    print(table)
    w = result.winner
    meta = {
        "model": "anomaly_model",
        "kind": w.model.kind,
        "features": ANOMALY_FEATURES,
        "metrics": w.metrics,
        "scenario_recall": w.recall,
        "info": w.info,
        "data_hash": result.data_hash,
    }
    save(w.model, args.out, meta)
    print(f"\nsaved {args.out}")
    if not args.no_mlflow:
        run_id = log_to_mlflow(result, cfg, table, args.out)
        print(f"MLflow run {run_id} in experiment '{cfg['anomaly']['mlflow_experiment']}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
