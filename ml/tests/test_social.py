"""Social-engineering model: features, target, simulated coach answers, training, save/load."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from ml.features.build import training_frame
from ml.models.social_model import (
    SOCIAL_FEATURES,
    SocialResult,
    load,
    main,
    save,
    simulate_coach,
    social_target,
    train_social,
)
from ml.pipelines.datasets import from_training_frame
from ml.pipelines.train import DEFAULT_CONFIG
from ml.simulator.config import load_config
from ml.simulator.run import simulate

CFG = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
CFG["social"]["lightgbm"]["num_boost_round"] = 200  # keep the tests quick
SCAMS = CFG["social"]["scenarios"]


def test_features_are_context_payee_coach_and_amount_ratio() -> None:
    assert SOCIAL_FEATURES == [
        "payee_is_new",
        "payee_account_age_days",
        "payee_unique_senders_24h",
        "payee_reputation",
        "payee_report_count",
        "ctx_unknown_call",
        "ctx_screen_share",
        "ctx_call_long",
        "coach_red_flags",
        "amount_ratio_avg",
    ]


def test_target_is_social_engineering_only() -> None:
    scenario = pd.Series(
        [
            "digital_arrest",
            None,
            "account_takeover",
            "investment_task",
            "fake_support_refund",
            "mule_ring",
        ]
    )
    assert social_target(scenario, SCAMS).tolist() == [1, 0, 0, 1, 1, 0]


def test_simulated_coach_answers() -> None:
    n = 20_000
    scenario = pd.Series(["digital_arrest"] * n + [None] * n + ["card_testing"] * n)
    asked = np.tile([True, False], 3 * n // 2)
    flags = simulate_coach(asked, scenario, CFG["social"], np.random.default_rng(0))
    assert (flags[~asked] == 0).all()  # no coach, no answers
    assert flags.min() >= 0 and flags.max() <= 3  # 2-3 questions
    scam, legit, other = (flags[i * n : (i + 1) * n][asked[i * n : (i + 1) * n]] for i in range(3))
    # Expected YES count: 2.5 questions x chance per question (0.6 / 0.03 / 0.05).
    assert scam.mean() == pytest.approx(1.5, abs=0.05)
    assert legit.mean() == pytest.approx(0.075, abs=0.02)
    assert other.mean() == pytest.approx(0.125, abs=0.02)
    again = simulate_coach(asked, scenario, CFG["social"], np.random.default_rng(0))
    np.testing.assert_array_equal(flags, again)  # seeded


@pytest.fixture(scope="module")
def result() -> SocialResult:
    tables = simulate(load_config(n_transactions=20_000, n_users=1_000))
    frame = training_frame(
        tables["transactions"], tables["users"], tables["devices"], tables["reports"]
    )
    return train_social(from_training_frame(frame, warmup_days=14), CFG)


def test_training_reports_recall_per_scam(result: SocialResult) -> None:
    assert set(result.metrics) == {"before", "after"}
    for stage in ("before", "after"):  # every scam is listed, NaN if the test period has none
        assert set(SCAMS) <= set(result.recall[stage])
    assert result.metrics["before"]["val"]["fpr"] <= 0.01
    assert 0 < result.coach["train_coached_rows"] <= result.coach["train_step_up_rows"]
    assert result.booster.feature_name() == SOCIAL_FEATURES


def test_red_flags_raise_the_score(result: SocialResult) -> None:
    row = pd.DataFrame([dict.fromkeys(SOCIAL_FEATURES, 0.0)]).assign(
        payee_account_age_days=400.0, amount_ratio_avg=1.0
    )
    scores = [result.booster.predict(row.assign(coach_red_flags=k))[0] for k in (0, 3)]
    assert scores[1] >= scores[0]


def test_scam_context_never_lowers_the_score(result: SocialResult) -> None:
    rows = pd.DataFrame(
        np.random.default_rng(2).random((500, len(SOCIAL_FEATURES))), columns=SOCIAL_FEATURES
    ).assign(payee_account_age_days=lambda d: d.payee_account_age_days * 1000)
    for feature in CFG["social"]["monotone_increasing"]:
        off = result.booster.predict(rows.assign(**{feature: 0.0}))
        on = result.booster.predict(rows.assign(**{feature: 1.0}))
        assert (on >= off - 1e-12).all(), feature


def test_save_and_load_give_identical_scores(result: SocialResult, tmp_path: Path) -> None:
    path = tmp_path / "social_model.txt"
    meta = save(result, path)
    rows = pd.DataFrame(
        np.random.default_rng(1).random((50, len(SOCIAL_FEATURES))), columns=SOCIAL_FEATURES
    )
    np.testing.assert_allclose(
        load(path).predict(rows),
        result.booster.predict(rows, num_iteration=result.booster.best_iteration),
    )
    assert '"threshold"' in meta.read_text(encoding="utf-8")


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
    out = tmp_path / "social_model.txt"
    assert main(["--config", str(config), "--out", str(out), "--no-mlflow"]) == 0
    assert out.exists() and out.with_suffix(".meta.json").exists()
