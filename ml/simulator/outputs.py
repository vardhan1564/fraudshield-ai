"""Turn the World and generated rows into the five output tables and write them as parquet."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from ml.simulator.population import DAY, World
from ml.simulator.schema import (
    DEVICE_COLUMNS,
    EDGE_COLUMNS,
    OUTPUT_COLUMNS,
    REPORT_COLUMNS,
    TRANSACTION_COLUMNS,
    USER_COLUMNS,
)

ToTimestamp = Callable[[pd.Series], pd.Series]  # simulator seconds -> timestamps
FALSE_REPORT_SHARE = 0.02  # share of all reports that accuse a legit payee


def assemble(
    world: World, parts: list[pd.DataFrame], rng: np.random.Generator
) -> dict[str, pd.DataFrame]:
    start = pd.Timestamp(world.cfg.start)

    def ts(seconds: pd.Series) -> pd.Series:
        return start + pd.to_timedelta(seconds, unit="s")

    txns = _transactions(world, parts, ts)
    return {
        "transactions": txns,
        "users": _users(world, ts),
        "devices": _devices(world, txns, ts),
        "edges": _edges(world, txns, ts),
        "reports": _reports(world, rng, ts),
    }


def _transactions(world: World, parts: list[pd.DataFrame], ts: ToTimestamp) -> pd.DataFrame:
    txns = pd.concat(parts, ignore_index=True).sort_values("t", kind="stable", ignore_index=True)
    txns["ts"] = ts(txns["t"])
    seq = pd.Series(np.arange(1, len(txns) + 1)).astype(str).str.zfill(6)
    txns["txn_id"] = "TXN-" + txns["ts"].dt.strftime("%Y%m%d") + "-" + seq

    # Payee facts as of the payment time (spec 05 payees.account_user_id / account_age_days).
    payees = world.payees.drop_duplicates("vpa").set_index("vpa")
    txns["payee_user_id"] = txns["payee_vpa"].map(payees["user_id"])
    created = txns["payee_vpa"].map(payees["created_t"])
    txns["payee_account_age_days"] = ((txns["t"] - created) // DAY).clip(lower=0).astype("int64")

    txns["currency"] = "INR"
    txns["is_fraud"] = txns["scenario"].notna()
    txns["amount"] = txns["amount"].astype(float).round(2)
    txns["call_duration_s"] = txns["call_duration_s"].astype("int64")
    for col in ("geo_lat", "geo_lon"):
        txns[col] = txns[col].astype(float).round(4)
    for col in ("typing_speed_z", "session_len_s", "login_to_pay_s"):
        txns[col] = txns[col].astype(float).round(2)
    for col in ("is_vpn", "on_call", "caller_in_contacts", "screen_share_active"):
        txns[col] = txns[col].astype(bool)
    return txns[TRANSACTION_COLUMNS]


def _users(world: World, ts: ToTimestamp) -> pd.DataFrame:
    users = world.users.copy()
    users["account_created_ts"] = ts(users["created_t"])
    users["sim_changed_at"] = ts(users["sim_changed_t"])  # NaN -> NaT (no SIM change)
    users["avg_txn_amount"] = users["avg_txn_amount"].round(2)
    users["daily_rate"] = users["daily_rate"].round(3)
    for col in ("home_lat", "home_lon"):
        users[col] = users[col].round(4)
    users["is_mule"] = users["is_mule"].astype(bool)
    return users[USER_COLUMNS]


def _devices(world: World, txns: pd.DataFrame, ts: ToTimestamp) -> pd.DataFrame:
    devices = world.devices.copy()
    devices["first_seen"] = ts(devices["first_seen_t"])
    last_used = txns.groupby("device_id")["ts"].max()
    devices["last_seen"] = devices["device_id"].map(last_used).fillna(devices["first_seen"])
    devices["is_rooted"] = devices["is_rooted"].astype(bool)
    return devices[DEVICE_COLUMNS]


def _edges(world: World, txns: pd.DataFrame, ts: ToTimestamp) -> pd.DataFrame:
    """Graph edges as the worker writes them to graph_edges (spec 05)."""
    sent = pd.DataFrame(
        {
            "edge_type": "SENT",
            "src_id": txns["user_id"],
            "dst_id": txns["payee_vpa"],
            "amount": txns["amount"],
            "txn_id": txns["txn_id"],
            "ts": txns["ts"],
        }
    )
    used = pd.DataFrame(
        {
            "edge_type": "USED_DEVICE",
            "src_id": world.devices["user_id"],
            "dst_id": world.devices["fingerprint"],
            "amount": np.nan,
            "txn_id": None,
            "ts": ts(world.devices["first_seen_t"]),
        }
    )
    first_ip = txns.groupby(["user_id", "ip"], as_index=False)["ts"].min()
    logged = pd.DataFrame(
        {
            "edge_type": "LOGGED_FROM",
            "src_id": first_ip["user_id"],
            "dst_id": first_ip["ip"],
            "amount": np.nan,
            "txn_id": None,
            "ts": first_ip["ts"],
        }
    )
    edges = pd.concat([sent, used, logged], ignore_index=True)
    return edges.sort_values("ts", kind="stable", ignore_index=True)[EDGE_COLUMNS]


def _reports(world: World, rng: np.random.Generator, ts: ToTimestamp) -> pd.DataFrame:
    real = (
        pd.concat(world.reports, ignore_index=True)
        if world.reports
        else pd.DataFrame(columns=["reporter_id", "payee_vpa", "scam_type", "t"])
    )
    # A few angry or mistaken customers report legit merchants and people.
    k = round(len(real) * FALSE_REPORT_SHARE / (1 - FALSE_REPORT_SHARE))
    legit = world.payees.iloc[: world.n_merchants + world.n_customers]
    false = pd.DataFrame(
        {
            "reporter_id": world.users["user_id"].to_numpy()[
                rng.integers(0, world.n_customers, size=k)
            ],
            "payee_vpa": legit["vpa"].to_numpy()[rng.integers(0, len(legit), size=k)],
            "scam_type": rng.choice(["fake_support_refund", "investment_task", "other"], size=k),
            "t": rng.integers(0, world.end_t, size=k),
        }
    )
    reports = pd.concat([real, false], ignore_index=True)
    reports = reports[reports["t"] <= world.end_t]  # reports after the window don't exist yet
    reports = reports.sort_values("t", kind="stable", ignore_index=True)
    reports["report_id"] = [f"RPT{i + 1:06d}" for i in range(len(reports))]
    reports["ts"] = ts(reports["t"].astype("int64"))
    return reports[REPORT_COLUMNS]


def write_outputs(tables: dict[str, pd.DataFrame], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in OUTPUT_COLUMNS:
        path = out_dir / f"{name}.parquet"
        tables[name].to_parquet(path, index=False)
        paths.append(path)
    return paths
