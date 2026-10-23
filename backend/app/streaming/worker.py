"""The worker process (spec 02 §4-5): `python -m app.streaming.worker`.

Each consumer reads stream:transactions.scored in its own consumer group (named after it), so
every consumer sees every event. A message is tried up to 3 times; after the third failure it is
copied to stream:dead and acknowledged. On start a consumer first finishes the messages it had
in flight before a crash; messages left by a dead worker are claimed after CLAIM_IDLE_MS.
APScheduler runs the periodic jobs: outbox replay and graph_refresh (approvals and drift come
later).
"""

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.logging import setup_logging
from app.core.redis import make_redis
from app.jobs import graph_refresh
from app.services.llm import build_chain
from app.streaming import bus
from app.streaming.consumers import (
    Deps,
    Handler,
    alert_pusher,
    graph_edges,
    llm_explainer,
    txn_writer,
)

logger = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF_S = (0.2, 1.0)  # wait before attempt 2 and 3
BATCH = 10
BLOCK_MS = 1000
CLAIM_IDLE_MS = 60_000
OUTBOX_EVERY_S = 10


@dataclass(frozen=True)
class Consumer:
    name: str  # also its consumer group
    stream: str
    handle: Handler


def consumers(settings: Settings) -> list[Consumer]:
    scored = settings.stream_scored
    return [
        Consumer("txn_writer", scored, txn_writer),
        Consumer("alert_pusher", scored, alert_pusher),
        Consumer("llm_explainer", scored, llm_explainer),
        Consumer("graph_edges", scored, graph_edges),
    ]


class Worker:
    def __init__(
        self,
        deps: Deps,
        consumer_list: Sequence[Consumer],
        name: str | None = None,
        backoff_s: Sequence[float] = BACKOFF_S,
        claim_idle_ms: int = CLAIM_IDLE_MS,
    ) -> None:
        self.deps = deps
        self.consumers = list(consumer_list)
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.backoff_s = backoff_s
        self.claim_idle_ms = claim_idle_ms

    @property
    def redis(self) -> Redis:
        return self.deps.redis

    async def ensure_groups(self) -> None:
        for c in self.consumers:
            try:  # id "0": a new group also takes events published before it existed
                await self.redis.xgroup_create(c.stream, c.name, id="0", mkstream=True)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def process(self, c: Consumer, msg_id: str, fields: dict[str, str]) -> bool:
        """Run the handler with retries; dead-letter after the last failure. Always ACKs.
        True when the handler succeeded."""
        error = ""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                await c.handle(bus.decode(fields), self.deps)
                break
            except Exception as exc:  # any handler bug or outage: retry, then dead-letter
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "consumer failed",
                    extra={
                        "consumer": c.name,
                        "msg_id": msg_id,
                        "attempt": attempt,
                        "error": error,
                    },
                )
                if attempt < ATTEMPTS:
                    await asyncio.sleep(self.backoff_s[min(attempt - 1, len(self.backoff_s) - 1)])
        else:
            await self.redis.xadd(
                bus.DEAD_LETTER,
                {"stream": c.stream, "group": c.name, "msg_id": msg_id, "attempts": str(ATTEMPTS),
                 "error": error[:500], "data": fields.get("data", "")},
                maxlen=bus.STREAM_MAXLEN, approximate=True,
            )  # fmt: skip
            logger.error("message dead-lettered", extra={"consumer": c.name, "msg_id": msg_id})
            await self.redis.xack(c.stream, c.name, msg_id)
            return False
        await self.redis.xack(c.stream, c.name, msg_id)
        return True

    async def _read(self, c: Consumer, start: str, block: int | None) -> list[tuple[str, Any]]:
        resp: Any = await self.redis.xreadgroup(  # redis-py types are too wide to unpack
            c.name, self.name, {c.stream: start}, count=BATCH, block=block
        )
        streams = resp.items() if isinstance(resp, dict) else resp  # RESP3 dict, RESP2 list
        return [(msg_id, fields) for _stream, messages in streams for msg_id, fields in messages]

    async def drain_own_pending(self, c: Consumer) -> None:
        """Messages this consumer name read but never acknowledged (a crash mid-message)."""
        while batch := await self._read(c, "0", None):
            for msg_id, fields in batch:
                if fields:  # trimmed from the stream meanwhile: nothing left to do
                    await self.process(c, msg_id, fields)
                else:
                    await self.redis.xack(c.stream, c.name, msg_id)

    async def claim_abandoned(self, c: Consumer) -> None:
        """Take over messages another (dead) worker left pending for too long."""
        resp: list[Any] = await self.redis.xautoclaim(
            c.stream, c.name, self.name, min_idle_time=self.claim_idle_ms, count=BATCH
        )
        for msg_id, fields in resp[1]:  # [next start id, claimed messages, deleted ids]
            await self.process(c, msg_id, fields)

    async def run_consumer(self, c: Consumer, stop: asyncio.Event) -> None:
        await self.drain_own_pending(c)
        rounds = 0
        while not stop.is_set():
            try:
                for msg_id, fields in await self._read(c, ">", BLOCK_MS):
                    await self.process(c, msg_id, fields)
                rounds += 1
                if rounds % 30 == 0:
                    await self.claim_abandoned(c)
            except RedisError:
                logger.exception("redis unavailable", extra={"consumer": c.name})
                await asyncio.sleep(1)

    def scheduler(self) -> AsyncIOScheduler:
        sched = AsyncIOScheduler(timezone="UTC")
        sched.add_job(
            bus.replay_outbox, "interval", seconds=OUTBOX_EVERY_S,
            args=[self.redis, self.deps.sessionmaker], id="outbox_replay",
            max_instances=1, coalesce=True,
        )  # fmt: skip
        sched.add_job(
            graph_refresh.refresh, "interval", minutes=self.deps.settings.graph_refresh_minutes,
            args=[self.redis, self.deps.sessionmaker], id="graph_refresh",
            max_instances=1, coalesce=True, next_run_time=datetime.now(UTC),  # once at start
        )  # fmt: skip
        return sched

    async def run(self, stop: asyncio.Event) -> None:
        await self.ensure_groups()
        sched = self.scheduler()
        sched.start()
        names = [c.name for c in self.consumers]
        logger.info("worker started", extra={"worker": self.name, "consumers": names})
        try:
            await asyncio.gather(*(self.run_consumer(c, stop) for c in self.consumers))
        finally:
            sched.shutdown(wait=False)
            logger.info("worker stopped", extra={"worker": self.name})


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings)
    redis = make_redis(settings)
    deps = Deps(settings, redis, make_sessionmaker(engine), build_chain(settings))
    stop = asyncio.Event()
    try:
        await Worker(deps, consumers(settings)).run(stop)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):  # Ctrl+C stops the worker
        asyncio.run(main())
