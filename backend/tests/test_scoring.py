"""POST /transactions/score (spec 04 §2.1): decisions, errors, auth and what gets saved.

A stand-in registry gives a known risk (amount / 100,000) so each decision is deterministic;
test_registry.py covers the real models. Uses the test database and Redis db 13.
"""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import create_token, hash_password
from app.main import create_app
from app.ml_runtime.registry import ModelOutput
from app.models import ModelScore, Payee, Transaction, User
from app.models.enums import Decision, Language, Role
from app.services import persist
from app.services.decision import ActiveRule
from app.streaming import bus
from tests.conftest import unavailable

PARTNER = {"X-API-Key": "test-partner-key"}
URL = "/api/v1/transactions/score"


class StubRegistry:
    """Same interface as ModelRegistry; risk = amount / 100k, scam score from the call."""

    step_up, hold, boost, version = 0.1, 0.5, 0.25, "ensemble-test"

    def predict(self, features: dict[str, Any], coach_red_flags: int = 0) -> ModelOutput:
        risk = min(1.0, features["amount"] / 100_000)
        social = min(1.0, (0.9 if features["ctx_unknown_call"] else 0.05) + 0.05 * coach_red_flags)
        return ModelOutput(
            scores={"txn_score": risk, "anomaly_score": 0.1, "social_eng_score": social,
                    "graph_score": 0.0},
            meta_score=risk,
            risk_score=risk,
            rule_hits=[],
            contributions={"amount": 1.5, "payee_is_new": 0.4, "hour": -0.2},
        )  # fmt: skip


@pytest.fixture
async def app(db_url: str) -> AsyncIterator[FastAPI]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=db_url,
        redis_url=Settings().redis_url.rsplit("/", 1)[0] + "/13",
        partner_api_keys="bank-a:test-partner-key",
    )
    app = create_app(settings)
    try:
        await app.state.redis.flushdb()
    except (OSError, RedisConnectionError) as exc:
        unavailable("Redis", exc)
    app.state.registry = StubRegistry()
    yield app
    await app.state.redis.aclose()
    await app.state.engine.dispose()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """After each scored payment, save its rows as the worker's txn_writer does in production."""

    async def save_scored(response: Response) -> None:
        if response.request.url.path == URL and response.status_code == 200:
            await save_all_scored(app)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    hooks = {"response": [save_scored]}
    async with AsyncClient(transport=transport, base_url="http://test", event_hooks=hooks) as ac:
        yield ac


async def save_all_scored(app: FastAPI) -> None:
    redis: Redis = app.state.redis
    for _id, fields in await redis.xrange(app.state.settings.stream_scored):
        await persist.ensure_saved(app.state.sessionmaker, bus.decode(fields))  # idempotent


async def make_user(
    app: FastAPI, role: Role = Role.CUSTOMER, language: Language = Language.EN
) -> User:
    async with app.state.sessionmaker() as session:
        user = User(
            external_id=f"U{uuid.uuid4().hex[:8]}",
            name="Test User",
            phone="9812345621",
            email=f"u-{uuid.uuid4().hex[:8]}@test.local",
            password_hash=hash_password("irrelevant-pw", rounds=4),
            role=role,
            language=language,
        )
        session.add(user)
        await session.commit()
        return user


@pytest.fixture
async def customer(app: FastAPI) -> User:
    return await make_user(app)


def bearer(app: FastAPI, user: User) -> dict[str, str]:
    token, _ = create_token(user.id, user.role.value, "access", app.state.settings)
    return {"Authorization": f"Bearer {token}"}


def payment(user: User, amount: float = 1_000.0, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "txn_id": f"TXN-{uuid.uuid4().hex[:12]}",
        "user_id": user.external_id,
        "amount": amount,
        "currency": "INR",
        "channel": "UPI",
        "payee_vpa": f"shop.{uuid.uuid4().hex[:6]}@okhdfc",
        "merchant_category": "GROCERY",
        "device_id": "D-test",
        "ip": "103.21.44.10",
        "geo": {"lat": 19.99, "lon": 73.79},
        "timestamp": datetime.now(UTC).isoformat(),
        "context": {"on_call": False},
        "behaviour": {"typing_speed_z": 0.4, "session_len_s": 95, "login_to_pay_s": 30},
    }
    return {**body, **extra}


