"""Normal customer payments and hard negatives (legit rows that look risky)."""

import numpy as np
import pandas as pd

from ml.simulator.population import DAY, GPS_NOISE_DEG, HOUR, World, random_ips, txn_frame


def amount_mults(world: World) -> dict[str, float]:
    """Amount multiplier per category (P2P included)."""
    cfg = world.cfg
    return {name: c.amount_mult for name, c in cfg.merchant_categories.items()} | {
        "P2P": cfg.p2p_amount_mult
    }


def generate_normal(world: World, n: int, rng: np.random.Generator) -> pd.DataFrame:
    """n legit payments by ordinary customers, then some turned into hard negatives."""
    cfg, norm = world.cfg, world.cfg.normal
    users = world.users.iloc[: world.n_customers]
    rate = users["daily_rate"].to_numpy()
    u = rng.choice(len(users), size=n, p=rate / rate.sum())  # busy users pay more often

    # When: a random day, mostly inside the user's usual hours.
    day = rng.integers(0, cfg.days, size=n)
    hour = rng.integers(
        users["active_hour_start"].to_numpy()[u], users["active_hour_end"].to_numpy()[u] + 1
    )
    off = rng.random(n) < norm.off_hours_share
    hour[off] = rng.integers(0, 24, size=off.sum())
    t = day * DAY + hour * HOUR + rng.integers(0, HOUR, size=n)

    # Whom: a usual payee most of the time, otherwise any merchant or customer.
    n_base = world.n_merchants + world.n_customers
    counts = np.diff(world.usual_ptr)[u]
    payee = world.usual_idx[world.usual_ptr[u] + (rng.random(n) * counts).astype(int)]
    new = rng.random(n) >= norm.repeat_payee_share
    payee[new] = rng.integers(0, n_base, size=new.sum())
    payee = np.where(payee == world.n_merchants + u, (payee + 1) % n_base, payee)  # not yourself
    payees = world.payees
    category = payees["category"].to_numpy()[payee]

    # How much: log-normal around the user's average, scaled by category.
    mult = pd.Series(category).map(amount_mults(world)).to_numpy()
    avg = users["avg_txn_amount"].to_numpy()[u]
    amount = np.round(np.maximum(avg * mult * rng.lognormal(0, 0.6, size=n), 1.0), 2)

    channels = list(norm.channel_mix)
    channel = rng.choice(
        channels,
        size=n,
        p=np.array(list(norm.channel_mix.values())) / sum(norm.channel_mix.values()),
    )
    channel[(category == "P2P") & (channel == "CARD")] = "UPI"  # no card payments to people

    # Which phone: the main device 85% of the time.
    first, n_dev = world.device_ptr[u], np.diff(world.device_ptr)[u]
    dev_row = np.where(rng.random(n) < 0.85, first, first + (rng.random(n) * n_dev).astype(int))

    # Where: home network and city, sometimes mobile data or travel.
    ip = users["home_ip"].to_numpy()[u].copy()
    mobile = rng.random(n) < 0.10
    ip[mobile] = random_ips(rng, int(mobile.sum()))
    lat, lon = users["home_lat"].to_numpy()[u], users["home_lon"].to_numpy()[u]
    travel = rng.random(n) < 0.05
    city = rng.choice(len(cfg.cities), size=int(travel.sum()))
    lat, lon = lat.copy(), lon.copy()
    lat[travel] = [cfg.cities[i].lat for i in city]
    lon[travel] = [cfg.cities[i].lon for i in city]

    # Some people happen to be on a call (mostly with contacts) while paying.
    on_call = rng.random(n) < 0.04
    frame = txn_frame(
        rng,
        user_id=users["user_id"].to_numpy()[u],
        payee_vpa=payees["vpa"].to_numpy()[payee],
        amount=amount,
        channel=channel,
        merchant_category=category,
        device_id=world.devices["device_id"].to_numpy()[dev_row],
        ip=ip,
        is_vpn=rng.random(n) < 0.01,
        geo_lat=lat + rng.normal(0, GPS_NOISE_DEG, size=n),
        geo_lon=lon + rng.normal(0, GPS_NOISE_DEG, size=n),
        t=t,
        status=np.where(rng.random(n) < norm.failed_share, "FAILED", "SUCCESS"),
        on_call=on_call,
        caller_in_contacts=on_call & (rng.random(n) < 0.9),
        call_duration_s=np.where(on_call, rng.exponential(240, size=n).astype(int), 0),
        screen_share_active=rng.random(n) < 0.002,
    )
    add_hard_negatives(world, frame, u, rng)
    return frame


def add_hard_negatives(
    world: World, frame: pd.DataFrame, u: np.ndarray, rng: np.random.Generator
) -> None:
    """Turn a share of normal rows into legit look-alikes of fraud, in place."""
    shares = world.cfg.hard_negatives
    sizes = [round(shares[name] * len(frame)) for name in shares]
    order = rng.permutation(len(frame))
    picks = dict(zip(shares, np.split(order[: sum(sizes)], np.cumsum(sizes)[:-1]), strict=True))
    avg = world.users["avg_txn_amount"].to_numpy()[u]
    merchants = world.payees.iloc[: world.n_merchants]

    rows = picks["big_legit"]  # rent, school fees by UPI: 10-40x average
    for cat, part in zip(("RENT", "EDUCATION"), np.array_split(rows, 2), strict=True):
        pool = merchants.loc[merchants["category"] == cat, "vpa"].to_numpy()
        frame.loc[part, "payee_vpa"] = rng.choice(pool, size=len(part))
        frame.loc[part, "merchant_category"] = cat
    frame.loc[rows, "channel"] = "UPI"
    frame.loc[rows, "amount"] = np.round(avg[rows] * rng.uniform(10, 40, size=len(rows)), 2)

    rows = picks["new_payee_legit"]  # first payment to a new shop, 3-10x average
    vpas = rng.choice(merchants["vpa"].to_numpy(), size=len(rows))
    frame.loc[rows, "payee_vpa"] = vpas
    frame.loc[rows, "merchant_category"] = (
        merchants.set_index("vpa").loc[vpas, "category"].to_numpy()
    )
    frame.loc[rows, "amount"] = np.round(avg[rows] * rng.uniform(3, 10, size=len(rows)), 2)

    rows = picks["night_legit"]  # 00:00-04:59
    day = frame.loc[rows, "t"].to_numpy() // DAY
    frame.loc[rows, "t"] = day * DAY + rng.integers(0, 5 * HOUR, size=len(rows))

    rows = picks["call_legit"]  # long call with family or a friend
    frame.loc[rows, ["on_call", "caller_in_contacts"]] = True
    frame.loc[rows, "call_duration_s"] = rng.integers(600, 2400, size=len(rows))

    all_rows = np.concatenate(list(picks.values()))
    frame.loc[all_rows, "status"] = "SUCCESS"
    for name, rows in picks.items():
        frame.loc[rows, "hard_negative"] = name
