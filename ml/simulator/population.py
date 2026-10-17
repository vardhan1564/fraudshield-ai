"""Customers, their devices and payees, plus the shared World state the generators add to.

Times inside the simulator are integer seconds from config.start (`*_t` columns); outputs.py
turns them into timestamps at the end.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ml.simulator.config import SimConfig

DAY = 86_400
HOUR = 3_600

FIRST_NAMES = [
    "Aarav",
    "Aditi",
    "Akash",
    "Ananya",
    "Arjun",
    "Bhavna",
    "Chetan",
    "Deepa",
    "Dev",
    "Divya",
    "Farhan",
    "Gauri",
    "Harish",
    "Isha",
    "Jaya",
    "Karan",
    "Kavya",
    "Lakshmi",
    "Manoj",
    "Meera",
    "Mohit",
    "Neha",
    "Nikhil",
    "Pooja",
    "Priya",
    "Rahul",
    "Riya",
    "Rohan",
    "Sakshi",
    "Sanjay",
    "Sneha",
    "Suresh",
    "Tanvi",
    "Uday",
    "Varun",
    "Vidya",
    "Vikram",
    "Yash",
    "Zoya",
    "Imran",
]
SURNAMES = [
    "Sharma",
    "Patel",
    "Reddy",
    "Iyer",
    "Nair",
    "Gupta",
    "Singh",
    "Kumar",
    "Joshi",
    "Desai",
    "Kulkarni",
    "Das",
    "Banerjee",
    "Khan",
    "Mehta",
    "Pillai",
    "Rao",
    "Verma",
    "Chauhan",
    "Patil",
]
VPA_HANDLES = ("okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "paytm")
AGE_BANDS = ("18-25", "26-35", "36-45", "46-60", "60+")
AGE_WEIGHTS = (0.22, 0.32, 0.22, 0.16, 0.08)
TIER_AMOUNT = {1: 1.3, 2: 1.0, 3: 0.7}  # bigger cities spend more per payment


@dataclass
class World:
    """Everything the generators share. Generators append; outputs.py reads."""

    cfg: SimConfig
    users: pd.DataFrame
    devices: pd.DataFrame
    payees: pd.DataFrame  # vpa, user_id (set when the payee is a customer), category, created_t
    usual_ptr: np.ndarray  # user i's usual payees: payees row ids usual_idx[ptr[i]:ptr[i+1]]
    usual_idx: np.ndarray
    device_ptr: np.ndarray  # user i's own devices: devices row ids device_idx[ptr[i]:ptr[i+1]]
    device_idx: np.ndarray
    reports: list[pd.DataFrame] = field(default_factory=list)
    n_customers: int = 0  # users[:n_customers] are ordinary customers; mules come after

    @property
    def end_t(self) -> int:
        return self.cfg.days * DAY

    def add_users(self, new: pd.DataFrame) -> None:
        self.users = pd.concat([self.users, new], ignore_index=True)

    def add_devices(self, new: pd.DataFrame) -> None:
        self.devices = pd.concat([self.devices, new], ignore_index=True)

    def add_payees(self, new: pd.DataFrame) -> None:
        self.payees = pd.concat([self.payees, new], ignore_index=True)

    def add_reports(self, new: pd.DataFrame) -> None:
        self.reports.append(new)

    def new_device_ids(self, k: int) -> list[str]:
        start = len(self.devices)
        return [f"D{i + 1:06d}" for i in range(start, start + k)]


def fingerprints(rng: np.random.Generator, k: int) -> list[str]:
    return [f"{x:016x}" for x in rng.integers(0, 2**62, size=k)]


def random_ips(rng: np.random.Generator, k: int) -> list[str]:
    octets = rng.integers(1, 255, size=(k, 3))
    return [f"103.{a}.{b}.{c}" for a, b, c in octets]


def make_people(
    rng: np.random.Generator,
    cfg: SimConfig,
    n: int,
    first_number: int,
    created_t: np.ndarray,
) -> pd.DataFrame:
    """n customer accounts numbered from first_number (U00001 …). Used for customers and mules."""
    cities = cfg.cities
    weights = np.array([c.weight for c in cities])
    city_i = rng.choice(len(cities), size=n, p=weights / weights.sum())
    tier = np.array([cities[i].tier for i in city_i])
    first = rng.choice(FIRST_NAMES, size=n)
    numbers = np.arange(first_number, first_number + n)
    hour_start = rng.integers(6, 12, size=n)
    avg = np.exp(rng.normal(np.log(800), 0.7, size=n)) * np.vectorize(TIER_AMOUNT.get)(tier)
    return pd.DataFrame(
        {
            "user_id": [f"U{k:05d}" for k in numbers],
            "name": [f"{f} {s}" for f, s in zip(first, rng.choice(SURNAMES, size=n), strict=True)],
            "phone": [f"9{x:09d}" for x in rng.integers(0, 10**9, size=n)],
            "age_band": rng.choice(AGE_BANDS, size=n, p=AGE_WEIGHTS),
            "city": [cities[i].name for i in city_i],
            "city_tier": tier,
            "home_lat": [cities[i].lat for i in city_i] + rng.normal(0, 0.03, size=n),
            "home_lon": [cities[i].lon for i in city_i] + rng.normal(0, 0.03, size=n),
            "avg_txn_amount": np.round(np.clip(avg, 100, 20_000), 2),
            "daily_rate": np.clip(rng.gamma(2.0, 0.5, size=n), 0.05, 6.0),
            "active_hour_start": hour_start,
            "active_hour_end": np.minimum(hour_start + rng.integers(10, 16, size=n), 23),
            "kyc_level": rng.choice([1, 2, 3], size=n, p=[0.15, 0.35, 0.5]),
            "vpa": [
                f"{f.lower()}{k}@{h}"
                for f, k, h in zip(first, numbers, rng.choice(VPA_HANDLES, size=n), strict=True)
            ],
            "created_t": created_t.astype("int64"),
            "sim_changed_t": np.full(n, np.nan),
            "is_mule": False,
            "ring_id": None,
            "home_ip": random_ips(rng, n),
        }
    )


def _ragged(
    rng: np.random.Generator, counts: np.ndarray, pool: int
) -> tuple[np.ndarray, np.ndarray]:
    """CSR-style lists: for each owner, counts[i] random ids in [0, pool)."""
    ptr = np.concatenate([[0], np.cumsum(counts)])
    return ptr, rng.integers(0, pool, size=int(ptr[-1]))


def build_population(cfg: SimConfig, rng: np.random.Generator) -> World:
    n = cfg.n_users
    users = make_people(rng, cfg, n, 1, -rng.integers(30 * DAY, 3650 * DAY, size=n))
    # A few legit SIM changes, so "SIM changed" alone is not fraud.
    legit_sim = rng.random(n) < 0.05
    users.loc[legit_sim, "sim_changed_t"] = rng.integers(0, cfg.days * DAY, size=legit_sim.sum())

    # Devices: 1-3 per customer, the first one is the main phone.
    per_user = rng.choice([1, 2, 3], size=n, p=[0.6, 0.3, 0.1])
    owner = np.repeat(np.arange(n), per_user)
    k = len(owner)
    devices = pd.DataFrame(
        {
            "device_id": [f"D{i + 1:06d}" for i in range(k)],
            "user_id": users["user_id"].to_numpy()[owner],
            "fingerprint": fingerprints(rng, k),
            "os": rng.choice(["Android", "iOS"], size=k, p=[0.85, 0.15]),
            "is_rooted": rng.random(k) < 0.02,
            "first_seen_t": users["created_t"].to_numpy()[owner]
            + rng.integers(0, 20 * DAY, size=k),
        }
    )
    device_ptr = np.concatenate([[0], np.cumsum(per_user)])

    # Payees: merchants plus every customer (P2P).
    cats = list(cfg.merchant_categories)
    cat_w = np.array([c.weight for c in cfg.merchant_categories.values()])
    n_merchants = max(200, n // 5)
    merchant_cat = rng.choice(cats, size=n_merchants, p=cat_w / cat_w.sum())
    merchants = pd.DataFrame(
        {
            "vpa": [
                f"{c.lower()}.m{i + 1:05d}@{h}"
                for i, (c, h) in enumerate(
                    zip(merchant_cat, rng.choice(VPA_HANDLES, size=n_merchants), strict=True)
                )
            ],
            "user_id": None,
            "category": merchant_cat,
            "created_t": -rng.integers(180 * DAY, 3650 * DAY, size=n_merchants),
        }
    )
    people = pd.DataFrame(
        {
            "vpa": users["vpa"],
            "user_id": users["user_id"],
            "category": "P2P",
            "created_t": users["created_t"],
        }
    )
    payees = pd.concat([merchants, people], ignore_index=True)

    # Usual payees: about 60% merchants, 40% other customers.
    lo, hi = cfg.normal.payees_per_user
    counts = rng.integers(lo, hi + 1, size=n)
    usual_ptr, raw = _ragged(rng, counts, n_merchants)
    is_person = rng.random(len(raw)) < 0.4
    person = rng.integers(0, n, size=is_person.sum())
    owner_of = np.repeat(np.arange(n), counts)[is_person]
    person = np.where(person == owner_of, (person + 1) % n, person)  # nobody pays themselves
    raw[is_person] = n_merchants + person

    return World(
        cfg=cfg,
        users=users,
        devices=devices,
        payees=payees,
        usual_ptr=usual_ptr,
        usual_idx=raw,
        device_ptr=device_ptr,
        device_idx=np.arange(k),
        n_customers=n,
    )