async def test_approve_matches_the_spec_shape_and_is_saved(
    app: FastAPI, client: AsyncClient, customer: User
) -> None:
    body = payment(customer, 1_000.0)
    r = await client.post(URL, json=body, headers=PARTNER)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {
        "txn_id", "risk_score", "decision", "action", "model_scores", "top_reasons",
        "explanation", "explanation_status", "coach_questions", "model_version", "degraded",
        "latency_ms",
    }  # fmt: skip
    assert out["decision"] == "APPROVE" and out["action"] == "NONE"
    assert set(out["model_scores"]) == {"transaction", "anomaly", "graph", "social_eng"}
    assert [x["text"] for x in out["top_reasons"]] == [
        "Amount ₹1,000",
        "First payment to this payee",
    ]
    assert out["explanation"] == "This payment looks normal."
    assert out["explanation_status"] == "ready"
    assert out["coach_questions"] == [] and out["degraded"] == ["graph"]
    assert isinstance(out["latency_ms"], int) and out["model_version"] == "ensemble-test"

    # Background task: Postgres rows, Redis feature state, scored event.
    async with app.state.sessionmaker() as session:
        txn = await session.scalar(select(Transaction).where(Transaction.txn_id == body["txn_id"]))
        assert txn is not None and txn.user_id == customer.id and txn.decision.value == "APPROVE"
        assert str(txn.ip) == "103.21.44.10" and float(txn.amount) == 1000.0
        scores = await session.get(ModelScore, txn.id)
        assert scores is not None and scores.explanation_lang == Language.EN
        assert scores.shap_json["top"][0] == {"feature": "amount", "value": 1000.0, "impact": 1.5}
        assert [e["feature"] for e in scores.shap_json["top"]] == ["amount", "payee_is_new", "hour"]
        payee = await session.get(Payee, txn.payee_id)
        assert payee is not None and payee.vpa_or_account == body["payee_vpa"]
    redis: Redis = app.state.redis
    assert await redis.zcard(f"vel:{customer.external_id}") == 1
    assert await redis.hget(f"payees:{customer.external_id}", body["payee_vpa"]) is not None
    events = await redis.xrange(app.state.settings.stream_scored)
    assert json.loads(events[-1][1]["data"])["txn_id"] == body["txn_id"]


async def test_step_up_scam_coach_when_on_an_unknown_call(
    client: AsyncClient, customer: User
) -> None:
    ctx = {"on_call": True, "caller_in_contacts": False, "call_duration_s": 900}
    r = await client.post(URL, json=payment(customer, 20_000.0, context=ctx), headers=PARTNER)
    out = r.json()
    assert out["decision"] == "STEP_UP" and out["action"] == "SCAM_COACH"
    assert 2 <= len(out["coach_questions"]) <= 3
    assert out["coach_questions"][0]["key"] == "on_call_instructed"
    assert out["explanation_status"] == "pending"
    assert out["explanation"].startswith("Before you pay, please answer a few quick questions.")


async def test_step_up_without_scam_signals_asks_for_otp(
    client: AsyncClient, customer: User
) -> None:
    out = (await client.post(URL, json=payment(customer, 20_000.0), headers=PARTNER)).json()
    assert out["decision"] == "STEP_UP" and out["action"] == "OTP"


async def test_hold_goes_to_an_analyst(client: AsyncClient, customer: User) -> None:
    out = (await client.post(URL, json=payment(customer, 80_000.0), headers=PARTNER)).json()
    assert out["decision"] == "HOLD" and out["action"] == "ANALYST_REVIEW"


async def test_admin_rule_overrides_the_model(
    app: FastAPI, client: AsyncClient, customer: User
) -> None:
    app.state.rules.rules = [
        ActiveRule("fuel cap", {"all": [{"field": "merchant_category", "op": "==",
                                         "value": "FUEL"}]}, Decision.HOLD, 1)
    ]  # fmt: skip
    r = await client.post(URL, json=payment(customer, merchant_category="fuel"), headers=PARTNER)
    assert r.json()["decision"] == "HOLD"


async def test_duplicate_txn_id_is_409(client: AsyncClient, customer: User) -> None:
    body = payment(customer)
    assert (await client.post(URL, json=body, headers=PARTNER)).status_code == 200
    again = await client.post(URL, json=body, headers=PARTNER)
    assert again.status_code == 409 and again.json()["error"]["code"] == "DUPLICATE_TXN"


