"""WebSockets (spec 04 §3) with starlette's TestClient, on the docker Redis (db 13) and test DB.

TestClient runs the app on its own event loop in a thread; database setup and the worker run on
that loop too, through `tc.portal`. The models are the stub from test_scoring (an empty
MODEL_DIR keeps the real models out of memory).
"""

import asyncio
import json
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.security import create_token
from app.main import create_app
from app.models import User
from app.models.enums import Role
from app.services import notify
from app.services.llm.template import TemplateProvider
from app.streaming.consumers import Deps
from app.streaming.worker import Worker, consumers
from tests.test_scoring import PARTNER, URL, StubRegistry, make_user, payment
from tests.test_worker import FakeClaude


@pytest.fixture
def tc(db_url: str, tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=db_url,
        redis_url=Settings().redis_url.rsplit("/", 1)[0] + "/13",
        partner_api_keys="bank-a:test-partner-key",
        model_dir=tmp_path,  # empty: no real models in memory
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            client.portal.call(app.state.redis.flushdb)
            app.state.registry = StubRegistry()
            yield client
    except OSError as exc:  # pragma: no cover - only without docker
        pytest.skip(f"Redis/Postgres not running: {exc}")


def app_of(tc: TestClient) -> FastAPI:
    app: FastAPI = tc.app  # type: ignore[assignment]
    return app


def user(tc: TestClient, role: Role = Role.CUSTOMER) -> User:
    made: User = tc.portal.call(make_user, app_of(tc), role)
    return made


def token(tc: TestClient, who: User) -> str:
    access, _ = create_token(who.id, who.role.value, "access", app_of(tc).state.settings)
    return access


def receive(ws: WebSocketTestSession, timeout: float = 5.0) -> dict[str, Any]:
    """receive_json with a timeout, so a missing message fails the test instead of hanging."""
    pool = ThreadPoolExecutor(1)
    try:
        message: dict[str, Any] = json.loads(pool.submit(ws.receive_text).result(timeout=timeout))
        return message
    finally:
        pool.shutdown(wait=False)


def test_hold_reaches_analysts_within_a_second_and_the_llm_sentence_reaches_the_customer(
    tc: TestClient,
) -> None:
    app = app_of(tc)
    analyst, customer = user(tc, Role.ANALYST), user(tc)
    deps = Deps(app.state.settings, app.state.redis, app.state.sessionmaker,
                [FakeClaude(), TemplateProvider()])  # fmt: skip
    worker = Worker(deps, consumers(app.state.settings), name="ws-test")
    stop = asyncio.Event()
    running = tc.portal.start_task_soon(worker.run, stop)

    async def set_stop() -> None:
        stop.set()

    try:
        with (
            tc.websocket_connect(f"/api/v1/ws/alerts?token={token(tc, analyst)}") as analysts,
            tc.websocket_connect(f"/api/v1/ws/customer?token={token(tc, customer)}") as mine,
        ):
            started = time.perf_counter()
            scored = tc.post(URL, json=payment(customer, 80_000.0), headers=PARTNER).json()
            assert scored["decision"] == "HOLD"
            got, alert_after = {}, None
            while len(got) < 2:  # alert.created and explanation.ready, in either order
                message = receive(analysts)
                got[message["type"]] = message["data"]
                if message["type"] == "alert.created":
                    alert_after = time.perf_counter() - started
            for_customer = receive(mine)
    finally:
        tc.portal.call(set_stop)
        running.result(timeout=10)

    assert alert_after is not None and alert_after < 1.0, f"alert.created took {alert_after} s"
    assert got["alert.created"]["txn_id"] == scored["txn_id"]
    assert got["alert.created"]["decision"] == "HOLD"
    sentence = "Mock LLM: Amount ₹80,000."
    assert got["explanation.ready"] == {"txn_id": scored["txn_id"], "explanation": sentence}
    assert for_customer == {
        "type": "explanation.ready",
        "data": {"txn_id": scored["txn_id"], "explanation": sentence},
    }


def test_customers_only_get_their_own_messages(tc: TestClient) -> None:
    redis = app_of(tc).state.redis
    me, someone = user(tc), user(tc)
    with tc.websocket_connect(f"/api/v1/ws/customer?token={token(tc, me)}") as ws:
        tc.portal.call(notify.to_user, redis, someone.id, "explanation.ready", {"txn_id": "X"})
        tc.portal.call(notify.to_analysts, redis, "alert.created", {"txn_id": "Y"})
        tc.portal.call(notify.to_user, redis, me.id, "explanation.ready", {"txn_id": "MINE"})
        assert receive(ws) == {"type": "explanation.ready", "data": {"txn_id": "MINE"}}


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("/api/v1/ws/alerts", Role.CUSTOMER),  # wrong role
        ("/api/v1/ws/customer", Role.ANALYST),
        ("/api/v1/ws/alerts", None),  # bad token
        ("/api/v1/ws/customer", None),
    ],
)
def test_bad_token_or_role_is_refused(tc: TestClient, path: str, role: Role | None) -> None:
    query = "token=not-a-jwt" if role is None else f"token={token(tc, user(tc, role))}"
    with pytest.raises(WebSocketDisconnect) as refused, tc.websocket_connect(f"{path}?{query}"):
        pass
    assert refused.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as no_token, tc.websocket_connect(path):
        pass
    assert no_token.value.code == 1008
