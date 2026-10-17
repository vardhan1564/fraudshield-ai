"""Mule rings: victims pay collector accounts, which pass the money down two layers and cash out.

Mules are real customer accounts (users + payees rows), so forwarded money can be traced. Each ring
is built to trip the spec 01 F3 rule detectors: fan-in (> 20 senders in 24 h), pass-through (> 80%
forwarded within 1 h), shared device (>= 3 accounts per phone) and new-account burst.
"""

import numpy as np
import pandas as pd

from ml.simulator.population import (
    DAY,
    HOUR,
    World,
    fingerprints,
    make_people,
    random_ips,
    txn_frame,
)

MINUTE = 60


def generate_rings(world: World, rng: np.random.Generator) -> pd.DataFrame:
    frames = [_ring(world, rng, f"R{r + 1:02d}") for r in range(world.cfg.rings.count)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _add_mules(world: World, rng: np.random.Generator, ring_id: str, start: int) -> pd.DataFrame:
    """Create the ring's accounts, their payee entries and shared phones."""
    rc = world.cfg.rings
    size = int(rng.integers(rc.min_size, rc.max_size + 1))
    new = rng.random(size) < rc.new_account_share
    created = np.where(
        new,
        start - rng.integers(DAY, 7 * DAY - HOUR, size=size),  # opened days before the ring runs
        start - rng.integers(30 * DAY, 400 * DAY, size=size),
    )
    mules = make_people(rng, world.cfg, size, len(world.users) + 1, created)
    mules["is_mule"] = True
    mules["ring_id"] = ring_id
    mules["home_ip"] = rng.choice(random_ips(rng, 2), size=size)  # the ring works from 2 networks
    world.add_users(mules)
    world.add_payees(
        pd.DataFrame(
            {
                "vpa": mules["vpa"],
                "user_id": mules["user_id"],
                "category": "P2P",
                "created_t": created,
            }
        )
    )
    # Phones shared by `accounts_per_device` accounts each (same fingerprint, one row per account).
    phones = fingerprints(rng, -(-size // rc.accounts_per_device))
    mules["device_id"] = world.new_device_ids(size)
    world.add_devices(
        pd.DataFrame(
            {
                "device_id": mules["device_id"],
                "user_id": mules["user_id"],
                "fingerprint": [phones[i // rc.accounts_per_device] for i in range(size)],
                "os": "Android",
                "is_rooted": False,
                "first_seen_t": created,
            }
        )
    )
    return mules


def _ring(world: World, rng: np.random.Generator, ring_id: str) -> pd.DataFrame:
    rc = world.cfg.rings
    start = int(rng.integers(10 * DAY, world.end_t - 3 * DAY))
    mules = _add_mules(world, rng, ring_id, start)
    size = len(mules)

    # Roles: 1-3 collectors, then layer 2 and layer 3 split the rest.
    n_col = min(int(rng.integers(1, 4)), max(1, size // 8))
    rest = np.arange(n_col, size)
    layer2, layer3 = rest[: len(rest) // 2], rest[len(rest) // 2 :]

    cash_out = f"cashout.{ring_id.lower()}@paytm"
    world.add_payees(
        pd.DataFrame(
            {
                "vpa": [cash_out],
                "user_id": [None],
                "category": ["P2P"],
                "created_t": [start - 60 * DAY],
            }
        )
    )

    rows: list[dict[str, object]] = []
    users = world.users

    def send(m: int, payee: str, amount: float, t: int, channel: str = "UPI") -> None:
        mule = mules.iloc[m]
        rows.append(
            {
                "user_id": mule["user_id"],
                "payee_vpa": payee,
                "amount": round(amount, 2),
                "t": t,
                "device_id": mule["device_id"],
                "ip": mule["home_ip"],
                "geo_lat": mule["home_lat"],
                "geo_lon": mule["home_lon"],
                "channel": channel,
            }
        )

    def forward_share() -> float:
        return float(rng.uniform(*rc.forward_share))

    for c in range(n_col):
        targets = layer2[c::n_col]  # this collector's layer-2 mules
        victims = rng.choice(
            world.n_customers,
            size=int(rng.integers(*rc.victims_per_collector, endpoint=True)),
            replace=False,
        )
        chunk_starts = start + np.sort(rng.integers(0, 20 * HOUR, size=len(targets)))
        for chunk, target, chunk_start in zip(
            np.array_split(victims, len(targets)), targets, chunk_starts, strict=True
        ):
            # Fan-in: a burst of victims pays the collector within 25 minutes ...
            t_in = chunk_start + np.sort(rng.integers(0, 25 * MINUTE, size=len(chunk)))
            amounts = np.round(rng.uniform(2_000, 25_000, size=len(chunk)), 2)
            for v, t, a in zip(chunk, t_in, amounts, strict=True):
                victim = users.iloc[v]
                rows.append(
                    {
                        "user_id": victim["user_id"],
                        "payee_vpa": mules["vpa"].iat[c],
                        "amount": a,
                        "t": int(t),
                        "device_id": world.devices["device_id"].iat[world.device_ptr[v]],
                        "ip": victim["home_ip"],
                        "geo_lat": victim["home_lat"],
                        "geo_lon": victim["home_lon"],
                        "channel": "UPI",
                    }
                )
            # ... and passes most of it on within 30 minutes of the last one (pass-through).
            t2 = int(t_in[-1] + rng.integers(2 * MINUTE, 30 * MINUTE))
            x = forward_share() * amounts.sum()
            send(c, mules["vpa"].iat[target], x, t2)

            # Layer 2 splits it across its layer-3 mules (or cashes out if it has none).
            mine = layer3[np.flatnonzero(layer2 == target)[0] :: len(layer2)]
            if len(mine) == 0:
                send(
                    target,
                    cash_out,
                    forward_share() * x,
                    t2 + int(rng.integers(2 * MINUTE, 40 * MINUTE)),
                    "WALLET",
                )
                continue
            y = forward_share() * x / len(mine)
            for m3 in mine:
                t3 = t2 + int(rng.integers(2 * MINUTE, 40 * MINUTE))
                send(target, mules["vpa"].iat[m3], y, t3)
                # Layer 3 cashes out to a wallet within 40 minutes.
                send(
                    m3,
                    cash_out,
                    forward_share() * y,
                    t3 + int(rng.integers(5 * MINUTE, 40 * MINUTE)),
                    "WALLET",
                )

    # A few victims report the collector later.
    first_victims = [r for r in rows if r["payee_vpa"] in set(mules["vpa"].iloc[:n_col])]
    reporters = [r for r in first_victims if rng.random() < 0.15]
    if reporters:
        world.add_reports(
            pd.DataFrame(
                {
                    "reporter_id": [r["user_id"] for r in reporters],
                    "payee_vpa": [r["payee_vpa"] for r in reporters],
                    "scam_type": "mule_ring",
                    "t": [int(r["t"]) + int(rng.integers(6 * HOUR, 10 * DAY)) for r in reporters],
                }
            )
        )

    cols = {k: np.array([r[k] for r in rows], dtype=object) for k in rows[0]}
    cols["t"] = cols["t"].astype("int64")
    cols["amount"] = cols["amount"].astype(float)
    cols["geo_lat"] = cols["geo_lat"].astype(float)
    cols["geo_lon"] = cols["geo_lon"].astype(float)
    return txn_frame(rng, **cols, scenario="mule_ring", ring_id=ring_id)
