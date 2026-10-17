"""UPI simulator (spec 03 §4): config checks, population, and a full n=5000 run."""

import numpy as np
import pytest
from pydantic import ValidationError

from ml.simulator.config import load_config
from ml.simulator.population import World, build_population

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
