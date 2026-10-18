"""Evaluation report (spec 03 §6) and Gate A: every saved model on the test split.

    python -m ml.pipelines.evaluate
    python -m ml.pipelines.evaluate --no-mlflow     # skip the IEEE-CIS lookup

Loads txn, anomaly, social and meta models plus thresholds.json from ml/artifacts, scores the
simulated test split (the same time split they were trained on) and writes
ml/reports/eval_<date>.md.

The served models are trained on simulated UPI data, whose features IEEE-CIS does not have. The
IEEE-CIS numbers come from the latest `txn_model` MLflow run tagged dataset=ieee
(`python -m ml.pipelines.train --dataset ieee`). Gate A passes only on IEEE-CIS PR-AUC > 0.80.
"""

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
import yaml
from sklearn.calibration import CalibratedClassifierCV

from ml.features.schema import FEATURE_NAMES
from ml.models import social_model
from ml.models.meta_model import (
    ARTIFACTS,
    SCORE_COLUMNS,
    BaseModels,
    decide,
    risk_score,
    rule_hits,
    split_for,
)
from ml.pipelines.metrics import classification_metrics, fpr_gap, scenario_recall
from ml.pipelines.train import DEFAULT_CONFIG, ROOT, load_dataset, mlflow_client
from ml.simulator.schema import SCENARIOS

REPORTS = ROOT / "ml" / "reports"

# spec 03 §6 targets
PR_AUC_MIN = 0.80
ROC_AUC_MIN = 0.92
RECALL_AT_1PCT_MIN = 0.70
SCENARIO_RECALL_MIN = 0.80
FPR_GAP_MAX_POINTS = 2.0
LATENCY_1_ROW_MAX_MS = 30.0


@dataclass
class Served:
    """Everything the API will load: base models, meta-model and thresholds."""

    base: BaseModels
    meta: CalibratedClassifierCV
    thresholds: dict[str, Any]
    social_meta: dict[str, Any]

    @classmethod
    def load(cls, folder: Path = ARTIFACTS) -> "Served":
        missing = [f for f in ("meta_model.pkl", "thresholds.json") if not (folder / f).exists()]
        if missing:
            raise FileNotFoundError(f"run python -m ml.models.meta_model first, missing: {missing}")
        return cls(
            base=BaseModels.load(folder),
            meta=joblib.load(folder / "meta_model.pkl"),
            thresholds=json.loads((folder / "thresholds.json").read_text(encoding="utf-8")),
            social_meta=json.loads((folder / "social_model.meta.json").read_text(encoding="utf-8")),
        )

    @property
    def boost(self) -> float:
        return float(self.thresholds["risk_rules"]["unknown_call_new_payee"]["add"])

    def risk(self, X: pd.DataFrame) -> np.ndarray:
        """Final risk_score, exactly as the scoring endpoint will compute it."""
        return risk_score(self.meta, self.base.scores(X), X, self.boost)


@dataclass
class Report:
    created: str
    sizes: dict[str, int]
    models: dict[str, dict[str, float]]
    decisions: dict[str, dict[str, float]]
    scenarios: dict[str, dict[str, float]]
    fairness: dict[str, dict[str, float]]
    latency: dict[str, dict[str, float]]
    thresholds: dict[str, float]
    rule_hit_rate: dict[str, float]
    boost: float
    ieee: dict[str, Any] | None = None
    provenance: dict[str, str] = field(default_factory=dict)


# --- measurements ------------------------------------------------------------------------------


def _decision_shares(decisions: np.ndarray, y: np.ndarray) -> dict[str, dict[str, float]]:
    out = {}
    for label, mask in (("legit", y == 0), ("fraud", y == 1)):
        counts = pd.Series(decisions[mask]).value_counts(normalize=True)
        out[label] = {d: float(counts.get(d, 0.0)) for d in ("APPROVE", "STEP_UP", "HOLD")}
    return out


def _timings_ms(fn: Callable[[], object], repeats: int) -> dict[str, float]:
    fn()  # warm-up: first calls pay for lazy initialisation
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return {"p50_ms": float(np.percentile(times, 50)), "p95_ms": float(np.percentile(times, 95))}


