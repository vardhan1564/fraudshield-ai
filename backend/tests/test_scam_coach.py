"""Scam Coach (spec 01 F1, T5.4): question choice, POST /transactions/{id}/coach, and the
digital-arrest done-when: STEP_UP -> one red flag -> HOLD (+ the analyst alert).

The API tests use the stand-in registry of test_scoring.py (risk = amount / 100k, STEP_UP above
0.1, HOLD above 0.5); the last test runs the real trained models when ml/artifacts has them.
"""

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import Settings
from app.ml_runtime.registry import REQUIRED, ModelRegistry
from app.models import Alert, CoachSession, Transaction, User
from app.models.enums import Decision, Role
from app.services.llm.template import TemplateProvider
from app.services.scam_coach import key, predicted_scam, questions_for, red_flags
from app.streaming import bus
from app.streaming.consumers import Deps, alert_pusher
from tests.test_decision import feats
from tests.test_scoring import (  # noqa: F401  # pytest fixtures
    URL,
    app,
    bearer,
    client,
    customer,
    make_user,
    payment,
)

# Spec 03 §4 digital_arrest: an unknown caller for 20+ minutes, screen sharing, a new payee.
DIGITAL_ARREST = {
    "on_call": True,
    "caller_in_contacts": False,
    "call_duration_s": 1500,
    "screen_share_active": True,
    "remote_access_app": "AnyDesk",
}


def coach_url(txn_id: str) -> str:
    return f"/api/v1/transactions/{txn_id}/coach"


# --- question choice -----------------------------------------------------------------------


def test_the_signals_predict_the_scam() -> None:
    long_call = feats(ctx_unknown_call=True, ctx_call_long=True)
    assert predicted_scam(long_call) == "digital_arrest"
    assert predicted_scam(feats(ctx_screen_share=True)) == "digital_arrest"
    assert predicted_scam(feats(ctx_unknown_call=True)) == "fake_support_refund"
    assert predicted_scam(feats(payee_report_count=3)) == "investment_task"


def test_two_or_three_questions_for_the_predicted_scam() -> None:
    arrest = questions_for(feats(ctx_screen_share=True, payee_is_new=True))
    assert arrest == ["on_call_instructed", "threat_or_arrest", "asked_secrecy"]
    refund = questions_for(feats(ctx_unknown_call=True, payee_is_new=True))
    assert refund == ["on_call_instructed", "refund_to_receive", "know_payee"]
    # "have you paid this person before?" only for a payee never paid
    assert questions_for(feats(ctx_unknown_call=True)) == [
        "on_call_instructed",
        "refund_to_receive",
    ]
    for f in (feats(), feats(payee_is_new=True), feats(payee_report_count=9)):
        keys = questions_for(f)
        assert 2 <= len(keys) <= 3 and len(set(keys)) == len(keys)


def test_red_flags_count_the_warning_answers() -> None:
    asked = ["on_call_instructed", "refund_to_receive", "know_payee"]
    safe = {"on_call_instructed": False, "refund_to_receive": False, "know_payee": True}
    assert red_flags(asked, safe) == 0
    assert red_flags(asked, {**safe, "know_payee": False}) == 1  # NO is the red flag here
    assert red_flags(asked, {k: not v for k, v in safe.items()}) == 3


# --- the API -------------------------------------------------------------------------------


async def coached(client: AsyncClient, app: FastAPI, user: User, amount: float) -> dict[str, Any]:  # noqa: F811
    """The customer pays during a digital-arrest call; returns the score response."""
    body = payment(user, amount, merchant_category="P2P", context=DIGITAL_ARREST)
    r = await client.post(URL, json=body, headers=bearer(app, user))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    assert (out["decision"], out["action"]) == ("STEP_UP", "SCAM_COACH")
    return out


async def run_alert_pusher(fastapi_app: FastAPI, txn_id: str) -> None:
    """The worker's alert_pusher on the newest event for this payment (the coach's update)."""
    state = fastapi_app.state
    redis: Redis = state.redis
    events = [bus.decode(f) for _, f in await redis.xrange(state.settings.stream_scored)]
    event = [e for e in events if e["txn_id"] == txn_id][-1]
    deps = Deps(state.settings, redis, state.sessionmaker, [TemplateProvider()])
    await alert_pusher(event, deps)


async def test_digital_arrest_step_up_then_one_red_flag_is_hold(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    """The T5.4 done-when, end to end through the API, the database and the worker's alert."""
    out = await coached(client, app, customer, 20_000.0)  # risk 0.2: STEP_UP on its own
    asked = [q["key"] for q in out["coach_questions"]]
    assert asked == ["on_call_instructed", "threat_or_arrest", "asked_secrecy"]
    assert out["coach_questions"][0]["text"].startswith("Is someone on a call")

    answers = {k: k == "on_call_instructed" for k in asked}  # YES to the first question only
    r = await client.post(coach_url(out["txn_id"]), json={"answers": answers},
                          headers=bearer(app, customer))  # fmt: skip
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["decision"], res["action"], res["red_flags"]) == ("HOLD", "ANALYST_REVIEW", 1)
    assert res["top_reasons"][0]["feature"] == "coach_red_flags"
    assert res["top_reasons"][0]["text"] == "Warning signs in your answers: 1"
    assert res["explanation"].startswith("We stopped this payment because your answers match")
    assert res["explanation_status"] == "pending"
    assert res["risk_score"] >= out["risk_score"]  # the coach never lowers the risk

    async with app.state.sessionmaker() as session:
        txn = await session.scalar(select(Transaction).where(Transaction.txn_id == out["txn_id"]))
        assert txn is not None and txn.decision == Decision.HOLD
        assert txn.action == "ANALYST_REVIEW"
        row = await session.scalar(select(CoachSession).where(CoachSession.txn_id == txn.id))
        assert row is not None and row.red_flags == 1 and row.outcome == "HOLD"
        assert row.completed_at is not None
        assert [q["key"] for q in row.questions] == asked
        assert row.answers is not None and [a["red_flag"] for a in row.answers] == [
            True,
            False,
            False,
        ]

    # the worker raises the analyst alert from the coach's event, as for any HOLD
    await run_alert_pusher(app, out["txn_id"])
    async with app.state.sessionmaker() as session:
        assert txn is not None
        assert await session.scalar(select(Alert).where(Alert.txn_id == txn.id)) is not None
    assert await app.state.redis.exists(key(out["txn_id"])) == 0  # one answer per payment


