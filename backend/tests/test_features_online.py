"""T3.3 parity: online features (Redis state) equal offline features (ml/features) exactly.

Replays a small simulation in time order through build_features() -> record(), filing each
crowd report as soon as it is older than the next payment, then compares all 38 features of
every payment with ml.features.build.build_features (tolerance 1e-6).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Settings
from app.services.features import (
    PaymentIn,
    add_report,
    build_features,
    epoch_s,
    put_device,
    put_profile,
    record,
)
from ml.features.build import build_features as offline_features
from ml.features.schema import DTYPES, FEATURE_NAMES
from ml.simulator.config import load_config
from ml.simulator.run import simulate
from tests.conftest import unavailable


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    url = Settings().redis_url.rsplit("/", 1)[0] + "/14"
    client = Redis.from_url(url, decode_responses=True)
    try:
        await client.flushdb()
    except (OSError, RedisConnectionError) as exc:
        unavailable("Redis", exc)
    yield client
    await client.flushdb()
    await client.aclose()


def _dt(value: pd.Timestamp) -> datetime | None:
    return None if pd.isna(value) else value.to_pydatetime().astimezone(UTC)


def _payment(row: pd.Series) -> PaymentIn:
    return PaymentIn(
        txn_id=row["txn_id"],
        user_id=row["user_id"],
        payee_vpa=row["payee_vpa"],
        amount=float(row["amount"]),
        channel=row["channel"],
        merchant_category=row["merchant_category"],
        ts=row["ts"].to_pydatetime(),
        device_id=row["device_id"],
        geo_lat=float(row["geo_lat"]),
        geo_lon=float(row["geo_lon"]),
        is_vpn=bool(row["is_vpn"]),
        on_call=bool(row["on_call"]),
        caller_in_contacts=bool(row["caller_in_contacts"]),
        call_duration_s=int(row["call_duration_s"]),
        screen_share_active=bool(row["screen_share_active"]),
        remote_access_app=None if pd.isna(row["remote_access_app"]) else row["remote_access_app"],
        typing_speed_z=float(row["typing_speed_z"]),
        session_len_s=float(row["session_len_s"]),
        login_to_pay_s=float(row["login_to_pay_s"]),
        payee_account_age_days=int(row["payee_account_age_days"]),
    )


async def test_online_features_equal_offline_on_replay(redis: Redis) -> None:
    tables = simulate(load_config(n_transactions=4_000, n_users=200))
    txns, users, devices, reports = (
        tables[k] for k in ("transactions", "users", "devices", "reports")
    )
    offline = offline_features(txns, users, devices, reports)

    for u in users.itertuples():
        await put_profile(redis, u.user_id, u.home_lat, u.home_lon, _dt(u.sim_changed_at))
    for d in devices.itertuples():
        first_seen = _dt(d.first_seen)
        assert first_seen is not None
        await put_device(redis, d.device_id, first_seen, bool(d.is_rooted))

    pending = reports.sort_values("ts", kind="stable").reset_index(drop=True)
    report_s = [epoch_s(ts.to_pydatetime()) for ts in pending["ts"]]
    next_report = 0
    mismatches: list[str] = []
    for _, row in txns.sort_values("ts", kind="stable").iterrows():
        p = _payment(row)
        t = epoch_s(p.ts)
        while next_report < len(pending) and report_s[next_report] < t:  # strictly earlier
            await add_report(redis, pending.at[next_report, "payee_vpa"])
            next_report += 1

        online = await build_features(p, redis)
        expected = offline.loc[p.txn_id]
        for name in FEATURE_NAMES:
            got, want = online[name], expected[name]
            if DTYPES[name] == "float64":
                same = np.isclose(float(got), float(want), rtol=1e-6, atol=1e-6)
            else:
                same = got == (str(want) if DTYPES[name] == "category" else want)
            if not same:
                mismatches.append(f"{p.txn_id} {name}: online {got!r} offline {want!r}")
        await record(p, row["status"], redis)

    assert len(txns) >= 100 and next_report > 0
    assert mismatches == [], f"{len(mismatches)} mismatches, first: {mismatches[:10]}"
