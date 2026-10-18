"""Training datasets: simulated UPI data with the spec 03 §2 features, or IEEE-CIS as a benchmark.

IEEE-CIS keeps its own ~430 columns (it has none of our UPI signals); it shows the method works on
real fraud data (Gate A). The model the API serves is trained on the simulated spec features.
"""

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ml.features.build import training_frame
from ml.features.schema import FEATURE_NAMES


@dataclass(frozen=True)
class Dataset:
    name: str
    X: pd.DataFrame  # model inputs only, ordered by time
    y: np.ndarray  # 1 = fraud
    ts: pd.Series  # sort key for the time split
    scenario: pd.Series | None  # simulator ground truth, for per-scenario recall
    features: list[str]
    groups: pd.DataFrame | None = None  # city_tier, age_band per row, for fairness checks


def user_groups(transactions: pd.DataFrame, users: pd.DataFrame) -> pd.DataFrame:
    """The paying customer's city tier and age band per txn_id (fairness groups, spec 03 §6)."""
    profile = users.set_index("user_id")[["city_tier", "age_band"]]
    return profile.reindex(transactions["user_id"]).set_axis(transactions["txn_id"])


def from_training_frame(
    frame: pd.DataFrame, warmup_days: int, groups: pd.DataFrame | None = None
) -> Dataset:
    """Drop the first warmup_days: their features lack history (e.g. every payee looks new)."""
    start = frame["ts"].min() + pd.Timedelta(days=warmup_days)
    kept = frame[frame["ts"] >= start].sort_values("ts", kind="stable")
    return Dataset(
        name="simulated",
        X=kept[FEATURE_NAMES],
        y=kept["is_fraud"].astype("int8").to_numpy(),
        ts=kept["ts"],
        scenario=kept["scenario"],
        features=list(FEATURE_NAMES),
        groups=None if groups is None else groups.reindex(kept.index),
    )


def load_simulated(features_path: Path, raw_dir: Path, warmup_days: int) -> Dataset:
    raw = {
        name: pd.read_parquet(raw_dir / f"{name}.parquet")
        for name in ("transactions", "users", "devices", "reports")
        if (raw_dir / f"{name}.parquet").exists()
    }
    if features_path.exists():
        frame = pd.read_parquet(features_path)
    else:  # build on the fly from the simulator output
        frame = training_frame(raw["transactions"], raw["users"], raw["devices"], raw["reports"])
    groups = None  # fairness groups need the raw transactions and users
    if "transactions" in raw and "users" in raw:
        groups = user_groups(raw["transactions"], raw["users"])
    return from_training_frame(frame, warmup_days, groups)


# IEEE-CIS text columns (Kaggle data description); everything else is numeric.
IEEE_STRING_COLS = {"ProductCD", "card4", "card6", "P_emaildomain", "R_emaildomain"}
IEEE_STRING_COLS |= {f"M{i}" for i in range(1, 10)}
IEEE_STRING_COLS |= {
    f"id_{i}" for i in (12, 15, 16, 23, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38)
}
IEEE_STRING_COLS |= {"DeviceType", "DeviceInfo"}
IEEE_ID_COLS = {"TransactionID": "int64", "TransactionDT": "int64", "isFraud": "int8"}


def _read_ieee_csv(path: Path) -> pd.DataFrame:
    """Numbers as float32 and text as category: about half the memory of pandas' defaults."""
    columns = pd.read_csv(path, nrows=0).columns
    dtypes = {
        c: IEEE_ID_COLS.get(c, "category" if c in IEEE_STRING_COLS else "float32") for c in columns
    }
    return pd.read_csv(path, dtype=dtypes)


def load_ieee(raw_dir: Path, max_rows: int | None = None) -> Dataset:
    txn = _read_ieee_csv(raw_dir / "train_transaction.csv")
    txn = txn.sort_values("TransactionDT", kind="stable", ignore_index=True)
    if max_rows:
        txn = txn.iloc[:max_rows]
    # Add identity columns one by one (NaN where a transaction has none). A merge would copy the
    # whole ~1 GB table, which does not fit next to it on an 8 GB laptop.
    idn = _read_ieee_csv(raw_dir / "train_identity.csv").set_index("TransactionID")
    idn = idn.reindex(txn["TransactionID"])
    with warnings.catch_warnings():  # many single-column inserts are intended (no full copy)
        warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
        for col in idn.columns:
            txn[col] = idn[col].to_numpy() if idn[col].dtype != "category" else idn[col].array
    del idn

    features = [c for c in txn.columns if c not in IEEE_ID_COLS]  # not the id, label or clock
    return Dataset(
        name="ieee",
        X=txn[features],
        y=txn["isFraud"].to_numpy(),
        ts=txn["TransactionDT"],
        scenario=None,
        features=features,
    )
