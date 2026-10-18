"""IEEE-CIS card-uid features: hand-checked on a 5-row fixture, and no peeking at later rows."""

import numpy as np
import pandas as pd
import pytest

from ml.features.ieee import add_ieee_features, reduce_v_columns

DAY = 86_400


def _frame() -> pd.DataFrame:
    # Rows 0, 2 and 4 are one card holder (card1 1, addr1 10, account opened on day 0).
    # Row 1 is the same card and address but a different account-open day: another uid.
    return pd.DataFrame(
        {
            "TransactionDT": [0, 3600, 2 * DAY, 3 * DAY + 7200, 5 * DAY],
            "TransactionAmt": [100.0, 50.0, 300.0, 20.25, 400.0],
            "card1": [1.0, 1.0, 1.0, 2.0, 1.0],
            "addr1": [10.0, 10.0, 10.0, np.nan, 10.0],
            "D1": [0.0, 7.0, 2.0, np.nan, 5.0],
            "P_emaildomain": pd.Categorical(["a.com", "a.com", "a.com", None, "b.com"]),
        }
    )


def test_uid_history_uses_only_earlier_rows() -> None:
    df = _frame()
    names = add_ieee_features(df)
    assert set(names) >= {"uid_prior_count", "uid_prior_amt_mean", "uid_amt_ratio", "D1n"}
    assert df["D1n"].tolist()[:3] == [0.0, -7.0, 0.0]
    assert df["uid_prior_count"].tolist() == [0, 0, 1, 0, 2]
    assert df["card1_prior_count"].tolist() == [0, 1, 2, 0, 3]
    mean = df["uid_prior_amt_mean"].to_numpy()
    assert np.isnan(mean[[0, 1, 3]]).all()
    assert mean[2] == pytest.approx(100.0) and mean[4] == pytest.approx(200.0)
    assert df["uid_amt_ratio"].iloc[4] == pytest.approx(2.0)
    assert df["uid_secs_since_last"].iloc[2] == 2 * DAY and np.isnan(df["uid_secs_since_last"][0])
    # uid2 also splits on the e-mail domain, so row 4 starts a new history.
    assert df["uid2_prior_count"].tolist() == [0, 0, 1, 0, 0]
    assert df["hour"].tolist()[:2] == [0.0, 1.0] and df["amt_cents"].iloc[3] == pytest.approx(0.25)
    assert all(df[n].dtype == "float32" for n in names)


def test_future_rows_do_not_change_past_features() -> None:
    full, head = _frame(), _frame().iloc[:3].copy()
    add_ieee_features(full)
    names = add_ieee_features(head)
    pd.testing.assert_frame_equal(full[names].iloc[:3], head[names])


def test_unsorted_frame_is_rejected() -> None:
    with pytest.raises(ValueError, match="sort"):
        add_ieee_features(_frame().iloc[::-1].reset_index(drop=True))


def test_reduce_v_columns_keeps_one_of_each_near_copy() -> None:
    rng = np.random.default_rng(0)
    base, other = rng.normal(size=500), rng.normal(size=500)
    gap = np.where(np.arange(500) < 100, np.nan, 1.0)  # V4/V5 share a missing pattern
    df = pd.DataFrame(
        {
            "V1": base,
            "V2": base * 2 + 0.01 * rng.normal(size=500),  # near-copy of V1: dropped
            "V3": other,  # same missing pattern, unrelated: kept
            "V4": base * gap,
            "V5": -base * gap,  # perfectly anti-correlated with V4: dropped
            "card1": base,  # not a V-column: never considered
        }
    )
    assert reduce_v_columns(df) == ["V1", "V3", "V4"]
