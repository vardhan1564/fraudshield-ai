"""Online feature service (spec 03 §2): the feature vector for one payment, built from Redis.

Offline, ml/features computes every window over the whole history at once. Here the same
definitions run for a single payment, on state kept in Redis (spec 05 §2), read in one
pipeline round trip:

    vel:{user_id}              ZSET  "{txn_id}|{amount}|{status}" scored by epoch second, 30 days
    payees:{user_id}           HASH  payee_vpa -> first successful payment (epoch s)
    payee:{vpa}:senders        ZSET  "{user_id}|{txn_id}" scored by epoch second, 24 h
    profile:{user_id}          HASH  home_lat, home_lon, sim_changed_at (epoch s)
    device:{device_id}         HASH  first_seen (epoch s), is_rooted (0/1)
    rep:{vpa}                  HASH  report_count, account_age_days
    graph:{user_id}            HASH  graph_score, graph_degree, ... (phase 5; 0 until then)

Windows match ml/features/windows.py: only *strictly earlier* events, both ends open, so a
payment never sees itself or anything at the same second. `record()` adds the payment to the
state after it is scored (with its final status). The parity test replays simulator data and
checks every feature against the offline builder.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from redis.asyncio import Redis
from redis.typing import EncodableT, FieldT

from ml.features.context import GRAPH_FEATURES
from ml.features.device import EARTH_RADIUS_KM
from ml.features.schema import DTYPES, FEATURE_NAMES
from ml.features.txn import IST

MINUTE, HOUR, DAY = 60, 3_600, 86_400
HISTORY_S = 30 * DAY  # longest window: amount_ratio_avg
SENDERS_S = DAY  # payee_unique_senders_24h
CALL_LONG_S = 300

FeatureValue = float | int | bool | str
Features = dict[str, FeatureValue]


@dataclass(frozen=True)
class PaymentIn:
    """What the scoring request tells us about one payment (spec 04 §2.1, flattened)."""

    txn_id: str
    user_id: str
    payee_vpa: str
    amount: float
    channel: str  # UPI | CARD | WALLET
    merchant_category: str
    ts: datetime
    device_id: str | None = None
    geo_lat: float | None = None
    geo_lon: float | None = None
    is_vpn: bool = False
    on_call: bool = False
    caller_in_contacts: bool = False
    call_duration_s: int = 0
    screen_share_active: bool = False
    remote_access_app: str | None = None
    typing_speed_z: float = 0.0
    session_len_s: float = 0.0
    login_to_pay_s: float = 0.0
    # The payee's account age when the bank's directory knows it; else from rep:{vpa}.
    payee_account_age_days: int | None = None


def epoch_s(ts: datetime) -> int:
    """Whole epoch seconds, like ml.features.windows.seconds()."""
    if ts.tzinfo is None:
        raise ValueError("timestamps must carry a timezone")
    return math.floor(ts.timestamp())


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Same formula as ml.features.device.haversine_km, for one pair of points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _num(value: str | None) -> float | None:
    return None if value is None or value == "" else float(value)


async def build_features(p: PaymentIn, redis: Redis) -> Features:
    """All 38 spec 03 §2 features for `p`, in FEATURE_NAMES order. One Redis round trip."""
    t = epoch_s(p.ts)
    async with redis.pipeline(transaction=False) as pipe:
        # "(" makes a bound exclusive: events in (t - 30 d, t), as offline.
        pipe.zrangebyscore(f"vel:{p.user_id}", f"({t - HISTORY_S}", f"({t}", withscores=True)
        pipe.hget(f"payees:{p.user_id}", p.payee_vpa)
        pipe.zrangebyscore(f"payee:{p.payee_vpa}:senders", f"({t - SENDERS_S}", f"({t}")
        pipe.hgetall(f"profile:{p.user_id}")
        pipe.hgetall(f"device:{p.device_id}") if p.device_id else pipe.echo("")
        pipe.hgetall(f"rep:{p.payee_vpa}")
        pipe.hgetall(f"graph:{p.user_id}")
        history, first_paid, senders, profile, device, rep, graph = await pipe.execute()

    f: Features = {}
    # --- txn ---
    local = p.ts.astimezone(IST)
    ok_sum, ok_count = 0.0, 0
    counts = {5 * MINUTE: 0, HOUR: 0, DAY: 0}
    sums = {HOUR: 0.0, DAY: 0.0}
    failed_1h = 0
    for member, score in history:
        _, amount_s, status = str(member).rsplit("|", 2)
        amount, age = float(amount_s), t - int(score)
        if status == "SUCCESS":
            ok_sum, ok_count = ok_sum + amount, ok_count + 1
        for window in counts:
            if age < window:
                counts[window] += 1
        for window in sums:
            if age < window:
                sums[window] += amount
        if age < HOUR and status == "FAILED":
            failed_1h += 1
    f["amount"] = p.amount
    f["amount_log"] = math.log1p(p.amount)
    f["amount_ratio_avg"] = p.amount / (ok_sum / ok_count) if ok_count else 1.0
    f["hour"] = local.hour
    f["weekday"] = local.weekday()
    f["is_night"] = local.hour >= 23 or local.hour < 5
    f["channel_upi"] = p.channel == "UPI"
    f["channel_card"] = p.channel == "CARD"
    f["channel_wallet"] = p.channel == "WALLET"
    f["merchant_category"] = p.merchant_category
    # --- velocity (failed attempts count too) ---
    f["txn_count_5m"] = counts[5 * MINUTE]
    f["txn_count_1h"] = counts[HOUR]
    f["txn_count_24h"] = counts[DAY]
    f["amount_sum_1h"] = round(sums[HOUR], 2)
    f["amount_sum_24h"] = round(sums[DAY], 2)
    f["failed_count_1h"] = failed_1h
    # --- payee ---
    report_count = int(rep.get("report_count", 0))
    f["payee_is_new"] = first_paid is None or int(first_paid) >= t
    age_days = p.payee_account_age_days
    f["payee_account_age_days"] = (
        int(rep.get("account_age_days", 0)) if age_days is None else age_days
    )
    f["payee_unique_senders_24h"] = len({str(m).split("|", 1)[0] for m in senders})
    f["payee_reputation"] = min(1.0, 0.1 * report_count)  # spec 01 F7, as offline (T5.5)
    f["payee_report_count"] = report_count
    # --- device ---
    first_seen = _num(device.get("first_seen")) if device else None
    f["device_is_new"] = first_seen is None or t - first_seen < DAY
    f["device_rooted"] = bool(device) and device.get("is_rooted") == "1"
    home_lat, home_lon = _num(profile.get("home_lat")), _num(profile.get("home_lon"))
    known = None not in (home_lat, home_lon, p.geo_lat, p.geo_lon)
    f["ip_distance_km"] = (
        _haversine_km(p.geo_lat, p.geo_lon, home_lat, home_lon)  # type: ignore[arg-type]
        if known
        else 0.0  # unknown home -> 0, as offline
    )
    f["is_vpn"] = p.is_vpn
    sim = _num(profile.get("sim_changed_at"))
    f["sim_changed_7d"] = sim is not None and sim <= t and t - sim <= 7 * DAY
    # --- behaviour, context, coach, graph ---
    f["typing_speed_z"] = p.typing_speed_z
    f["session_len_s"] = p.session_len_s
    f["login_to_pay_s"] = p.login_to_pay_s
    f["ctx_unknown_call"] = p.on_call and not p.caller_in_contacts
    f["ctx_screen_share"] = p.screen_share_active or p.remote_access_app is not None
    f["ctx_call_long"] = p.call_duration_s > CALL_LONG_S
    f["coach_red_flags"] = 0  # no coach session before the first decision
    for name in GRAPH_FEATURES:
        f[name] = float(graph.get(name, 0.0))
    return {name: _cast(name, f[name]) for name in FEATURE_NAMES}


def _cast(name: str, value: FeatureValue) -> FeatureValue:
    dtype = DTYPES[name]
    if dtype == "float64":
        return float(value)
    if dtype == "int64":
        return int(value)
    if dtype == "bool":
        return bool(value)
    return value  # category: kept as its label


async def record(p: PaymentIn, status: str, redis: Redis) -> None:
    """After scoring: add the payment (with its final status) to the user's and payee's state."""
    t = epoch_s(p.ts)
    async with redis.pipeline(transaction=True) as pipe:
        vel = f"vel:{p.user_id}"
        pipe.zadd(vel, {f"{p.txn_id}|{p.amount:.2f}|{status}": t})
        pipe.zremrangebyscore(vel, "-inf", t - HISTORY_S)
        pipe.expire(vel, HISTORY_S)
        if status == "SUCCESS":
            pipe.hsetnx(f"payees:{p.user_id}", p.payee_vpa, t)  # keeps the first payment
            pipe.expire(f"payees:{p.user_id}", 90 * DAY)
        senders = f"payee:{p.payee_vpa}:senders"
        pipe.zadd(senders, {f"{p.user_id}|{p.txn_id}": t})
        pipe.zremrangebyscore(senders, "-inf", t - SENDERS_S)
        pipe.expire(senders, SENDERS_S)
        await pipe.execute()


# --- reference data (written by seed / sync jobs, read by build_features) -----------------------


async def put_profile(
    redis: Redis,
    user_id: str,
    home_lat: float | None,
    home_lon: float | None,
    sim_changed_at: datetime | None,
) -> None:
    values: dict[FieldT, EncodableT] = {
        "home_lat": "" if home_lat is None else repr(home_lat),
        "home_lon": "" if home_lon is None else repr(home_lon),
        "sim_changed_at": "" if sim_changed_at is None else str(epoch_s(sim_changed_at)),
    }
    await redis.hset(f"profile:{user_id}", mapping=values)


async def put_device(redis: Redis, device_id: str, first_seen: datetime, is_rooted: bool) -> None:
    values: dict[FieldT, EncodableT] = {
        "first_seen": str(epoch_s(first_seen)),
        "is_rooted": "1" if is_rooted else "0",
    }
    await redis.hset(f"device:{device_id}", mapping=values)


async def add_report(redis: Redis, payee_vpa: str) -> None:
    await redis.hincrby(f"rep:{payee_vpa}", "report_count", 1)


async def put_report_count(redis: Redis, payee_vpa: str, report_count: int) -> None:
    """The payee's report count as the database has it (POST /reports, T3.8)."""
    await redis.hset(f"rep:{payee_vpa}", "report_count", str(report_count))
