"""Injected fraud scenarios (spec 03 §4). Each one fills an exact share of transaction rows.

An incident is one victim's episode (e.g. a digital-arrest call with 1-3 payments). Incidents are
added until the scenario's row target is met; the last one is sized to fit.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd

from ml.simulator.population import (
    DAY,
    GPS_NOISE_DEG,
    HOUR,
    World,
    fingerprints,
    random_ips,
    txn_frame,
)
from ml.simulator.schema import REMOTE_ACCESS_APPS, SHARED_SCENARIOS

MINUTE = 60
Incident = Callable[[World, np.random.Generator, int], pd.DataFrame]


# --- shared helpers ----------------------------------------------------------------------------


def pick_victim(world: World, rng: np.random.Generator) -> pd.Series:
    """A customer not used by another scenario incident yet."""
    while True:
        i = int(rng.integers(0, world.n_customers))
        if i not in world.used_victims:
            world.used_victims.add(i)
            row = world.users.iloc[i].copy()
            row["row"] = i
            row["main_device"] = world.devices["device_id"].iat[world.device_ptr[i]]
            return row


def new_scam_payee(
    world: World,
    rng: np.random.Generator,
    prefixes: tuple[str, ...],
    t0: int,
    age_days: tuple[int, int],
) -> str:
    """A fraudster-controlled VPA (no customer account). Usually freshly opened, but some
    scams use old "rented" accounts, so account age alone can't give them away."""
    vpa = f"{rng.choice(prefixes)}{len(world.payees) + 1}@{rng.choice(['ybl', 'okaxis', 'paytm'])}"
    if rng.random() < world.cfg.realism.aged_scam_payee_share:
        age_days = (180, 2000)
    created = t0 - int(rng.integers(age_days[0], age_days[1] + 1)) * DAY
    world.add_payees(
        pd.DataFrame({"vpa": [vpa], "user_id": [None], "category": ["P2P"], "created_t": [created]})
    )
    return vpa


def new_device(
    world: World, rng: np.random.Generator, user_id: str, first_seen: int, rooted_p: float
) -> str:
    """A device the fraudster uses, recorded against the victim's account."""
    device_id = world.new_device_ids(1)[0]
    world.add_devices(
        pd.DataFrame(
            {
                "device_id": [device_id],
                "user_id": [user_id],
                "fingerprint": fingerprints(rng, 1),
                "os": ["Android"],
                "is_rooted": [bool(rng.random() < rooted_p)],
                "first_seen_t": [first_seen],
            }
        )
    )
    return device_id


def report(
    world: World, rng: np.random.Generator, reporter: str, vpa: str, kind: str, after: int, p: float
) -> None:
    """With probability p the victim reports the payee hours to days later."""
    if rng.random() < p:
        t = after + int(rng.integers(2 * HOUR, 5 * DAY))
        world.add_reports(
            pd.DataFrame(
                {"reporter_id": [reporter], "payee_vpa": [vpa], "scam_type": [kind], "t": [t]}
            )
        )


def far_city(world: World, rng: np.random.Generator, k: int) -> tuple[np.ndarray, np.ndarray]:
    cities = world.cfg.cities
    pick = rng.choice(len(cities), size=k)
    lat = np.array([cities[i].lat for i in pick]) + rng.normal(0, GPS_NOISE_DEG, size=k)
    lon = np.array([cities[i].lon for i in pick]) + rng.normal(0, GPS_NOISE_DEG, size=k)
    return lat, lon


def home(rng: np.random.Generator, v: pd.Series, k: int) -> dict[str, object]:
    """Victim's own phone, network and location (with the same GPS jitter as normal payments)."""
    return {
        "user_id": v["user_id"],
        "device_id": v["main_device"],
        "ip": v["home_ip"],
        "geo_lat": v["home_lat"] + rng.normal(0, GPS_NOISE_DEG, size=k),
        "geo_lon": v["home_lon"] + rng.normal(0, GPS_NOISE_DEG, size=k),
    }


# --- scenarios ---------------------------------------------------------------------------------


