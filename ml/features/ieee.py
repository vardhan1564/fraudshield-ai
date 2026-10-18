"""IEEE-CIS feature engineering for the Gate A benchmark (not used by the served UPI models).

IEEE-CIS has no customer id. The well-known trick from the Kaggle competition is a "uid" per card
holder: card1 + addr1 + the day the account was opened (transaction day - D1). Fraud sticks to a
uid, so per-uid history helps a lot. Every statistic here uses only *earlier* rows of the same
key (the frame must be sorted by TransactionDT), so nothing leaks from the future, as in
ml/features for the UPI data.
"""

import warnings

import numpy as np
import pandas as pd

SECONDS_PER_DAY = 86_400
# D-columns are "days since X"; turning them into "the day X happened" makes them stable per card.
D_COLS = ("D1", "D2", "D3", "D4", "D10", "D11", "D15")
COUNT_KEYS = ("card1", "addr1", "P_emaildomain", "uid", "uid2")
UID_KEYS = ("uid", "uid2")


def reduce_v_columns(
    df: pd.DataFrame, max_corr: float = 0.75, sample_rows: int = 100_000, seed: int = 42
) -> list[str]:
    """The V-columns worth keeping (most of the 339 are near-copies of each other).

    Columns with the same number of missing values belong to the same group; inside a group a
    column is kept only if its |correlation| with every column kept so far is <= max_corr.
    Pass only training-period rows: the choice must not look at validation or test data.
    """
    v_cols = [c for c in df.columns if c.startswith("V") and c[1:].isdigit()]
    sample = df[v_cols].sample(min(sample_rows, len(df)), random_state=seed)
    missing = sample.isna().sum()
    keep: list[str] = []
    for _, group in missing.groupby(missing, sort=True):
        cols = list(group.index)
        if len(cols) == 1:
            keep += cols
            continue
        corr = sample[cols].corr().abs().fillna(0.0)
        kept: list[str] = []
        for col in cols:
            if all(corr.at[col, k] <= max_corr for k in kept):
                kept.append(col)
        keep += kept
    order = {c: i for i, c in enumerate(v_cols)}
    return sorted(keep, key=order.__getitem__)


def _group_ids(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """One integer per distinct combination of `cols`; missing values form their own group."""
    return df.groupby(cols, dropna=False, observed=True, sort=False).ngroup().to_numpy()


def add_ieee_features(df: pd.DataFrame) -> list[str]:
    """Add engineered columns to `df` in place and return their names."""
    ts = df["TransactionDT"].to_numpy()
    if (np.diff(ts) < 0).any():
        raise ValueError("sort by TransactionDT first: history features look only backwards")
    day = ts // SECONDS_PER_DAY
    amt = df["TransactionAmt"].to_numpy(dtype="float64")
    new: dict[str, np.ndarray] = {
        "hour": ((ts // 3600) % 24).astype("float32"),
        "weekday": (day % 7).astype("float32"),
        "amt_log": np.log1p(amt).astype("float32"),
        "amt_cents": np.round(amt - np.floor(amt), 3).astype("float32"),
    }
    for col in D_COLS:
        if col in df:
            new[f"{col}n"] = (day - df[col].to_numpy(dtype="float64")).astype("float32")

    keys = pd.DataFrame(
        {
            "card1": df["card1"],
            "addr1": df["addr1"],
            "P_emaildomain": df["P_emaildomain"],
            "D1n": new.get("D1n", np.full(len(df), np.nan, dtype="float32")),
        }
    )
    groups = {k: _group_ids(keys, [k]) for k in ("card1", "addr1", "P_emaildomain")}
    groups["uid"] = _group_ids(keys, ["card1", "addr1", "D1n"])
    groups["uid2"] = _group_ids(keys, ["card1", "addr1", "D1n", "P_emaildomain"])

    for key in COUNT_KEYS:
        g = pd.Series(groups[key])
        new[f"{key}_prior_count"] = g.groupby(g).cumcount().to_numpy(dtype="float32")
    for key in UID_KEYS:
        g = pd.Series(groups[key])
        prior = new[f"{key}_prior_count"].astype("float64")
        prior_sum = pd.Series(amt).groupby(g).cumsum().to_numpy() - amt
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(prior > 0, prior_sum / prior, np.nan)
        new[f"{key}_prior_amt_mean"] = mean.astype("float32")
        new[f"{key}_amt_ratio"] = (amt / mean).astype("float32")
        last = pd.Series(ts).groupby(g).shift(1).to_numpy(dtype="float64")
        new[f"{key}_secs_since_last"] = (ts - last).astype("float32")

    with warnings.catch_warnings():  # one insert per column is intended (no full-frame copy)
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        for name, values in new.items():
            df[name] = values
    return list(new)
