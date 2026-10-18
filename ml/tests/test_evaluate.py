"""Evaluation report: loads every artifact, measures spec 03 §6 metrics, decides Gate A."""

import runpy
import shutil
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from ml.models import meta_model
from ml.pipelines.datasets import Dataset
from ml.pipelines.evaluate import (
    Report,
    Served,
    evaluate_simulated,
    gate_a,
    main,
    measure_latency,
    render,
    target_rows,
)
from ml.tests.conftest import config_for


@pytest.fixture(scope="module")
def served_folder(
    mini_artifacts: tuple[Path, Dataset], tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """A private copy of the mini artifacts plus the meta-model and thresholds.json, built with
    `python -m` semantics (run as __main__) like the real command, so the pickle loads."""
    folder = tmp_path_factory.mktemp("served")
    shutil.copytree(mini_artifacts[0], folder, dirs_exist_ok=True)
    argv = ["meta_model", "--config", str(config_for(folder)), "--artifacts", str(folder)]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "argv", [*argv, "--no-mlflow"])
        with pytest.raises(SystemExit) as done:
            runpy.run_module("ml.models.meta_model", run_name="__main__", alter_sys=True)
    assert done.value.code == 0
    return folder


@pytest.fixture(scope="module")
def report(served_folder: Path) -> Report:
    cfg = yaml.safe_load(config_for(served_folder).read_text(encoding="utf-8"))
    return evaluate_simulated(Served.load(served_folder), cfg, latency_repeats=(5, 2))


def test_gate_a_needs_ieee_pr_auc_above_080() -> None:
    assert gate_a({"pr_auc": 0.81})
    assert not gate_a({"pr_auc": 0.80})
    assert not gate_a({"pr_auc": 0.47})
    assert not gate_a(None)  # not measured is not a pass


def test_report_has_every_spec_metric(report: Report) -> None:
    final = report.models["risk_score (meta + rule), HOLD"]
    assert {"pr_auc", "roc_auc", "recall_at_1pct_fpr", "f1"} <= set(final)
    assert set(report.models) >= {"txn_model", "anomaly_model"}
    assert set(report.fairness["HOLD"]) == {"city_tier", "age_band"}
    assert "mule_ring" in report.scenarios["HOLD"]
    for shares in report.decisions.values():
        assert sum(shares.values()) == pytest.approx(1.0)
    assert report.thresholds["step_up"] <= report.thresholds["hold"]


def test_latency_covers_1_and_1000_rows(report: Report) -> None:
    assert {"all models, 1 row", "all models, 1000 rows"} <= set(report.latency)
    for t in report.latency.values():
        assert 0 < t["p50_ms"] <= t["p95_ms"]


def test_served_risk_matches_meta_model(
    served_folder: Path, mini_artifacts: tuple[Path, Dataset]
) -> None:
    served = Served.load(served_folder)
    X = mini_artifacts[1].X.head(50)
    expected = meta_model.risk_score(served.meta, served.base.scores(X), X, served.boost)
    np.testing.assert_allclose(served.risk(X), expected)
    assert set(measure_latency(served, X, 2, 1)) >= {"all models, 1 row", "all models, 50 rows"}


def test_without_ieee_gate_a_fails_and_says_why(report: Report) -> None:
    report.ieee = None
    text = render(report)
    assert "## Gate A: FAIL" in text and "not measured" in text
    assert "city_tier" in text and "1000 rows" in text
    pr = [r for r in target_rows(report) if r[0] == "PR-AUC" and r[2].startswith("IEEE")]
    assert pr[0][3] == "not measured" and pr[0][4] is False


def test_with_ieee_numbers_gate_a_passes(report: Report) -> None:
    report.ieee = {
        "run_id": "abc",
        "date": "2026-10-18",
        "stage": "tuned",
        "pr_auc": 0.85,
        "roc_auc": 0.95,
        "recall_at_1pct_fpr": 0.75,
        "f1": 0.7,
    }
    text = render(report)
    assert "## Gate A: PASS" in text and "`abc`" in text


def test_cli_writes_dated_report(served_folder: Path, tmp_path: Path) -> None:
    out = tmp_path / "reports"
    args = ["--config", str(config_for(served_folder)), "--artifacts", str(served_folder)]
    assert main([*args, "--out-dir", str(out), "--no-mlflow"]) == 0
    assert (out / f"eval_{date.today().isoformat()}.md").exists()


def test_missing_meta_model_is_a_clear_error(
    mini_artifacts: tuple[Path, Dataset], tmp_path: Path
) -> None:
    for f in ("txn_model.json", "txn_model.meta.json", "anomaly_model.pt", "social_model.txt"):
        shutil.copy(mini_artifacts[0] / f, tmp_path / f)
    with pytest.raises(FileNotFoundError, match="meta_model"):
        Served.load(tmp_path)
