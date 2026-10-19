"""Worker integration tests on the docker Redis (db 13) and the test database: the three consumers
end to end, retry + dead-letter, crash recovery, the outbox fallback, and the real
`python -m app.streaming.worker` process."""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.models import Alert, GraphEdge, ModelScore, Outbox, Transaction, User
from app.models.enums import EdgeType, Severity
from app.services.llm import ExplainRequest, Provider
from app.services.llm.template import TemplateProvider
from app.streaming import bus
from app.streaming.consumers import Deps
from app.streaming.worker import Consumer, Worker, consumers
from tests.test_scoring import PARTNER, URL, app, client, customer, payment  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]


class FakeClaude:
    name = "anthropic"

    async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
        return f"Mock LLM: {req.reasons[0]}."


def deps_for(app: FastAPI, chain: list[Provider] | None = None) -> Deps:  # noqa: F811
    return Deps(
        app.state.settings,
        app.state.redis,
        app.state.sessionmaker,
        chain or [FakeClaude(), TemplateProvider()],
    )


async def until(check: Callable[[], Awaitable[bool]], timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.1)


@pytest.fixture
async def running(app: FastAPI) -> AsyncIterator[Callable[[Worker], None]]:  # noqa: F811
    """Start workers in the background; all stop at the end of the test."""
    stop, tasks = asyncio.Event(), []

    def start(worker: Worker) -> None:
        tasks.append(asyncio.create_task(worker.run(stop)))

    yield start
    stop.set()
    await asyncio.gather(*tasks)


async def messages(pubsub: Any, want: int, timeout: float = 15.0) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = []
    deadline = asyncio.get_running_loop().time() + timeout
    while len(got) < want and asyncio.get_running_loop().time() < deadline:
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if msg is not None:
            got.append({"channel": msg["channel"], **json.loads(msg["data"])})
    return got


async def test_hold_end_to_end(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
    running: Callable[[Worker], None],
) -> None:
    redis: Redis = app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe("ws:analysts", f"ws:user:{customer.id}")

    body = payment(customer, 80_000.0)  # the stub registry makes this a HOLD
    scored = (await client.post(URL, json=body, headers=PARTNER)).json()
    assert scored["decision"] == "HOLD" and scored["explanation_status"] == "pending"
    running(Worker(deps_for(app), consumers(app.state.settings), name="w1"))

    async with app.state.sessionmaker() as session:
        txn = await session.scalar(select(Transaction).where(Transaction.txn_id == body["txn_id"]))
        assert txn is not None

    async def done() -> bool:
        async with app.state.sessionmaker() as session:
            alert = await session.scalar(select(Alert).where(Alert.txn_id == txn.id))
            edges = await session.scalar(
                select(func.count()).where(GraphEdge.txn_id == body["txn_id"])
            )
            score = await session.get(ModelScore, txn.id, populate_existing=True)
            return (
                alert is not None
                and edges == 3
                and score is not None
                and (score.explanation or "").startswith("Mock LLM")
            )

    await until(done)
    got = await messages(pubsub, want=3)
    await pubsub.aclose()

    async with app.state.sessionmaker() as session:
        alert = await session.scalar(select(Alert).where(Alert.txn_id == txn.id))
        assert alert is not None and alert.severity == Severity.MEDIUM  # risk 0.8
        rows = (
            await session.scalars(select(GraphEdge).where(GraphEdge.txn_id == body["txn_id"]))
        ).all()
    account = f"A:{customer.external_id}"
    assert {(e.edge_type, e.src_id, e.dst_id) for e in rows} == {
        (EdgeType.SENT, account, f"P:{body['payee_vpa']}"),
        (EdgeType.USED_DEVICE, account, "D:D-test"),
        (EdgeType.LOGGED_FROM, account, "I:103.21.44.10"),
    }
    assert next(e for e in rows if e.edge_type == EdgeType.SENT).amount == 80_000

    sentence = "Mock LLM: Amount ₹80,000."
    kinds = {(m["channel"], m["type"]) for m in got}
    assert kinds == {
        ("ws:analysts", "alert.created"),
        ("ws:analysts", "explanation.ready"),
        (f"ws:user:{customer.id}", "explanation.ready"),
    }
    alert_msg = next(m for m in got if m["type"] == "alert.created")["data"]
    assert alert_msg["txn_id"] == body["txn_id"] and alert_msg["alert_id"] == str(alert.id)
    assert set(alert_msg) == {"alert_id", "case_id", "txn_id", "risk_score", "decision",
                              "top_reasons", "created_at"}  # fmt: skip
    mine = next(m for m in got if m["channel"] == f"ws:user:{customer.id}")["data"]
    assert mine == {"txn_id": body["txn_id"], "explanation": sentence}