def measure_latency(
    served: Served, X: pd.DataFrame, repeats_1: int = 200, repeats_1000: int = 20
) -> dict[str, dict[str, float]]:
    """All models end to end (base scores -> meta-model -> rule) for 1 row and 1000 rows,
    plus each stage alone for 1 row. Single-threaded Python, no batching tricks."""
    one = X.iloc[[len(X) // 2]]
    batch = X.iloc[:1000]
    scores = served.base.scores(one)
    social_in = social_model._frame(one).assign(coach_red_flags=0.0)
    return {
        "all models, 1 row": _timings_ms(lambda: served.risk(one), repeats_1),
        f"all models, {len(batch)} rows": _timings_ms(lambda: served.risk(batch), repeats_1000),
        "txn_model, 1 row": _timings_ms(
            lambda: served.base.txn.predict(
                xgb.DMatrix(one[FEATURE_NAMES], enable_categorical=True)
            ),
            repeats_1,
        ),
        "anomaly_model, 1 row": _timings_ms(lambda: served.base.anomaly.score(one), repeats_1),
        "social_model, 1 row": _timings_ms(
            lambda: served.base.social.predict(social_in), repeats_1
        ),
        "meta_model + rule, 1 row": _timings_ms(
            lambda: risk_score(served.meta, scores, one, served.boost), repeats_1
        ),
    }


def evaluate_simulated(
    served: Served, cfg: dict[str, Any], *, latency_repeats: tuple[int, int] = (200, 20)
) -> Report:
    ds = load_dataset("simulated", cfg)
    if ds.scenario is None or ds.groups is None:
        raise ValueError("evaluation needs the simulator's scenario and fairness columns")
    split = split_for(ds, cfg, served.base)
    X, y = ds.X.iloc[split.test], ds.y[split.test]
    scenario, groups = ds.scenario.iloc[split.test], ds.groups.iloc[split.test]

    base_scores = served.base.scores(X)
    risk = risk_score(served.meta, base_scores, X, served.boost)
    th = {k: float(served.thresholds[k]) for k in ("step_up", "hold")}
    social_y = social_model.social_target(scenario, cfg["social"]["scenarios"])

    # each model at its own saved threshold, then the final risk_score at HOLD
    models = {
        "txn_model": classification_metrics(
            y, base_scores["txn_score"].to_numpy(), served.base.txn_meta["threshold"]
        ),
        "anomaly_model": classification_metrics(
            y, base_scores["anomaly_score"].to_numpy(), served.base.anomaly.threshold
        ),
        "social_model (target: scams only)": classification_metrics(
            social_y, base_scores["social_eng_score"].to_numpy(), served.social_meta["threshold"]
        ),
        "risk_score (meta + rule), HOLD": classification_metrics(y, risk, th["hold"]),
    }
    decisions = decide(risk, th)
    hold, flagged = decisions == "HOLD", decisions != "APPROVE"
    hits = rule_hits(X)
    return Report(
        created=date.today().isoformat(),
        sizes={"test_rows": len(y), "test_fraud": int(y.sum())},
        models=models,
        decisions=_decision_shares(decisions, y),
        scenarios={
            "HOLD": scenario_recall(scenario, hold, list(SCENARIOS)),
            "STEP_UP or HOLD": scenario_recall(scenario, flagged, list(SCENARIOS)),
        },
        fairness={
            "HOLD": fpr_gap(y, hold, groups[["city_tier", "age_band"]]),
            "STEP_UP or HOLD": fpr_gap(y, flagged, groups[["city_tier", "age_band"]]),
        },
        latency=measure_latency(served, X, *latency_repeats),
        thresholds=th,
        rule_hit_rate={"legit": float(hits[y == 0].mean()), "fraud": float(hits[y == 1].mean())},
        boost=served.boost,
        provenance={
            "txn_model": f"v{served.base.txn_meta.get('version')}, data "
            f"{served.base.txn_meta.get('data_hash')}",
            "thresholds": str(served.thresholds.get("created")),
            "inputs": ", ".join(SCORE_COLUMNS),
        },
    )


def ieee_from_mlflow(experiment: str) -> dict[str, Any] | None:
    """Test metrics of the newest txn_model run on IEEE-CIS, or None if there is none."""
    from mlflow.exceptions import MlflowException

    try:
        mlflow = mlflow_client(experiment)
        runs = mlflow.search_runs(
            experiment_names=[experiment],
            filter_string="tags.dataset = 'ieee'",
            order_by=["attributes.start_time DESC"],
            max_results=1,
        )
    except (MlflowException, OSError) as exc:
        print(f"IEEE-CIS lookup in MLflow failed: {exc}", file=sys.stderr)
        return None
    if runs.empty:
        return None
    run = runs.iloc[0]
    keys = ("pr_auc", "roc_auc", "recall_at_1pct_fpr", "f1", "recall", "fpr", "fraud_rate")
    return {
        "run_id": str(run["run_id"]),
        "date": str(run["start_time"])[:10],
        "stage": str(run.get("tags.stage", "")),
        **{k: float(run[f"metrics.test_{k}"]) for k in keys if f"metrics.test_{k}" in run},
    }


def gate_a(ieee: dict[str, Any] | None) -> bool:
    """Gate A (implementation plan, P2): PR-AUC > 0.80 on the IEEE-CIS test split."""
    return ieee is not None and ieee.get("pr_auc", 0.0) > PR_AUC_MIN


# --- markdown ----------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return "n/a" if np.isnan(x) else f"{x:.1%}"


def _ok(passed: bool) -> str:
    return "✅ pass" if passed else "❌ fail"


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def target_rows(report: Report) -> list[tuple[str, str, str, str, bool | None]]:
    """(metric, target, data, measured, passed) for every spec 03 §6 line."""
    final = report.models["risk_score (meta + rule), HOLD"]
    ieee = report.ieee
    rows: list[tuple[str, str, str, str, bool | None]] = []
    for key, label, target in (
        ("pr_auc", "PR-AUC", PR_AUC_MIN),
        ("roc_auc", "ROC-AUC", ROC_AUC_MIN),
        ("recall_at_1pct_fpr", "Recall @ 1% FPR", RECALL_AT_1PCT_MIN),
    ):
        shown = f"{target:.0%}" if key.startswith("recall") else f"{target:.2f}"
        if ieee is None or key not in ieee:
            rows.append((label, f"> {shown}", "IEEE-CIS txn_model", "not measured", False))
        else:
            value = ieee[key]
            txt = _pct(value) if key.startswith("recall") else f"{value:.3f}"
            rows.append((label, f"> {shown}", "IEEE-CIS txn_model", txt, value > target))
        value = final[key]
        txt = _pct(value) if key.startswith("recall") else f"{value:.3f}"
        rows.append((label, f"> {shown}", "simulated risk_score", txt, value > target))
    if ieee is not None and "f1" in ieee:
        rows.append(
            ("F1 at chosen threshold", "report", "IEEE-CIS txn_model", f"{ieee['f1']:.3f}", None)
        )
    rows.append(("F1 at chosen threshold", "report", "simulated, HOLD", f"{final['f1']:.3f}", None))
    held = {k: v for k, v in report.scenarios["HOLD"].items() if not np.isnan(v)}
    worst = min(held, key=held.__getitem__)
    rows.append(
        (
            "Per-scenario recall",
            f"each ≥ {SCENARIO_RECALL_MIN:.0%}",
            "simulated, HOLD",
            f"lowest {worst} {_pct(held[worst])}",
            min(held.values()) >= SCENARIO_RECALL_MIN,
        )
    )
    for col in ("city_tier", "age_band"):
        gap = report.fairness["HOLD"][col]
        rows.append(
            (
                f"FPR gap by {col}",
                f"< {FPR_GAP_MAX_POINTS:.0f} points",
                "simulated, HOLD",
                f"{gap:.2f} pt",
                gap < FPR_GAP_MAX_POINTS,
            )
        )
    one = report.latency["all models, 1 row"]
    rows.append(
        (
            "Inference latency, all models, 1 row",
            f"< {LATENCY_1_ROW_MAX_MS:.0f} ms",
            "simulated",
            f"p50 {one['p50_ms']:.1f} ms / p95 {one['p95_ms']:.1f} ms",
            one["p95_ms"] < LATENCY_1_ROW_MAX_MS,
        )
    )
    return rows


def render(report: Report) -> str:
    passed = gate_a(report.ieee)
    ieee = report.ieee
    lines = [
        f"# Model evaluation, {report.created}",
        "",
        "Generated by `python -m ml.pipelines.evaluate` (spec 03 §6). Commit "
        f"`{_git_commit()}`; txn_model {report.provenance.get('txn_model')}; thresholds from "
        f"{report.provenance.get('thresholds')}.",
        "",
        f"## Gate A: {'PASS ✅' if passed else 'FAIL ❌'}",
        "",
    ]
    if ieee is None:
        lines.append(
            "No IEEE-CIS run found in MLflow, so PR-AUC on IEEE-CIS is not measured. Run "
            "`python -m ml.pipelines.train --dataset ieee` with MLflow up, then re-run this report."
        )
    else:
        lines.append(
            f"IEEE-CIS test PR-AUC **{ieee['pr_auc']:.3f}** vs target > {PR_AUC_MIN:.2f} "
            f"(txn_model {ieee['stage']} run `{ieee['run_id']}`, {ieee['date']}, "
            f"fraud rate {_pct(ieee.get('fraud_rate', float('nan')))})."
        )
    lines += [
        "",
        "## Targets (spec 03 §6)",
        "",
        "| Metric | Target | Data | Measured | Result |",
        "|---|---|---|---|---|",
    ]
    for metric, target, data, measured, ok in target_rows(report):
        lines.append(
            f"| {metric} | {target} | {data} | {measured} | {'report' if ok is None else _ok(ok)} |"
        )

    lines += [
        "",
        f"## Each model on the simulated test split ({report.sizes['test_rows']:,} payments, "
        f"{report.sizes['test_fraud']:,} fraud)",
        "",
        "Each base model at its own saved threshold (1% validation FPR). The social model is "
        "graded on its own target (digital arrest, fake support refund, investment task), "
        "before any Scam Coach answers.",
        "",
        "| Model | PR-AUC | ROC-AUC | Recall @ 1% FPR | Recall | FPR | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, m in report.models.items():
        lines.append(
            f"| {name} | {m['pr_auc']:.3f} | {m['roc_auc']:.4f} | {_pct(m['recall_at_1pct_fpr'])} "
            f"| {_pct(m['recall'])} | {m['fpr']:.2%} | {m['f1']:.3f} |"
        )

    th = report.thresholds
    lines += [
        "",
        f"## Decisions (STEP_UP > {th['step_up']:.4f}, HOLD > {th['hold']:.4f})",
        "",
        "| Payments | APPROVE | STEP_UP | HOLD |",
        "|---|---|---|---|",
    ]
    for label, shares in report.decisions.items():
        lines.append(
            f"| {label} | {_pct(shares['APPROVE'])} | {_pct(shares['STEP_UP'])} | "
            f"{_pct(shares['HOLD'])} |"
        )
    lines += [
        "",
        f"Unknown-call rule (spec 01 F2, +{report.boost:.2f}) fires on "
        f"{_pct(report.rule_hit_rate['legit'])} of legit and {_pct(report.rule_hit_rate['fraud'])} "
        "of fraud payments.",
        "",
        "## Recall per fraud scenario (target each ≥ 80%)",
        "",
        "| Scenario | HOLD | STEP_UP or HOLD |",
        "|---|---|---|",
    ]
    for name in report.scenarios["HOLD"]:
        lines.append(
            f"| {name} | {_pct(report.scenarios['HOLD'][name])} | "
            f"{_pct(report.scenarios['STEP_UP or HOLD'][name])} |"
        )
    lines += [
        "",
        "## Fairness: largest FPR gap between groups, legit payments only (target < 2 points)",
        "",
        "| Decision | city_tier | age_band |",
        "|---|---|---|",
    ]
    for label, gaps in report.fairness.items():
        lines.append(f"| {label} | {gaps['city_tier']:.2f} pt | {gaps['age_band']:.2f} pt |")
    lines += [
        "",
        "## Inference latency (this machine, single process, models loaded once)",
        "",
        "| Call | p50 | p95 |",
        "|---|---|---|",
    ]
    for label, t in report.latency.items():
        lines.append(f"| {label} | {t['p50_ms']:.2f} ms | {t['p95_ms']:.2f} ms |")
    if ieee is not None:
        lines += [
            "",
            "## IEEE-CIS (txn_model only)",
            "",
            "The served models use UPI features (payee, device, call context) that IEEE-CIS does "
            "not have, so only the transaction model is trained and scored on it.",
            "",
            "| PR-AUC | ROC-AUC | Recall @ 1% FPR | Recall | FPR | F1 |",
            "|---|---|---|---|---|---|",
            f"| {ieee.get('pr_auc', float('nan')):.3f} | {ieee.get('roc_auc', float('nan')):.3f} | "
            f"{_pct(ieee.get('recall_at_1pct_fpr', float('nan')))} | "
            f"{_pct(ieee.get('recall', float('nan')))} | {_pct(ieee.get('fpr', float('nan')))} | "
            f"{ieee.get('f1', float('nan')):.3f} |",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate every saved model; write the report.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    parser.add_argument("--out-dir", type=Path, default=REPORTS)
    parser.add_argument("--no-mlflow", action="store_true", help="skip the IEEE-CIS lookup")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    report = evaluate_simulated(Served.load(args.artifacts), cfg)
    if not args.no_mlflow:
        report.ieee = ieee_from_mlflow(cfg["mlflow"]["experiment"])

    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"eval_{report.created}.md"
    path.write_text(render(report), encoding="utf-8")
    for metric, target, data, measured, ok in target_rows(report):
        verdict = "report" if ok is None else ("pass" if ok else "FAIL")
        print(f"{metric:<38} {target:<14} {data:<22} {measured:<34} {verdict}")
    print(f"\nGate A: {'PASS' if gate_a(report.ieee) else 'FAIL'}\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
