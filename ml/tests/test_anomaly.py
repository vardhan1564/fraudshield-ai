"""Anomaly model: preprocessing, scorers on synthetic outliers, 0-1 score, save/load, choice."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ml.features.build import training_frame
from ml.models.anomaly_model import (
    ANOMALY_FEATURES,
    LOG_COLUMNS,
    AnomalyResult,
    AutoencoderScorer,
    Candidate,
    IsolationForestScorer,
    MinMax,
    Preprocessor,
    choose,
    load,
    main,
    save,
    train_anomaly,
)
from ml.pipelines.datasets import from_training_frame
from ml.pipelines.train import DEFAULT_CONFIG
from ml.simulator.config import load_config
from ml.simulator.run import simulate

CFG = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
CFG["anomaly"]["autoencoder"]["epochs"] = 15  # keep the tests quick


def synthetic(n: int, seed: int) -> pd.DataFrame:
    """Normal-looking rows: small counts and sums, old devices, typing near the user's norm."""
    rng = np.random.default_rng(seed)
    data = {c: rng.poisson(1.0, n).astype(float) for c in ANOMALY_FEATURES}
    data |= {
        "typing_speed_z": rng.normal(0, 1, n),
        "session_len_s": rng.lognormal(np.log(60), 0.4, n),
        "login_to_pay_s": rng.lognormal(np.log(25), 0.4, n),
        "ip_distance_km": rng.exponential(2.0, n),
        "amount_sum_1h": rng.lognormal(6, 0.5, n),
        "amount_sum_24h": rng.lognormal(7, 0.5, n),
    }
    for flag in ("device_is_new", "device_rooted", "is_vpn", "sim_changed_7d"):
        data[flag] = (rng.random(n) < 0.01).astype(float)
    return pd.DataFrame(data)[ANOMALY_FEATURES]


def takeover_like(n: int, seed: int) -> pd.DataFrame:
    """Account-takeover-like rows: new phone, new SIM, odd typing, far away, bursts."""
    frame = synthetic(n, seed)
    frame[["device_is_new", "sim_changed_7d", "is_vpn"]] = 1.0
    frame["typing_speed_z"] = 4.5
    frame["ip_distance_km"] = 900.0
    frame["login_to_pay_s"] = 4.0
    frame["txn_count_1h"] = 6.0
    return frame


NORMAL_TRAIN, NORMAL_VAL, NORMAL_TEST = synthetic(3000, 1), synthetic(500, 2), synthetic(500, 3)
ODD = takeover_like(50, 4)


# --- preprocessing -----------------------------------------------------------------------------


def test_features_come_from_the_schema_groups() -> None:
    assert len(ANOMALY_FEATURES) == 14
    assert {"typing_speed_z", "device_is_new", "sim_changed_7d", "txn_count_1h"} <= set(
        ANOMALY_FEATURES
    )
    assert "amount" not in ANOMALY_FEATURES and "ctx_unknown_call" not in ANOMALY_FEATURES
    assert set(LOG_COLUMNS) <= set(ANOMALY_FEATURES)


def test_preprocessor_scales_train_to_mean_0_std_1() -> None:
    pre = Preprocessor.fit(NORMAL_TRAIN)
    x = pre.transform(NORMAL_TRAIN)
    assert x.dtype == np.float32 and x.shape == (3000, 14)
    np.testing.assert_allclose(x.mean(axis=0), 0, atol=1e-4)
    # log1p first: a 1000x larger sum is only a few standard deviations away, not thousands
    big = NORMAL_TRAIN.head(1).assign(amount_sum_24h=NORMAL_TRAIN["amount_sum_24h"].iloc[0] * 1000)
    col = ANOMALY_FEATURES.index("amount_sum_24h")
    assert pre.transform(big)[0, col] < 20


def test_minmax_maps_validation_range_to_0_1_and_clips() -> None:
    mm = MinMax.fit(np.array([2.0, 4.0, 6.0]))
    np.testing.assert_allclose(mm.apply(np.array([2.0, 4.0, 6.0, 0.0, 10.0])), [0, 0.5, 1, 0, 1])
    assert (MinMax(1.0, 1.0).apply(np.array([5.0])) == 0).all()  # degenerate range


# --- both scorers: trained on normal rows, takeover-like rows score higher ----------------------


