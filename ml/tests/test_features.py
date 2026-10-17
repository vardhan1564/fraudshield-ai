"""Offline feature builders (spec 03 §2) on a 50-row hand-made fixture with hand-computed values."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.features.build import build_features, training_frame
from ml.features.build import main as build_main
from ml.features.context import GRAPH_FEATURES, context_features
from ml.features.device import device_features
from ml.features.payee import payee_features
from ml.features.schema import (
    DTYPES,
    FEATURE_NAMES,
    FEATURES_JSON,
    LABEL_COLUMNS,
    MERCHANT_CATEGORIES,
    to_json,
)
from ml.features.txn import txn_features
from ml.features.velocity import velocity_features
from ml.simulator.config import load_config
from ml.simulator.run import simulate

T0 = pd.Timestamp("2026-07-01 00:00", tz="+05:30")  # a Wednesday
MUMBAI, PUNE, DELHI = (19.08, 72.88), (18.52, 73.86), (28.61, 77.21)
HOME = {"U1": MUMBAI, "U2": PUNE, "U3": DELHI, "U4": DELHI}


def at(day: float, hh: int = 0, mm: int = 0) -> pd.Timestamp:
    return T0 + pd.Timedelta(days=day, hours=hh, minutes=mm)


COLLECTOR = "collector@ybl"

# (txn_id, user, ts, payee, amount, overrides). Comments give the point of each row.
ROWS: list[tuple[str, str, pd.Timestamp, str, float, dict[str, object]]] = [
    # U1: velocity window edges and the 30-day average
    ("a1", "U1", at(0, 0, 0), "shop.a", 100, {}),  # first payment: no history
    ("a2", "U1", at(0, 0, 3), "shop.a", 200, {}),
    ("a3", "U1", at(0, 0, 8), "shop.b", 50, {"status": "FAILED"}),  # a2 is exactly 5 min back
    ("a4", "U1", at(0, 1, 0), "shop.a", 300, {}),  # a1 is exactly 1 h back
    ("a5", "U1", at(0, 1, 1), "shop.b", 10, {}),  # shop.b only failed before -> still new
    ("a6", "U1", at(0, 1, 1), "shop.c", 20, {"channel": "CARD"}),  # same second as a5
    ("a7", "U1", at(16, 10, 0), "shop.d", 1000, {"device_id": "D1new"}),  # SIM changed 6 d ago
    ("a8", "U1", at(18, 12, 0), "shop.a", 126, {"device_id": "D1b"}),  # SIM changed 8.5 d ago
    ("a9", "U1", at(45, 12, 0), "shop.a", 500, {}),  # only a7, a8 inside 30 days
    # U2: context flags and distance (pays from Mumbai, lives in Pune)
    (
        "b1",
        "U2",
        at(1, 12, 0),
        "u3pay@ybl",
        500,
        {
            "geo": MUMBAI,
            "channel": "WALLET",
            "merchant_category": "P2P",
            "on_call": True,
            "call_duration_s": 400,
            "remote_access_app": "AnyDesk",
        },
    ),
    (
        "b2",
        "U2",
        at(1, 12, 30),
        "u3pay@ybl",
        100,
        {
            "merchant_category": "P2P",
            "on_call": True,
            "caller_in_contacts": True,
            "call_duration_s": 300,
        },
    ),
    # Collector: distinct senders in 24 h and reports before the payment
    ("c1", "U2", at(20, 10, 0), COLLECTOR, 1000, {"merchant_category": "P2P"}),
    ("c2", "U3", at(20, 11, 0), COLLECTOR, 1000, {"merchant_category": "P2P"}),
    ("c3", "U2", at(20, 12, 0), COLLECTOR, 1000, {"merchant_category": "P2P"}),
    ("c4", "U4", at(20, 13, 0), COLLECTOR, 1000, {"merchant_category": "P2P"}),  # U2 counts once
    ("c5", "U4", at(21, 10, 30), COLLECTOR, 1000, {"merchant_category": "P2P"}),  # c1 drops out
    ("c6", "U3", at(21, 13, 0), COLLECTOR, 1000, {"merchant_category": "P2P"}),  # c4 exactly 24 h
    # U3: night edges
    ("n1", "U3", at(2, 23, 30), "shop.e", 50, {}),
    ("n2", "U3", at(3, 4, 59), "shop.e", 50, {}),
    ("n3", "U3", at(3, 5, 0), "shop.e", 50, {"screen_share_active": True}),
]
# U4: one grocery payment a day to make 50 rows.
ROWS += [(f"f{i:02d}", "U4", at(25 + i, 12, 0), "grocery.f", 100, {}) for i in range(30)]


def make_transactions() -> pd.DataFrame:
    records = []
    for txn_id, user, ts, payee, amount, extra in ROWS:
        geo = extra.get("geo", HOME[user])  # read, never pop: ROWS is shared by every test
        row = {
            "txn_id": txn_id,
            "user_id": user,
            "payee_vpa": payee,
            "payee_account_age_days": 3 if payee == COLLECTOR else 100,
            "amount": float(amount),
            "channel": "UPI",
            "merchant_category": "GROCERY",
            "device_id": f"D{user[1]}",
            "is_vpn": False,
            "geo_lat": geo[0],
            "geo_lon": geo[1],
            "ts": ts,
            "status": "SUCCESS",
            "on_call": False,
            "caller_in_contacts": False,
            "call_duration_s": 0,
            "screen_share_active": False,
            "remote_access_app": None,
            "typing_speed_z": 0.1,
            "session_len_s": 60.0,
            "login_to_pay_s": 20.0,
            "scenario": None,
            "is_fraud": False,
        }
        row.update({k: v for k, v in extra.items() if k != "geo"})
        records.append(row)
    return pd.DataFrame(records)


@pytest.fixture
def txns() -> pd.DataFrame:
    return make_transactions()


def by_id(features: pd.DataFrame, txns: pd.DataFrame) -> pd.DataFrame:
    return features.set_axis(txns["txn_id"].to_numpy())


def test_fixture_has_50_rows(txns: pd.DataFrame) -> None:
    assert len(txns) == 50 and txns["txn_id"].is_unique


# --- schema ------------------------------------------------------------------------------------


def test_schema_matches_spec_and_json() -> None:
    assert len(FEATURE_NAMES) == 38 and len(set(FEATURE_NAMES)) == 38
    assert FEATURE_NAMES[:3] == ["amount", "amount_log", "amount_ratio_avg"]
    assert FEATURE_NAMES[-5:] == [
        "graph_score",
        "graph_degree",
        "graph_pagerank",
        "hops_to_fraud",
        "community_fraud_ratio",
    ]
    assert set(DTYPES.values()) == {"float64", "int64", "bool", "category"}
    assert json.loads(FEATURES_JSON.read_text(encoding="utf-8")) == to_json()


# --- txn ---------------------------------------------------------------------------------------


def test_amount_ratio_avg(txns: pd.DataFrame) -> None:
    f = by_id(txn_features(txns), txns)["amount_ratio_avg"]
    assert f["a1"] == 1.0  # no history
    assert f["a2"] == pytest.approx(2.0)  # 200 / 100
    assert f["a3"] == pytest.approx(50 / 150)
    assert f["a5"] == pytest.approx(10 / 200)  # failed a3 is not in the average
    assert f["a7"] == pytest.approx(1000 / 126)  # (100+200+300+10+20) / 5
    assert f["a8"] == pytest.approx(126 / (1630 / 6))  # a1-a7 successes
    assert f["a9"] == pytest.approx(500 / 563)  # older than 30 days dropped


def test_time_and_channel(txns: pd.DataFrame) -> None:
    f = by_id(txn_features(txns), txns)
    assert (f.loc["a1", "hour"], f.loc["a1", "weekday"]) == (0, 2)  # Wed 00:00
    assert (f.loc["n1", "hour"], f.loc["n1", "weekday"]) == (23, 4)  # Fri 23:30
    assert f.loc[["a1", "n1", "n2"], "is_night"].all()  # 00:00, 23:30, 04:59
    assert not f.loc["n3", "is_night"]  # 05:00
    assert f.loc["a6", "channel_card"] and not f.loc["a6", "channel_upi"]
    assert f.loc["b1", "channel_wallet"] and f.loc["a1", "channel_upi"]
    assert f.loc["a1", "amount_log"] == pytest.approx(np.log1p(100))


# --- velocity ----------------------------------------------------------------------------------


def test_velocity_window_edges(txns: pd.DataFrame) -> None:
    f = by_id(velocity_features(txns), txns)
    cols = ["txn_count_5m", "txn_count_1h", "txn_count_24h", "amount_sum_1h", "failed_count_1h"]
    expected = {
        "a1": [0, 0, 0, 0, 0],
        "a2": [1, 1, 1, 100, 0],
        "a3": [0, 2, 2, 300, 0],  # a2 exactly 5 min back is out; a3 itself is not counted
        "a4": [0, 2, 3, 250, 1],  # a1 exactly 1 h back is out of 1h, in 24h
        "a5": [1, 3, 4, 550, 1],
        "a6": [1, 3, 4, 550, 1],  # same second as a5: a5 is not "earlier"
        "a7": [0, 0, 0, 0, 0],
    }
    for txn_id, values in expected.items():
        assert f.loc[txn_id, cols].tolist() == values, txn_id
    assert f.loc["a4", "amount_sum_24h"] == 350
    assert f.loc["a6", "amount_sum_24h"] == 650  # 100+200+50+300, a5 same second


def test_velocity_is_per_user(txns: pd.DataFrame) -> None:
    f = by_id(velocity_features(txns), txns)
    assert f.loc["c2", "txn_count_24h"] == 0  # U3's previous payment was 17 days earlier
    assert f.loc["c3", "txn_count_24h"] == 1  # U2: c1
    assert f.loc["f00", "txn_count_24h"] == 0  # U4's last was c5, 4 days before


def test_row_order_does_not_matter(txns: pd.DataFrame) -> None:
    shuffled = txns.sample(frac=1, random_state=0)
    a = by_id(velocity_features(txns), txns).sort_index()
    b = by_id(velocity_features(shuffled), shuffled).sort_index()
    pd.testing.assert_frame_equal(a, b)


# --- users, devices, reports for the fixture ---------------------------------------------------


def make_users() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "user_id": list(HOME),
            "home_lat": [HOME[u][0] for u in HOME],
            "home_lon": [HOME[u][1] for u in HOME],
            "sim_changed_at": [at(10), pd.NaT, pd.NaT, pd.NaT],  # U1: after a1, 6 d before a7
        }
    )


def make_devices() -> pd.DataFrame:
    old = at(-100)
    return pd.DataFrame(
        {
            "device_id": ["D1", "D1b", "D1new", "D2", "D3", "D4"],
            "user_id": ["U1", "U1", "U1", "U2", "U3", "U4"],
            "is_rooted": [False, True, False, False, False, False],
            "first_seen": [old, old, at(16, 8, 0), old, old, old],  # D1new: 2 h before a7
        }
    )


def make_reports() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "payee_vpa": [COLLECTOR, COLLECTOR, COLLECTOR, "shop.d"],
            # c3 sees 1, c5 sees 2, the third never counts; shop.d's is the same second as a7
            "ts": [at(20, 11, 30), at(21, 9, 0), at(22), at(16, 10, 0)],
        }
    )


# --- payee -------------------------------------------------------------------------------------


def test_payee_is_new(txns: pd.DataFrame) -> None:
    f = by_id(payee_features(txns, make_reports()), txns)["payee_is_new"]
    assert f["a1"] and not f["a2"]
    assert f["a5"]  # shop.b was only tried (FAILED) before
    assert f["b1"] and not f["b2"]
    assert f["f00"] and not f["f01"]


def test_payee_unique_senders_and_reports(txns: pd.DataFrame) -> None:
    f = by_id(payee_features(txns, make_reports()), txns)
    rows = ["c1", "c2", "c3", "c4", "c5", "c6"]
    # U2 twice counts once; c4 exactly 24 h before c6 is out.
    assert f.loc[rows, "payee_unique_senders_24h"].tolist() == [0, 1, 2, 2, 3, 1]
    assert f.loc[rows, "payee_report_count"].tolist() == [0, 0, 1, 1, 2, 2]
    assert f.loc["c5", "payee_reputation"] == pytest.approx(0.2)
    assert f.loc["a7", "payee_report_count"] == 0  # filed the same second, not before
    assert f.loc["c1", "payee_account_age_days"] == 3


# --- device ------------------------------------------------------------------------------------


def test_device_features(txns: pd.DataFrame) -> None:
    f = by_id(device_features(txns, make_users(), make_devices()), txns)
    assert f.loc["a7", "device_is_new"] and not f.loc["a8", "device_is_new"]
    assert f.loc["a8", "device_rooted"] and not f.loc["a7", "device_rooted"]
    assert f.loc["b1", "ip_distance_km"] == pytest.approx(119.5, abs=5)  # Mumbai -> Pune home
    assert f.loc["a1", "ip_distance_km"] == pytest.approx(0)
    assert f.loc["a7", "sim_changed_7d"]  # 6 d 10 h after the change
    assert not f.loc["a8", "sim_changed_7d"]  # 8 d 12 h after
    assert not f.loc["a1", "sim_changed_7d"]  # the change is in the future
    assert not f.loc["b1", "sim_changed_7d"]  # never changed


# --- context, coach, graph ---------------------------------------------------------------------


def test_context_features(txns: pd.DataFrame) -> None:
    f = by_id(context_features(txns), txns)
    flags = ["ctx_unknown_call", "ctx_screen_share", "ctx_call_long"]
    assert f.loc["b1", flags].all()  # unknown caller, AnyDesk, 400 s
    assert not f.loc["b2", flags].any()  # caller in contacts, exactly 300 s
    assert f.loc["n3", "ctx_screen_share"]  # screen share without a call
    assert (f["coach_red_flags"] == 0).all() and (f[GRAPH_FEATURES] == 0).all().all()


# --- full frame --------------------------------------------------------------------------------


def test_build_contract(txns: pd.DataFrame) -> None:
    users, devices, reports = make_users(), make_devices(), make_reports()
    originals = [frame.copy() for frame in (txns, users, devices, reports)]
    f = build_features(txns, users, devices, reports)
    assert list(f.columns) == FEATURE_NAMES and len(f) == 50
    assert f.index.name == "txn_id" and f.index.tolist() == txns["txn_id"].tolist()
    assert {c: str(t) for c, t in f.dtypes.items()} == DTYPES
    assert list(f["merchant_category"].cat.categories) == list(MERCHANT_CATEGORIES)
    assert not f.isna().any().any()
    assert not set(f.columns) & {"is_fraud", "scenario", "hard_negative", "ring_id", "ts"}
    # Pure: the inputs are untouched.
    for after, before in zip((txns, users, devices, reports), originals, strict=True):
        pd.testing.assert_frame_equal(after, before)


def test_training_frame_adds_labels(txns: pd.DataFrame) -> None:
    f = training_frame(txns, make_users(), make_devices(), make_reports())
    assert list(f.columns) == FEATURE_NAMES + LABEL_COLUMNS


def assert_no_leakage(
    txns: pd.DataFrame, users: pd.DataFrame, devices: pd.DataFrame, reports: pd.DataFrame
) -> None:
    """Features of rows before a cut-off must not change when everything after it is removed."""
    full = build_features(txns, users, devices, reports)
    for cut in txns["ts"].quantile([0.3, 0.6], interpolation="nearest"):
        early = (txns["ts"] < cut).to_numpy()
        part = build_features(txns[early], users, devices, reports[reports["ts"] < cut])
        pd.testing.assert_frame_equal(part, full[early])


def test_no_future_leakage_on_fixture(txns: pd.DataFrame) -> None:
    assert_no_leakage(txns, make_users(), make_devices(), make_reports())


# --- on simulator output -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def simulated() -> dict[str, pd.DataFrame]:
    return simulate(load_config(n_transactions=5000, n_users=500))


def test_simulated_features(simulated: dict[str, pd.DataFrame]) -> None:
    t = simulated["transactions"]
    f = build_features(t, simulated["users"], simulated["devices"], simulated["reports"])
    assert list(f.columns) == FEATURE_NAMES and len(f) == len(t)
    assert not f.isna().any().any()
    scenario = t["scenario"].to_numpy()
    assert f.loc[scenario == "digital_arrest", "ctx_unknown_call"].all()
    ato = f[scenario == "account_takeover"]
    assert ato["device_is_new"].all() and ato["sim_changed_7d"].all()
    assert f.loc[scenario == "mule_ring", "payee_unique_senders_24h"].max() > 20
    assert f.loc[scenario == "fake_support_refund", "payee_report_count"].min() >= 3
    big_card = (scenario == "card_testing") & (t["status"] == "SUCCESS").to_numpy()
    assert (f.loc[big_card, "failed_count_1h"] >= 5).all()


def test_no_future_leakage_on_simulated(simulated: dict[str, pd.DataFrame]) -> None:
    assert_no_leakage(
        simulated["transactions"], simulated["users"], simulated["devices"], simulated["reports"]
    )


def test_cli_writes_parquet(tmp_path: Path, simulated: dict[str, pd.DataFrame]) -> None:
    for name in ("transactions", "users", "devices", "reports"):
        simulated[name].to_parquet(tmp_path / f"{name}.parquet", index=False)
    out = tmp_path / "features.parquet"
    assert build_main(["--in", str(tmp_path), "--out", str(out)]) == 0
    back = pd.read_parquet(out)
    assert list(back.columns) == FEATURE_NAMES + LABEL_COLUMNS
    assert back["merchant_category"].dtype == "category"
