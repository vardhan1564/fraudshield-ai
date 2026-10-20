"""Prepare the load test (T3.9): a separate database + Redis db, so the dev/demo data stays clean.

    python load/prepare.py            # from the repo root, infra running

1. Creates the database `fraudshield_load` if missing (never touches the dev database) and runs
   the migrations on it.
2. Inserts the simulator customers used by the replay (scoring rejects unknown customers).
3. Fills Redis db 2 with their home location, SIM change, devices and the payees' account age, as
   the bank's systems would (spec 05 §2), and empties it first (db 2 is only for load tests).
4. Writes load/data/replay.parquet: the first N simulator payments in time order.

Prints the DATABASE_URL / REDIS_URL to start the API and worker with.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import pandas as pd
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from app.core.config import Settings  # noqa: E402
from app.models import User  # noqa: E402
from app.models.enums import Language, Role  # noqa: E402
from app.services.features import put_device, put_profile  # noqa: E402

SIM = ROOT / "ml" / "data" / "simulated"
OUT = ROOT / "load" / "data" / "replay.parquet"
DB_NAME = "fraudshield_load"
REDIS_DB = 2
NO_LOGIN = "!"  # not a bcrypt hash: these customers can't log in, they only get scored


def urls() -> tuple[str, str]:
    settings = Settings()
    db = make_url(settings.database_url).set(database=DB_NAME).render_as_string(hide_password=False)
    redis = settings.redis_url.rsplit("/", 1)[0] + f"/{REDIS_DB}"
    return db, redis


async def create_database(url: str) -> None:
    admin = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("select 1 from pg_database where datname = :n"), {"n": DB_NAME}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{DB_NAME}"'))
                print(f"created database {DB_NAME}")
    finally:
        await admin.dispose()


def migrate(url: str) -> None:
    cfg = Config(str(ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")


async def insert_users(url: str, users: pd.DataFrame) -> None:
    rows = [
        {
            "external_id": u.user_id,
            "name": u.name,
            "phone": u.phone,
            "password_hash": NO_LOGIN,
            "role": Role.CUSTOMER,
            "language": Language.EN,
            "city": u.city,
            "city_tier": int(u.city_tier),
            "age_band": u.age_band,
            "kyc_level": int(u.kyc_level),
        }
        for u in users.itertuples()
    ]
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            for start in range(0, len(rows), 2000):
                stmt = insert(User).values(rows[start : start + 2000])
                await conn.execute(stmt.on_conflict_do_nothing(index_elements=["external_id"]))
    finally:
        await engine.dispose()


async def fill_redis(
    url: str, users: pd.DataFrame, devices: pd.DataFrame, txns: pd.DataFrame
) -> None:
    redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.flushdb()
        for u in users.itertuples():
            sim = None if pd.isna(u.sim_changed_at) else u.sim_changed_at.to_pydatetime()
            await put_profile(redis, u.user_id, float(u.home_lat), float(u.home_lon), sim)
        for d in devices.itertuples():
            await put_device(redis, d.device_id, d.first_seen.to_pydatetime(), bool(d.is_rooted))
        ages = txns.groupby("payee_vpa")["payee_account_age_days"].max()
        async with redis.pipeline(transaction=False) as pipe:
            for vpa, age in ages.items():
                pipe.hset(f"rep:{vpa}", "account_age_days", str(int(age)))
            await pipe.execute()
    finally:
        await redis.aclose()


async def main(n: int) -> None:
    db_url, redis_url = urls()
    txns = pd.read_parquet(SIM / "transactions.parquet").sort_values("ts").head(n)
    users = pd.read_parquet(SIM / "users.parquet")
    users = users[users["user_id"].isin(txns["user_id"])]
    devices = pd.read_parquet(SIM / "devices.parquet")
    devices = devices[devices["device_id"].isin(txns["device_id"])]

    await create_database(db_url)
    await asyncio.to_thread(migrate, db_url)
    await insert_users(db_url, users)
    await fill_redis(redis_url, users, devices, txns)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    txns.drop(columns=["scenario", "is_fraud", "hard_negative", "ring_id", "status"]).to_parquet(
        OUT
    )
    print(f"{len(txns):,} payments from {len(users):,} customers, {len(devices):,} devices")
    print(f"replay file: {OUT.relative_to(ROOT)}")
    print(f"DATABASE_URL={db_url}")
    print(f"REDIS_URL={redis_url}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=60_000, help="payments to replay")
    asyncio.run(main(parser.parse_args().n))