@pytest.mark.parametrize(
    "scorer",
    [IsolationForestScorer(seed=0), AutoencoderScorer(epochs=30, seed=0)],
    ids=["isolation_forest", "autoencoder"],
)
def test_outliers_score_higher(scorer: IsolationForestScorer | AutoencoderScorer) -> None:
    pre = Preprocessor.fit(NORMAL_TRAIN)
    scorer.fit(pre.transform(NORMAL_TRAIN), pre.transform(NORMAL_VAL))
    normal, odd = scorer.raw(pre.transform(NORMAL_TEST)), scorer.raw(pre.transform(ODD))
    assert np.median(odd) > np.quantile(normal, 0.99)  # every-day rows vs takeover-like rows


def test_autoencoder_is_deterministic_with_a_seed() -> None:
    pre = Preprocessor.fit(NORMAL_TRAIN)
    a = AutoencoderScorer(epochs=5, seed=7).fit(
        pre.transform(NORMAL_TRAIN), pre.transform(NORMAL_VAL)
    )
    b = AutoencoderScorer(epochs=5, seed=7).fit(
        pre.transform(NORMAL_TRAIN), pre.transform(NORMAL_VAL)
    )
    x = pre.transform(NORMAL_TEST)
    np.testing.assert_array_equal(a.raw(x), b.raw(x))


# --- choosing the model ------------------------------------------------------------------------


def _cand(ato: float, pr_auc: float, kind: str) -> Candidate:
    model = type("M", (), {"kind": kind})()
    return Candidate(
        model=model, metrics={"val": {"pr_auc": pr_auc}}, recall={"val": {"account_takeover": ato}}
    )  # type: ignore[arg-type]


def test_choose_prefers_account_takeover_recall_then_pr_auc() -> None:
    assert choose([_cand(0.6, 0.9, "if"), _cand(0.8, 0.1, "ae")]).model.kind == "ae"
    assert choose([_cand(0.8, 0.3, "if"), _cand(0.8, 0.2, "ae")]).model.kind == "if"


# --- end to end on simulated data --------------------------------------------------------------


@pytest.fixture(scope="module")
def result() -> AnomalyResult:
    tables = simulate(load_config(n_transactions=20_000, n_users=1_000))
    frame = training_frame(
        tables["transactions"], tables["users"], tables["devices"], tables["reports"]
    )
    return train_anomaly(from_training_frame(frame, warmup_days=14), CFG)


def test_end_to_end_scores_and_reports(result: AnomalyResult) -> None:
    assert [c.model.kind for c in result.candidates] == ["isolation_forest", "autoencoder"]
    for c in result.candidates:
        assert set(c.metrics) == {"val", "test"} and "account_takeover" in c.recall["test"]
        assert c.metrics["val"]["fpr"] <= 0.01
    assert result.winner is choose(result.candidates)
    # A takeover changes device, SIM and typing: whichever model is kept must catch most of them.
    assert result.winner.recall["test"]["account_takeover"] >= 0.5


def test_save_and_load_give_identical_scores(result: AnomalyResult, tmp_path: Path) -> None:
    path = tmp_path / "anomaly_model.pt"
    for cand in result.candidates:  # both kinds round-trip
        save(cand.model, path, {"kind": cand.model.kind})
        loaded = load(path)
        assert loaded.kind == cand.model.kind
        x = NORMAL_TEST.head(100)
        np.testing.assert_allclose(loaded.score(x), cand.model.score(x), rtol=1e-6)
        scores = loaded.score(pd.concat([NORMAL_TEST, ODD]))
        assert scores.min() >= 0 and scores.max() <= 1


def test_cli_without_mlflow(tmp_path: Path) -> None:
    sim = simulate(load_config(n_transactions=20_000, n_users=1_000))
    for name in ("transactions", "users", "devices", "reports"):
        sim[name].to_parquet(tmp_path / f"{name}.parquet", index=False)
    cfg = {
        **CFG,
        "datasets": {
            "simulated": {
                "features": str(tmp_path / "missing.parquet"),
                "raw_dir": str(tmp_path),
                "warmup_days": 14,
            }
        },
    }
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    out = tmp_path / "anomaly_model.pt"
    assert main(["--config", str(config), "--out", str(out), "--no-mlflow"]) == 0
    assert load(out).score(NORMAL_TEST).shape == (500,)