async def test_no_red_flags_never_releases_a_held_payment(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    out = await coached(client, app, customer, 80_000.0)  # the models alone would hold it
    asked = [q["key"] for q in out["coach_questions"]]
    r = await client.post(coach_url(out["txn_id"]), json={"answers": dict.fromkeys(asked, False)},
                          headers=bearer(app, customer))  # fmt: skip
    res = r.json()
    assert (res["decision"], res["red_flags"]) == ("HOLD", 0)
    assert res["explanation"].startswith("We paused this payment.")


async def test_no_red_flags_on_an_extra_check_asks_for_the_otp(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    out = await coached(client, app, customer, 20_000.0)
    asked = [q["key"] for q in out["coach_questions"]]
    r = await client.post(coach_url(out["txn_id"]), json={"answers": dict.fromkeys(asked, False)},
                          headers=bearer(app, customer))  # fmt: skip
    res = r.json()
    assert (res["decision"], res["action"], res["red_flags"]) == ("STEP_UP", "OTP", 0)


async def test_coach_errors(app: FastAPI, client: AsyncClient, customer: User) -> None:  # noqa: F811
    out = await coached(client, app, customer, 20_000.0)
    url, mine = coach_url(out["txn_id"]), bearer(app, customer)
    asked = [q["key"] for q in out["coach_questions"]]
    good = {"answers": dict.fromkeys(asked, False)}

    other = await make_user(app)
    assert (await client.post(url, json=good, headers=bearer(app, other))).status_code == 404
    analyst = await make_user(app, role=Role.ANALYST)
    assert (await client.post(url, json=good, headers=bearer(app, analyst))).status_code == 403
    missing = {"answers": {asked[0]: True}}
    bad = await client.post(url, json=missing, headers=mine)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    extra = {"answers": {**good["answers"], "promised_returns": True}}
    assert (await client.post(url, json=extra, headers=mine)).status_code == 400
    unknown = coach_url(f"TXN-{uuid.uuid4().hex[:12]}")
    assert (await client.post(unknown, json=good, headers=mine)).status_code == 404

    assert (await client.post(url, json=good, headers=mine)).status_code == 200
    again = await client.post(url, json=good, headers=mine)
    assert again.status_code == 409 and again.json()["error"]["code"] == "COACH_ALREADY_ANSWERED"


async def test_no_coach_without_scam_signals(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    r = await client.post(URL, json=payment(customer, 20_000.0), headers=bearer(app, customer))
    out = r.json()
    assert out["action"] == "OTP" and out["coach_questions"] == []
    assert await app.state.redis.exists(key(out["txn_id"])) == 0
    coach = await client.post(coach_url(out["txn_id"]), json={"answers": {"know_payee": True}},
                              headers=bearer(app, customer))  # fmt: skip
    assert coach.status_code == 404


MODEL_DIR = Settings().model_dir


@pytest.mark.skipif(
    not all((Path(MODEL_DIR) / name).exists() for name in REQUIRED),
    reason="trained models not in ml/artifacts (CI trains none)",
)
async def test_digital_arrest_with_the_real_models(
    app: FastAPI,  # noqa: F811
    client: AsyncClient,  # noqa: F811
    customer: User,  # noqa: F811
) -> None:
    """Spec 07 §5 payment (₹48,500, scam call) on the trained ensemble: the models alone hold it,
    so the coach goes first, and one red flag ends in HOLD."""
    app.state.registry = ModelRegistry.load(Path(MODEL_DIR))
    body = payment(customer, 48_500.0, merchant_category="P2P", context=DIGITAL_ARREST,
                   payee_vpa="refund.help@okbank")  # fmt: skip
    r = await client.post(URL, json=body, headers=bearer(app, customer))
    out = r.json()
    assert (out["decision"], out["action"]) == ("STEP_UP", "SCAM_COACH")
    assert out["risk_score"] > app.state.registry.hold
    asked = [q["key"] for q in out["coach_questions"]]
    answers = {k: k == asked[0] for k in asked}
    res = (await client.post(coach_url(out["txn_id"]), json={"answers": answers},
                             headers=bearer(app, customer))).json()  # fmt: skip
    assert (res["decision"], res["red_flags"]) == ("HOLD", 1)
    assert res["top_reasons"][0]["feature"] == "coach_red_flags"
