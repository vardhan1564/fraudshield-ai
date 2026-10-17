"""Join all feature groups into the spec 03 §2 feature frame.

    python -m ml.features.build --in ml/data/simulated --out ml/data/features/simulated.parquet

build_features() and training_frame() are pure; only main() reads and writes files.
"""

import argparse
import time
from pathlib import Path

import pandas as pd

from ml.features.context import context_features
from ml.features.device import device_features
from ml.features.payee import payee_features
from ml.features.schema import DTYPES, FEATURE_NAMES, LABEL_COLUMNS, MERCHANT_CATEGORIES
from ml.features.txn import txn_features
from ml.features.velocity import velocity_features

ROOT = Path(__file__).resolve().parents[2]


def build_features(
    transactions: pd.DataFrame,
    users: pd.DataFrame,
    devices: pd.DataFrame,
    reports: pd.DataFrame,
) -> pd.DataFrame:
    """One row per transaction, indexed by txn_id, columns exactly FEATURE_NAMES."""
    parts = [
        txn_features(transactions),
        velocity_features(transactions),
        payee_features(transactions, reports),
        device_features(transactions, users, devices),
        context_features(transactions),
    ]
    frame = pd.concat(parts, axis=1)[FEATURE_NAMES]
    for name, dtype in DTYPES.items():
        if dtype == "category":
            frame[name] = pd.Categorical(frame[name], categories=MERCHANT_CATEGORIES)
        else:
            frame[name] = frame[name].astype(dtype)
    frame.index = pd.Index(transactions["txn_id"], name="txn_id")
    return frame


def training_frame(
    transactions: pd.DataFrame,
    users: pd.DataFrame,
    devices: pd.DataFrame,
    reports: pd.DataFrame,
) -> pd.DataFrame:
    """Features plus ts / is_fraud / scenario for time splits and evaluation (not model inputs)."""
    features = build_features(transactions, users, devices, reports)
    labels = transactions.set_index("txn_id")[LABEL_COLUMNS]
    return features.join(labels)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build spec 03 §2 features from simulator output.")
    parser.add_argument(
        "--in", dest="in_dir", type=Path, default=ROOT / "ml" / "data" / "simulated"
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "ml" / "data" / "features" / "simulated.parquet"
    )
    args = parser.parse_args(argv)

    started = time.perf_counter()
    tables = {
        name: pd.read_parquet(args.in_dir / f"{name}.parquet")
        for name in ("transactions", "users", "devices", "reports")
    }
    frame = training_frame(**tables)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(args.out)
    fraud = frame["is_fraud"].mean()
    elapsed = time.perf_counter() - started
    print(
        f"{len(FEATURE_NAMES)} features x {len(frame):,} rows  |  fraud {fraud:.2%}"
        f"  |  {elapsed:.1f} s  |  written to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