async def test_models_missing_is_503(app: FastAPI, client: AsyncClient, customer: User) -> None:
    app.state.registry = None
    body = payment(customer)
    r = await client.post(URL, json=body, headers=PARTNER)
    assert r.status_code == 503 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"
    app.state.registry = StubRegistry()  # the same txn_id can be scored once models are back
    assert (await client.post(URL, json=body, headers=PARTNER)).status_code == 200


async def test_customers_may_only_score_their_own_payments(
    app: FastAPI, client: AsyncClient, customer: User
) -> None:
    settings = app.state.settings
    token, _ = create_token(customer.id, "customer", "access", settings)
    mine = await client.post(
        URL, json=payment(customer), headers={"Authorization": f"Bearer {token}"}
    )
    assert mine.status_code == 200
    other = payment(customer, user_id="U-someone-else")
    r = await client.post(URL, json=other, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert (await client.post(URL, json=payment(customer))).status_code == 401


async def test_unknown_user_is_404(client: AsyncClient, customer: User) -> None:
    r = await client.post(URL, json=payment(customer, user_id="U-nobody"), headers=PARTNER)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "change",
    [
        {"currency": "USD"},
        {"amount": 0},
        {"amount": 10_000_001},
        {"channel": "CASH"},
        {"merchant_category": "CASINO"},
        {"ip": "not-an-ip"},
        {"timestamp": "2026-10-16T02:14:05"},  # no timezone
        {"surprise": 1},
    ],
)
async def test_bad_requests_are_400(
    client: AsyncClient, customer: User, change: dict[str, Any]
) -> None:
    r = await client.post(URL, json=payment(customer, **change), headers=PARTNER)
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_reasons_and_sentence_follow_the_customer_language(
    app: FastAPI, client: AsyncClient
) -> None:
    hindi = await make_user(app, language=Language.HI)
    out = (await client.post(URL, json=payment(hindi, 80_000.0), headers=PARTNER)).json()
    assert out["decision"] == "HOLD" and out["explanation_status"] == "pending"
    assert [r["text"] for r in out["top_reasons"]] == [
        "राशि ₹80,000", "इस प्राप्तकर्ता को पहला भुगतान"
    ]  # fmt: skip
    assert out["explanation"].startswith("हमने यह भुगतान रोक दिया है।")


EXPLAIN = "/api/v1/transactions/{}/explain"


async def test_explain_returns_the_stored_reasons(
    app: FastAPI, client: AsyncClient, customer: User
) -> None:
    body = payment(customer, 80_000.0)
    scored = (await client.post(URL, json=body, headers=PARTNER)).json()
    r = await client.get(EXPLAIN.format(body["txn_id"]), headers=bearer(app, customer))
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"txn_id", "shap", "explanation", "language"}
    assert out["txn_id"] == body["txn_id"] and out["language"] == "en"
    assert out["explanation"] == scored["explanation"]
    assert [s["feature"] for s in out["shap"]] == ["amount", "payee_is_new", "hour"]
    assert out["shap"][2] == {"feature": "hour", "value": out["shap"][2]["value"],
                              "impact": -0.2, "text": out["shap"][2]["text"]}  # fmt: skip

    # Analysts see any payment, by txn_id or UUID, in another language on request.
    analyst = await make_user(app, role=Role.ANALYST)
    async with app.state.sessionmaker() as session:
        txn = await session.scalar(select(Transaction).where(Transaction.txn_id == body["txn_id"]))
        assert txn is not None
    r = await client.get(
        EXPLAIN.format(txn.id), params={"lang": "mr"}, headers=bearer(app, analyst)
    )
    assert r.status_code == 200 and r.json()["language"] == "mr"
    assert r.json()["shap"][0]["text"] == "रक्कम ₹80,000"
    assert r.json()["explanation"].startswith("आम्ही हे पेमेंट थांबवले आहे.")


async def test_explain_access(app: FastAPI, client: AsyncClient, customer: User) -> None:
    body = payment(customer)
    assert (await client.post(URL, json=body, headers=PARTNER)).status_code == 200
    url = EXPLAIN.format(body["txn_id"])
    stranger = await make_user(app)
    r = await client.get(url, headers=bearer(app, stranger))
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=PARTNER)).status_code == 401  # partners: score only
    missing = await client.get(EXPLAIN.format("TXN-nope"), headers=bearer(app, customer))
    assert missing.status_code == 404
    bad_lang = await client.get(url, params={"lang": "fr"}, headers=bearer(app, customer))
    assert bad_lang.status_code == 400
