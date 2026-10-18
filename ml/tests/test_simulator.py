"""UPI simulator (spec 03 §4): config checks, population, and a full n=5000 run."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from ml.simulator.config import load_config
from ml.simulator.normal import generate_normal
from ml.simulator.population import World, build_population
from ml.simulator.rings import generate_rings
from ml.simulator.run import main, simulate
from ml.simulator.scenarios import fill_to_target, generate_scenarios
from ml.simulator.schema import (
    CHANNELS,
    HARD_NEGATIVES,
    OUTPUT_COLUMNS,
    SCENARIOS,
    SHARED_SCENARIOS,
)

N_USERS = 500


# --- config -----------------------------------------------------------------------------------


def test_default_config_loads() -> None:
    cfg = load_config()
    assert cfg.n_users == 10_000
    assert cfg.rings.count == 20
    assert cfg.shares["digital_arrest"] == pytest.approx(0.004)


def test_overrides_win_and_none_is_ignored() -> None:
    cfg = load_config(n_transactions=5000, n_users=N_USERS, seed=None)
    assert (cfg.n_transactions, cfg.n_users, cfg.seed) == (5000, N_USERS, 42)


def test_bad_config_is_rejected() -> None:
    with pytest.raises(ValidationError):
        load_config(shares={"digital_arrest": 0.004})  # other scenarios missing
    with pytest.raises(ValidationError):
        load_config(start="2026-07-01T00:00:00")  # no UTC offset


# --- population -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def world() -> World:
    cfg = load_config(n_users=N_USERS)
    return build_population(cfg, np.random.default_rng(0))


def test_population_users(world: World) -> None:
    users = world.users
    assert len(users) == N_USERS and users["user_id"].is_unique and users["vpa"].is_unique
    assert users["user_id"].iloc[0] == "U00001"
    assert (users["created_t"] < 0).all()  # all customers exist before the window starts
    assert (users["active_hour_start"] < users["active_hour_end"]).all()
    assert not users["is_mule"].any()


def test_population_devices_and_payees(world: World) -> None:
    per_user = world.devices.groupby("user_id").size()
    assert per_user.between(1, 3).all() and len(per_user) == N_USERS
    assert world.devices["device_id"].is_unique
    # Every customer is also a payee, linked back to their account.
    people = world.payees[world.payees["user_id"].notna()]
    assert set(people["user_id"]) == set(world.users["user_id"])
    # Usual payees: 3-15 each, never yourself.
    counts = np.diff(world.usual_ptr)
    assert counts.min() >= 3 and counts.max() <= 15
    owners = np.repeat(world.users["user_id"].to_numpy(), counts)
    assert (world.payees["user_id"].to_numpy()[world.usual_idx] != owners).all()


# --- normal behaviour + hard negatives ---------------------------------------------------------


@pytest.fixture(scope="module")
def normal(world: World) -> pd.DataFrame:
    return generate_normal(world, 4000, np.random.default_rng(1))


def test_normal_rows(world: World, normal: pd.DataFrame) -> None:
    assert len(normal) == 4000
    assert normal["scenario"].isna().all()
    assert set(normal["user_id"]) <= set(world.users["user_id"])
    assert set(normal["payee_vpa"]) <= set(world.payees["vpa"])
    assert (normal["amount"] > 0).all()
    assert normal["t"].between(0, world.end_t).all()
    assert set(normal["channel"]) <= set(CHANNELS)
    assert not ((normal["merchant_category"] == "P2P") & (normal["channel"] == "CARD")).any()
    assert normal["status"].eq("FAILED").mean() < 0.06


def test_normal_mostly_repeat_payees(world: World, normal: pd.DataFrame) -> None:
    usual = {
        (world.users["user_id"].iat[i], world.payees["vpa"].iat[j])
        for i in range(world.n_customers)
        for j in world.usual_idx[world.usual_ptr[i] : world.usual_ptr[i + 1]]
    }
    plain = normal[normal["hard_negative"].isna()]
    repeat = [pair in usual for pair in zip(plain["user_id"], plain["payee_vpa"], strict=True)]
    assert 0.6 < np.mean(repeat) < 0.8  # config: 70%


def test_hard_negatives(world: World, normal: pd.DataFrame) -> None:
    hn = normal["hard_negative"]
    assert set(hn.dropna()) == set(HARD_NEGATIVES)
    assert (normal.loc[hn.notna(), "status"] == "SUCCESS").all()
    big = normal[hn == "big_legit"].merge(world.users[["user_id", "avg_txn_amount"]])
    assert (big["amount"] >= 10 * big["avg_txn_amount"] - 0.01).all()
    assert set(big["merchant_category"]) <= {"RENT", "EDUCATION"}
    night = normal[hn == "night_legit"]
    assert ((night["t"] % 86_400) < 5 * 3600).all()
    calls = normal[hn == "call_legit"]
    assert (calls["on_call"] & calls["caller_in_contacts"]).all()


# --- injected scenarios ------------------------------------------------------------------------

SCENARIO_N = 20_000  # big enough that every scenario has several incidents


@pytest.fixture(scope="module")
def scen() -> tuple[World, pd.DataFrame]:
    w = build_population(load_config(n_users=N_USERS), np.random.default_rng(2))
    return w, generate_scenarios(w, SCENARIO_N, np.random.default_rng(3))


def test_scenario_counts_are_exact(scen: tuple[World, pd.DataFrame]) -> None:
    world, frame = scen
    counts = frame["scenario"].value_counts()
    for name in SHARED_SCENARIOS:
        assert counts[name] == round(world.cfg.shares[name] * SCENARIO_N)
    assert frame["t"].between(0, world.end_t).all() and (frame["amount"] > 0).all()


def test_fill_to_target_folds_small_leftovers(world: World) -> None:
    def make(w: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
        return pd.DataFrame({"size": [size]})

    for target in (0, 1, 7, 23):
        parts = fill_to_target(world, np.random.default_rng(target), target, make, (6, 16))
        sizes = [int(p["size"].iat[0]) for p in parts]
        assert sum(sizes) == target
        assert all(s >= 6 for s in sizes) or len(sizes) == 1


def _rows(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    return frame[frame["scenario"] == name]


def test_digital_arrest_signature(scen: tuple[World, pd.DataFrame]) -> None:
    world, frame = scen
    da = _rows(frame, "digital_arrest").merge(world.users[["user_id", "avg_txn_amount"]])
    assert (da["on_call"] & ~da["caller_in_contacts"] & da["screen_share_active"]).all()
    assert (da["call_duration_s"] >= 1200).all()
    assert (da["amount"] >= 10 * da["avg_txn_amount"] - 0.01).all()
    assert (da["amount"] <= 50 * da["avg_txn_amount"] + 0.01).all()
    # New payee: a scam VPA nobody else ever pays.
    assert not set(da["payee_vpa"]) & set(
        frame.loc[frame["scenario"] != "digital_arrest", "payee_vpa"]
    )


def test_fake_support_refund_signature(scen: tuple[World, pd.DataFrame]) -> None:
    world, frame = scen
    fs = _rows(frame, "fake_support_refund")
    assert (fs["screen_share_active"] | fs["remote_access_app"].notna()).all()
    reports = pd.concat(world.reports)
    first = fs.groupby("payee_vpa")["t"].min()
    for vpa, t in first.items():  # reported before the scam
        assert (reports.loc[reports["payee_vpa"] == vpa, "t"] < t).sum() >= 3


def test_investment_task_signature(scen: tuple[World, pd.DataFrame]) -> None:
    _, frame = scen
    for _, g in _rows(frame, "investment_task").groupby("payee_vpa"):
        g = g.sort_values("t")
        assert g["user_id"].nunique() == 1
        assert g["amount"].is_monotonic_increasing
        assert g["t"].max() - g["t"].min() <= 10 * 86_400


def test_account_takeover_signature(scen: tuple[World, pd.DataFrame]) -> None:
    world, frame = scen
    ato = _rows(frame, "account_takeover")
    ato = ato.merge(world.devices[["device_id", "first_seen_t"]]).merge(
        world.users[["user_id", "sim_changed_t"]]
    )
    assert ((ato["t"] - ato["first_seen_t"]).between(0, 86_400)).all()  # new device
    assert ((ato["t"] - ato["sim_changed_t"]).between(0, 7 * 86_400)).all()  # SIM changed < 7 d
    assert ((ato["t"] % 86_400) < 5 * 3600).all()  # night
    assert (ato["typing_speed_z"].abs() > 3).all()


def test_card_testing_signature(scen: tuple[World, pd.DataFrame]) -> None:
    _, frame = scen
    ct = _rows(frame, "card_testing")
    assert (ct["channel"] == "CARD").all()
    failed, ok = ct[ct["status"] == "FAILED"], ct[ct["status"] == "SUCCESS"]
    assert (failed["amount"] <= 20).all()
    for _, big in ok.iterrows():  # each big payment follows failed small tests by the same card
        before = failed[(failed["user_id"] == big["user_id"]) & (failed["t"] < big["t"])]
        assert len(before) >= 5 and big["t"] - before["t"].min() < 3600


# --- mule rings --------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rings() -> tuple[World, pd.DataFrame]:
    w = build_population(load_config(n_users=N_USERS), np.random.default_rng(4))
    return w, generate_rings(w, np.random.default_rng(5))


def test_ring_count_and_size(rings: tuple[World, pd.DataFrame]) -> None:
    world, frame = rings
    mules = world.users[world.users["is_mule"]]
    sizes = mules.groupby("ring_id").size()
    assert len(sizes) == 20 and frame["ring_id"].nunique() == 20
    assert sizes.between(10, 30).all()
    assert (frame["scenario"] == "mule_ring").all()


def test_ring_payees_are_customer_accounts(rings: tuple[World, pd.DataFrame]) -> None:
    world, frame = rings
    mule_vpas = set(world.users.loc[world.users["is_mule"], "vpa"])
    paid = set(frame["payee_vpa"])
    assert mule_vpas <= paid  # every mule receives money
    # Every payee that is a mule is a row in users and in payees with its user_id.
    linked = world.payees[world.payees["vpa"].isin(mule_vpas)]
    assert linked["user_id"].notna().all() and len(linked) == len(mule_vpas)


def test_ring_fan_in_and_pass_through(rings: tuple[World, pd.DataFrame]) -> None:
    world, frame = rings
    vpa_to_user = world.users.set_index("vpa")["user_id"]
    frame = frame.assign(payee_user=frame["payee_vpa"].map(vpa_to_user))
    mule_ids = set(world.users.loc[world.users["is_mule"], "user_id"])
    incoming = frame[frame["payee_user"].isin(mule_ids)]
    for mule, got in incoming.groupby("payee_user"):
        sent = frame[frame["user_id"] == mule]
        assert sent["amount"].sum() >= 0.8 * got["amount"].sum()  # pass-through: > 80% forwarded
        for t in got["t"]:  # money moves on within 1 h of arriving
            assert ((sent["t"] > t) & (sent["t"] <= t + 3600)).any()
    # Collectors (paid by ordinary customers): > 20 distinct senders within 24 h.
    from_customers = incoming[~incoming["user_id"].isin(mule_ids)]
    for _, got in from_customers.groupby("payee_user"):
        assert got["user_id"].nunique() > 20
        assert got["t"].max() - got["t"].min() < 86_400


def test_ring_shared_devices_and_new_accounts(rings: tuple[World, pd.DataFrame]) -> None:
    world, frame = rings
    mules = world.users[world.users["is_mule"]]
    devices = world.devices.merge(mules[["user_id", "ring_id", "created_t"]])
    per_phone = devices.groupby(["ring_id", "fingerprint"])["user_id"].nunique()
    assert (per_phone.groupby("ring_id").max() >= 3).all()
    first_in = frame.groupby("ring_id")["t"].min()
    age_days = (mules["ring_id"].map(first_in) - mules["created_t"]) / 86_400
    assert (age_days < 7).groupby(mules["ring_id"]).any().all()  # new accounts in every ring


# --- full run: python -m ml.simulator.run --n 5000 ---------------------------------------------

FULL_N = 5000


@pytest.fixture(scope="module")
def out(tmp_path_factory: pytest.TempPathFactory) -> dict[str, pd.DataFrame]:
    out_dir = tmp_path_factory.mktemp("simulated")
    assert main(["--n", str(FULL_N), "--users", str(N_USERS), "--out", str(out_dir)]) == 0
    return {name: pd.read_parquet(out_dir / f"{name}.parquet") for name in OUTPUT_COLUMNS}


def test_five_files_with_spec_columns(out: dict[str, pd.DataFrame]) -> None:
    for name, columns in OUTPUT_COLUMNS.items():
        assert list(out[name].columns) == columns, name
    assert len(out["transactions"]) == FULL_N


def test_label_shares_match_spec(out: dict[str, pd.DataFrame]) -> None:
    txns, shares = out["transactions"], load_config().shares
    counts = txns["scenario"].value_counts()
    for name in SHARED_SCENARIOS:
        assert counts[name] / FULL_N == pytest.approx(shares[name], rel=0.2), name
    assert set(txns["scenario"].dropna()) == set(SCENARIOS)
    assert (txns["is_fraud"] == txns["scenario"].notna()).all()
    assert not txns.loc[txns["hard_negative"].notna(), "is_fraud"].any()


def test_twenty_rings_of_customer_accounts(out: dict[str, pd.DataFrame]) -> None:
    txns, users = out["transactions"], out["users"]
    assert txns["ring_id"].nunique() == 20
    assert users.loc[users["is_mule"]].groupby("ring_id").size().between(10, 30).all()
    mule_vpas = set(users.loc[users["is_mule"], "vpa"])
    to_mules = txns[txns["payee_vpa"].isin(mule_vpas)]
    assert to_mules["payee_user_id"].notna().all()
    assert set(to_mules["payee_user_id"]) <= set(users["user_id"])


def test_transactions_are_consistent(out: dict[str, pd.DataFrame]) -> None:
    txns = out["transactions"]
    assert txns["txn_id"].is_unique and txns["txn_id"].str.match(r"^TXN-\d{8}-\d{6}$").all()
    assert txns["ts"].is_monotonic_increasing
    assert (txns["amount"] > 0).all() and (txns["currency"] == "INR").all()
    assert set(txns["user_id"]) <= set(out["users"]["user_id"])
    assert set(txns["device_id"]) <= set(out["devices"]["device_id"])
    assert (txns["payee_account_age_days"] >= 0).all()
    start = pd.Timestamp(load_config().start)
    assert txns["ts"].min() >= start and txns["ts"].max() < start + pd.Timedelta(days=90)


def test_edges_and_reports(out: dict[str, pd.DataFrame]) -> None:
    edges, txns, reports = out["edges"], out["transactions"], out["reports"]
    assert set(edges["edge_type"]) == {"SENT", "USED_DEVICE", "LOGGED_FROM"}
    assert (edges["edge_type"] == "SENT").sum() == len(txns)
    assert reports["report_id"].is_unique and len(reports) > 0
    assert reports["ts"].is_monotonic_increasing


def test_same_seed_same_data_other_seed_differs() -> None:
    cfg = load_config(n_transactions=FULL_N, n_users=N_USERS)
    a, b = simulate(cfg), simulate(cfg)
    for name in OUTPUT_COLUMNS:
        pd.testing.assert_frame_equal(a[name], b[name])
    c = simulate(load_config(n_transactions=FULL_N, n_users=N_USERS, seed=7))
    assert not a["transactions"]["amount"].equals(c["transactions"]["amount"])


def test_too_small_n_is_rejected() -> None:
    with pytest.raises(ValueError, match="too small"):
        simulate(load_config(n_transactions=1000, n_users=N_USERS))


def test_no_payment_sits_exactly_at_home(out: dict[str, pd.DataFrame]) -> None:
    """Every payment gets GPS jitter. Exact home coordinates on fraud rows only would let a model
    spot fraud by `ip_distance_km == 0` (a leak found while training the T2.1 baseline)."""
    homes = out["users"][["user_id", "home_lat", "home_lon"]]
    txns = out["transactions"].merge(homes, on="user_id")
    exact = (txns["geo_lat"] == txns["home_lat"]) & (txns["geo_lon"] == txns["home_lon"])
    assert exact.groupby(txns["is_fraud"]).mean().max() < 0.01
