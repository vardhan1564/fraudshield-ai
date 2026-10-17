"""UPI simulator (spec 03 §4): config checks, population, and a full n=5000 run."""

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from ml.simulator.config import load_config
from ml.simulator.normal import generate_normal
from ml.simulator.population import World, build_population
from ml.simulator.schema import CHANNELS, HARD_NEGATIVES

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