async def test_approve_makes_no_alert_and_no_llm_call(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
    running: Callable[[Worker], None],
) -> None:
    calls = 0

    class Counting(FakeClaude):
        async def sentence(self, req: ExplainRequest, timeout_s: float) -> str:
            nonlocal calls
            calls += 1
            return "x"

    body = payment(customer, 1_000.0)
    assert (await client.post(URL, json=body, headers=PARTNER)).json()["decision"] == "APPROVE"
    running(Worker(deps_for(app, [Counting(), TemplateProvider()]), consumers(app.state.settings)))

    async def edges_written() -> bool:
        async with app.state.sessionmaker() as session:
            n = await session.scalar(select(func.count()).where(GraphEdge.txn_id == body["txn_id"]))
            return n == 3

    await until(edges_written)
    await asyncio.sleep(0.5)  # give the other consumers time to (not) act
    async with app.state.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Alert).join(
            Transaction, Transaction.id == Alert.txn_id).where(
                Transaction.txn_id == body["txn_id"])) == 0  # fmt: skip
    assert calls == 0


async def test_retry_then_dead_letter(app: FastAPI, running: Callable[[Worker], None]) -> None:  # noqa: F811
    redis: Redis = app.state.redis
    stream, attempts = "stream:test", {"flaky": 0, "broken": 0}

    async def flaky(event: dict[str, Any], deps: Deps) -> None:
        attempts["flaky"] += 1
        if attempts["flaky"] < 3:
            raise ConnectionError("database blip")

    async def broken(event: dict[str, Any], deps: Deps) -> None:
        attempts["broken"] += 1
        raise ValueError("bad event")

    await redis.xadd(stream, bus.encode({"txn_id": "T1"}))
    worker = Worker(
        deps_for(app),
        [Consumer("flaky", stream, flaky), Consumer("broken", stream, broken)],
        backoff_s=(0.0,),
    )
    await worker.ensure_groups()  # so the check below never races the worker's start-up
    running(worker)

    async def settled() -> bool:
        pending = [await redis.xpending(stream, g) for g in ("flaky", "broken")]
        return (
            attempts["broken"] == 3
            and attempts["flaky"] == 3
            and all(p["pending"] == 0 for p in pending)
        )

    await until(settled)
    dead = await redis.xrange(bus.DEAD_LETTER)
    assert len(dead) == 1  # only the handler that never succeeded
    entry = dead[0][1]
    assert entry["group"] == "broken" and entry["stream"] == stream and entry["attempts"] == "3"
    assert entry["error"] == "ValueError: bad event"
    assert json.loads(entry["data"]) == {"txn_id": "T1"}


async def test_crash_recovery_finishes_own_pending(
    app: FastAPI,  # noqa: F811
    running: Callable[[Worker], None],
) -> None:
    redis: Redis = app.state.redis
    stream, seen = "stream:crash", []

    async def record(event: dict[str, Any], deps: Deps) -> None:
        seen.append(event["n"])

    c = Consumer("recorder", stream, record)
    worker = Worker(deps_for(app), [c], name="crashy")
    await worker.ensure_groups()
    await redis.xadd(stream, bus.encode({"n": 1}))
    await redis.xreadgroup("recorder", "crashy", {stream: ">"})  # read, then "crash" before ACK
    await redis.xadd(stream, bus.encode({"n": 2}))
    running(worker)

    async def both() -> bool:
        return sorted(seen) == [1, 2]

    await until(both)
    assert (await redis.xpending(stream, "recorder"))["pending"] == 0


async def test_outbox_when_redis_is_down(app: FastAPI) -> None:  # noqa: F811
    marker = uuid.uuid4().hex
    down = Redis.from_url("redis://127.0.0.1:1/0", decode_responses=True)  # nothing listens
    async with app.state.sessionmaker() as session:
        assert not await bus.publish(down, session, "stream:outbox-test", {"marker": marker})
        await session.commit()
    await down.aclose()

    assert await bus.replay_outbox(app.state.redis, app.state.sessionmaker) >= 1
    entries = await app.state.redis.xrange("stream:outbox-test")
    assert [bus.decode(f)["marker"] for _id, f in entries] == [marker]
    async with app.state.sessionmaker() as session:
        row = await session.scalar(select(Outbox).where(Outbox.payload["marker"].astext == marker))
        assert row is not None and row.sent_at is not None
    assert await bus.replay_outbox(app.state.redis, app.state.sessionmaker) == 0  # sent once


async def test_worker_command_runs(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    """`python -m app.streaming.worker` as its own process, on the test Redis and database."""
    body = payment(customer, 80_000.0)
    assert (await client.post(URL, json=body, headers=PARTNER)).json()["decision"] == "HOLD"
    settings = app.state.settings
    env = {**os.environ, "DATABASE_URL": settings.database_url, "REDIS_URL": settings.redis_url,
           "LLM_PROVIDER": "template", "PYTHONIOENCODING": "utf-8"}  # fmt: skip
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.streaming.worker"],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )  # fmt: skip
    try:

        async def alerted() -> bool:
            assert proc.poll() is None, proc.stderr.read().decode() if proc.stderr else ""
            async with app.state.sessionmaker() as session:
                n = await session.scalar(
                    select(func.count()).select_from(Alert)
                    .join(Transaction, Transaction.id == Alert.txn_id)
                    .where(Transaction.txn_id == body["txn_id"])
                )  # fmt: skip
                return n == 1

        await until(alerted, timeout=60)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