def digital_arrest(world: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
    """Fake police on a 20+ min unknown call with screen share; 10-50x average to a new payee."""
    v = pick_victim(world, rng)
    call_start = int(rng.integers(DAY, world.end_t - 2 * DAY))
    t = (
        call_start
        + int(rng.integers(20 * MINUTE, 60 * MINUTE))
        + np.cumsum(np.r_[0, rng.integers(2 * MINUTE, 15 * MINUTE, size=size - 1)])
    )
    payee = new_scam_payee(
        world, rng, ("cbi.case", "rbi.verify", "customs.clear", "cyber.cell"), call_start, (2, 60)
    )
    frame = txn_frame(
        rng,
        **home(rng, v, size),
        payee_vpa=payee,
        amount=np.round(v["avg_txn_amount"] * rng.uniform(10, 50, size=size), 2),
        t=t,
        on_call=True,
        caller_in_contacts=False,
        call_duration_s=t - call_start,
        screen_share_active=bool(rng.random() < world.cfg.realism.digital_arrest_screen_share),
        remote_access_app="AnyDesk" if rng.random() < 0.25 else None,
        session_len_s=rng.uniform(300, 1500, size=size),
        login_to_pay_s=rng.uniform(60, 400, size=size),
    )
    report(world, rng, v["user_id"], payee, "digital_arrest", int(t[-1]), p=0.7)
    return frame


def fake_support_refund(world: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
    """'Customer care' asks for screen share / a remote app to 'process a refund'. The payee
    has already been reported by other people."""
    v = pick_victim(world, rng)
    t0 = int(rng.integers(21 * DAY, world.end_t - DAY))
    t = t0 + np.cumsum(np.r_[0, rng.integers(MINUTE, 10 * MINUTE, size=size - 1)])
    payee = new_scam_payee(
        world, rng, ("refund.help", "customer.care", "support.desk"), t0, (10, 200)
    )
    k = int(rng.integers(3, 13))  # earlier victims' reports, 1 h to 20 days before
    others = rng.integers(0, world.n_customers, size=k)
    world.add_reports(
        pd.DataFrame(
            {
                "reporter_id": world.users["user_id"].to_numpy()[others],
                "payee_vpa": payee,
                "scam_type": "fake_support_refund",
                "t": t0 - rng.integers(HOUR, 20 * DAY, size=k),
            }
        )
    )
    share = rng.random(size) < 0.7
    app = rng.choice(REMOTE_ACCESS_APPS, size=size).astype(object)
    app[share & (rng.random(size) < 0.5)] = None  # screen share alone is enough sometimes
    on_call = rng.random(size) < 0.8
    frame = txn_frame(
        rng,
        **home(rng, v, size),
        payee_vpa=payee,
        amount=np.round(v["avg_txn_amount"] * rng.uniform(1, 8, size=size), 2),
        t=t,
        on_call=on_call,
        call_duration_s=np.where(on_call, rng.integers(5 * MINUTE, 30 * MINUTE, size=size), 0),
        screen_share_active=share,
        remote_access_app=app,
    )
    report(world, rng, v["user_id"], payee, "fake_support_refund", int(t[-1]), p=0.6)
    return frame


def investment_task(world: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
    """'Earn from tasks' scam: rising payments to one new payee over 2-10 days."""
    v = pick_victim(world, rng)
    t0 = int(rng.integers(0, world.end_t - 11 * DAY))
    span = int(rng.integers(2 * DAY, 10 * DAY))
    t = t0 + np.sort(np.r_[0, rng.integers(HOUR, span, size=size - 1)])
    growth = rng.uniform(1.4, 2.5) ** np.arange(size)  # each payment bigger than the last
    payee = new_scam_payee(world, rng, ("invest.task", "crypto.profit", "earn.daily"), t0, (5, 90))
    frame = txn_frame(
        rng,
        **home(rng, v, size),
        payee_vpa=payee,
        amount=np.round(v["avg_txn_amount"] * rng.uniform(0.5, 2) * growth, 2),
        t=t,
    )
    report(world, rng, v["user_id"], payee, "investment_task", int(t[-1]), p=0.5)
    return frame


def account_takeover(world: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
    """Fraudster with the victim's SIM on a new phone, at night, typing unlike the owner."""
    v = pick_victim(world, rng)
    day = int(rng.integers(8, world.cfg.days - 1))
    t0 = day * DAY + int(rng.integers(0, 4 * HOUR))  # 00:00-04:00, all payments before 05:00
    t = t0 + np.cumsum(np.r_[0, rng.integers(MINUTE, 10 * MINUTE, size=size - 1)])
    world.users.loc[v["row"], "sim_changed_t"] = t0 - int(rng.integers(HOUR, 6 * DAY))
    device = new_device(
        world, rng, v["user_id"], t0 - int(rng.integers(10 * MINUTE, 2 * HOUR)), rooted_p=0.3
    )
    payee = new_scam_payee(world, rng, ("quick.cash", "pay.fast", "wallet.load"), t0, (1, 30))
    lat, lon = far_city(world, rng, size)
    frame = txn_frame(
        rng,
        user_id=v["user_id"],
        payee_vpa=payee,
        amount=np.round(v["avg_txn_amount"] * rng.uniform(3, 15, size=size), 2),
        device_id=device,
        ip=random_ips(rng, size),
        is_vpn=rng.random(size) < 0.4,
        geo_lat=lat,
        geo_lon=lon,
        t=t,
        typing_speed_z=rng.choice([-1, 1], size=size) * rng.uniform(3.2, 5, size=size),
        session_len_s=rng.uniform(20, 90, size=size),
        login_to_pay_s=rng.uniform(3, 15, size=size),
    )
    report(world, rng, v["user_id"], payee, "account_takeover", int(t[-1]), p=0.8)
    return frame


def card_testing(world: World, rng: np.random.Generator, size: int) -> pd.DataFrame:
    """Stolen card: many small failed attempts within minutes, then one large payment."""
    v = pick_victim(world, rng)
    t0 = int(rng.integers(0, world.end_t - DAY))
    t = t0 + np.cumsum(np.r_[0, rng.integers(5, 60, size=size - 1)])
    t[-1] += int(rng.integers(30, 10 * MINUTE))  # the big one comes after the tests
    amount = np.round(rng.uniform(1, 20, size=size), 2)
    amount[-1] = np.round(v["avg_txn_amount"] * rng.uniform(5, 20), 2)
    status = np.full(size, "FAILED", dtype=object)
    status[-1] = "SUCCESS"
    merchants = world.payees.iloc[: world.n_merchants]
    shop = rng.choice(merchants.loc[merchants["category"] == "ECOMMERCE", "vpa"].to_numpy())
    lat, lon = far_city(world, rng, size)
    return txn_frame(
        rng,
        user_id=v["user_id"],
        payee_vpa=shop,
        amount=amount,
        channel="CARD",
        merchant_category="ECOMMERCE",
        device_id=new_device(
            world, rng, v["user_id"], t0 - int(rng.integers(0, 5 * MINUTE)), rooted_p=0.5
        ),
        ip=random_ips(rng, size),
        is_vpn=rng.random(size) < 0.5,
        geo_lat=lat,
        geo_lon=lon,
        t=t,
        status=status,
    )


INCIDENTS: dict[str, tuple[Incident, tuple[int, int]]] = {
    # name: (generator, rows per incident min-max)
    "digital_arrest": (digital_arrest, (1, 3)),
    "fake_support_refund": (fake_support_refund, (1, 2)),
    "investment_task": (investment_task, (2, 6)),
    "account_takeover": (account_takeover, (1, 4)),
    "card_testing": (card_testing, (6, 16)),
}
assert tuple(INCIDENTS) == SHARED_SCENARIOS


def fill_to_target(
    world: World, rng: np.random.Generator, target: int, make: Incident, sizes: tuple[int, int]
) -> list[pd.DataFrame]:
    """Add incidents until exactly `target` rows exist. The last incident is sized to fit, and a
    leftover too small for a valid incident is folded into the one before it."""
    lo, hi = sizes
    parts, made = [], 0
    while made < target:
        remaining = target - made
        size = min(int(rng.integers(lo, hi + 1)), remaining)
        if 0 < remaining - size < lo:
            size = remaining
        parts.append(make(world, rng, size))
        made += size
    return parts


def generate_scenarios(world: World, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """All injected scenarios for a dataset of n transaction rows."""
    frames = []
    for name, (make, sizes) in INCIDENTS.items():
        target = round(world.cfg.shares[name] * n)
        frames += [
            part.assign(scenario=name) for part in fill_to_target(world, rng, target, make, sizes)
        ]
    return pd.concat(frames, ignore_index=True)
