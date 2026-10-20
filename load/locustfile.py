"""Load test for POST /api/v1/transactions/score (T3.9, Gate B: p95 < 100 ms at 50 rps).

    locust -f load/locustfile.py --headless --host http://127.0.0.1:8001 \
        --html load/reports/<run>.html --csv load/reports/<run>

Replays simulator payments (load/data/replay.parquet, from load/prepare.py) in time order, each
with a fresh txn_id and the current time. Partner auth: the first key in PARTNER_API_KEYS.

Shape: 30 s warm-up at 25 rps (not measured), 2 minutes at 50 rps (the gate), then +25 rps every
30 s up to RAMP_MAX to find the most the API sustains. Every request is also written to
load/reports/<run>_requests.csv for load/analyze.py.
"""

import csv
import itertools
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from locust import FastHttpUser, LoadTestShape, events, task

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "load" / "data" / "replay.parquet"
IST = timezone(timedelta(hours=5, minutes=30))

WARMUP_RPS, WARMUP_S = 25, 30  # caches, connections, first-call costs; not measured
GATE_RPS, GATE_S = 50, 120
RAMP_STEP_RPS, RAMP_STEP_S = 25, 30
RAMP_MAX = int(os.environ.get("RAMP_MAX", "300"))
# KEEPALIVE=0: a new connection per payment (A/B test of Windows TCP behaviour, T3.9).
CONNECTION = {} if os.environ.get("KEEPALIVE", "1") == "1" else {"Connection": "close"}
RUN = os.environ.get("RUN", datetime.now().strftime("%Y%m%d-%H%M%S"))
LOG = ROOT / "load" / "reports" / f"{RUN}_requests.csv"


def partner_key() -> str:
    """X-API-Key from the environment or the repo's .env (name:key,...: the first key)."""
    raw = os.environ.get("PARTNER_API_KEYS")
    if raw is None:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("PARTNER_API_KEYS="):
                raw = line.split("=", 1)[1]
    if not raw:
        raise SystemExit("Set PARTNER_API_KEYS in .env (name:key) for the load test")
    return raw.split(",")[0].split(":", 1)[1].strip()


def clean(value: Any) -> Any:
    return None if isinstance(value, float) and math.isnan(value) else value


ROWS = pd.read_parquet(REPLAY).to_dict("records")
NEXT = itertools.count()
KEY = partner_key()
LOG.parent.mkdir(parents=True, exist_ok=True)
log_file = LOG.open("w", newline="", encoding="utf-8")
log = csv.writer(log_file)
log.writerow(["t", "client_ms", "server_ms", "status", "decision", "row", "user_id", "payee_vpa"])
started = time.monotonic()


def body(i: int) -> dict[str, Any]:
    r = ROWS[i % len(ROWS)]
    return {
        "txn_id": f"LOAD-{RUN}-{i}",
        "user_id": r["user_id"],
        "amount": float(r["amount"]),
        "currency": "INR",
        "channel": r["channel"],
        "payee_vpa": r["payee_vpa"],
        "merchant_category": r["merchant_category"],
        "device_id": clean(r["device_id"]),
        "ip": clean(r["ip"]),
        "geo": {"lat": float(r["geo_lat"]), "lon": float(r["geo_lon"])},
        "timestamp": datetime.now(IST).isoformat(),
        "context": {
            "on_call": bool(r["on_call"]),
            "caller_in_contacts": bool(r["caller_in_contacts"]),
            "call_duration_s": int(r["call_duration_s"]),
            "screen_share_active": bool(r["screen_share_active"]),
            "remote_access_app": clean(r["remote_access_app"]),
            "is_vpn": bool(r["is_vpn"]),
        },
        "behaviour": {
            "typing_speed_z": float(r["typing_speed_z"]),
            "session_len_s": float(r["session_len_s"]),
            "login_to_pay_s": float(r["login_to_pay_s"]),
        },
    }


def poisson_wait(user: Any) -> float:
    """Payments arrive like real traffic: a Poisson process, on average one per payer per second.
    Each payer keeps its own timeline of random (exponential) arrival times and waits until the
    next one, so response time doesn't lower the rate. constant_throughput(1) started every payer
    in the same second: all 50 fired in lockstep, which measured burst queueing, not the API."""
    now = time.monotonic()
    nxt = max(getattr(user, "next_arrival", now), now - 1.0) + random.expovariate(1.0)
    user.next_arrival = nxt
    return max(0.0, nxt - now)


class Payer(FastHttpUser):
    """One payer = on average one payment per second; the shape sets how many payers there are."""

    wait_time = poisson_wait

    @task
    def score(self) -> None:
        i = next(NEXT)
        payload = body(i)
        sent = time.monotonic()
        with self.client.post(
            "/api/v1/transactions/score",
            json=payload,
            headers={"X-API-Key": KEY, **CONNECTION},
            name="/transactions/score",
            catch_response=True,
        ) as resp:
            client_ms = (time.monotonic() - sent) * 1000
            server_ms, decision = "", ""
            if resp.status_code == 200:
                data = resp.json()
                server_ms, decision = data["latency_ms"], data["decision"]
            else:
                resp.failure(f"HTTP {resp.status_code}")
            log.writerow([round(sent - started - WARMUP_S, 3), round(client_ms, 2), server_ms,
                          resp.status_code, decision, i % len(ROWS), payload["user_id"],
                          payload["payee_vpa"]])  # fmt: skip


class GateThenRamp(LoadTestShape):
    """(users, spawn rate) over time; one user ~ one request per second."""

    def tick(self) -> tuple[int, float] | None:
        t = self.get_run_time()
        if t < WARMUP_S:
            return WARMUP_RPS, WARMUP_RPS
        t -= WARMUP_S
        if t < GATE_S:
            return GATE_RPS, GATE_RPS
        step = int((t - GATE_S) // RAMP_STEP_S) + 1
        target = GATE_RPS + step * RAMP_STEP_RPS
        return (target, RAMP_STEP_RPS) if target <= RAMP_MAX else None


@events.quitting.add_listener
def _close_log(**_kwargs: Any) -> None:
    log_file.close()
