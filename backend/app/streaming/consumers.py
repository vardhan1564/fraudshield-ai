"""Consumers of stream:transactions.scored (spec 02 §4). Each runs in its own consumer group, so
every one sees every event, and each is safe to run twice on the same event (retries)."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models import Alert, Case, GraphEdge, ModelScore
from app.models.enums import Decision, EdgeType, Language, Severity
from app.services import notify, persist
from app.services.llm import ExplainRequest, Provider, explain_sentence


@dataclass(frozen=True)
class Deps:
    settings: Settings
    redis: Redis
    sessionmaker: async_sessionmaker[AsyncSession]
    llm_chain: list[Provider]


Handler = Callable[[dict[str, Any], Deps], Awaitable[None]]


def severity(risk: float) -> Severity:
    """Analyst queue priority for a HOLD."""
    if risk >= 0.9:
        return Severity.HIGH
    if risk >= 0.6:
        return Severity.MEDIUM
    return Severity.LOW


async def txn_writer(event: dict[str, Any], deps: Deps) -> None:
    """Every scored payment -> its transactions + model_scores rows (T3.9: moved out of the API)."""
    await persist.ensure_saved(deps.sessionmaker, event)


async def alert_pusher(event: dict[str, Any], deps: Deps) -> None:
    """HOLD -> one alert and its case (once per transaction) + `alert.created` to analysts."""
    if event["decision"] != Decision.HOLD.value:
        return
    await persist.ensure_saved(deps.sessionmaker, event)  # the alert's row may not exist yet
    txn_pk = uuid.UUID(event["id"])
    async with deps.sessionmaker() as session:
        found = (
            await session.execute(
                select(Alert, Case)
                .outerjoin(Case, Case.alert_id == Alert.id)
                .where(Alert.txn_id == txn_pk)
            )
        ).first()
        alert, case = found if found is not None else (None, None)
        if alert is None:
            alert = Alert(txn_id=txn_pk, severity=severity(float(event["risk_score"])))
            session.add(alert)
            await session.flush()
        if case is None:  # every alert gets a case for the analyst to work in (T3.7)
            case = Case(alert_id=alert.id)
            session.add(case)
        await session.commit()
        await session.refresh(alert)
    await notify.to_analysts(
        deps.redis,
        "alert.created",
        {
            "alert_id": str(alert.id),
            "case_id": str(case.id),
            "txn_id": event["txn_id"],
            "risk_score": event["risk_score"],
            "decision": event["decision"],
            "top_reasons": event["top_reasons"],
            "created_at": alert.created_at.isoformat(),
        },
    )


async def llm_explainer(event: dict[str, Any], deps: Deps) -> None:
    """Pending explanations -> LLM sentence (fallback chain) saved + `explanation.ready`."""
    if event.get("explanation_status") != "pending":
        return
    language = Language(event.get("language", Language.EN.value))
    req = ExplainRequest(
        decision=Decision(event["decision"]),
        action=event["action"],
        reasons=[r["text"] for r in event["top_reasons"]],
        language=language,
    )
    text, _provider = await explain_sentence(req, deps.llm_chain, deps.settings.llm_timeout_ms)
    await persist.ensure_saved(deps.sessionmaker, event)  # the row the sentence goes into
    async with deps.sessionmaker() as session:
        await session.execute(
            update(ModelScore)
            .where(ModelScore.txn_id == uuid.UUID(event["id"]))
            .values(explanation=text, explanation_lang=language)
        )
        await session.commit()
    data = {"txn_id": event["txn_id"], "explanation": text}
    await notify.to_user(deps.redis, event["user_id"], "explanation.ready", data)
    await notify.to_analysts(deps.redis, "explanation.ready", data)


def edges_for(event: dict[str, Any]) -> list[GraphEdge]:
    """Spec 05 §3: Account -SENT-> Payee, -USED_DEVICE-> Device, -LOGGED_FROM-> IP."""
    account, ts = f"A:{event['account_id']}", datetime.fromisoformat(event["ts"])
    txn_id = event["txn_id"]
    edges = [
        GraphEdge(edge_type=EdgeType.SENT, src_id=account, dst_id=f"P:{event['payee_vpa']}",
                  amount=Decimal(str(event["amount"])), txn_id=txn_id, ts=ts),
    ]  # fmt: skip
    if event.get("device_id"):
        edges.append(GraphEdge(edge_type=EdgeType.USED_DEVICE, src_id=account,
                               dst_id=f"D:{event['device_id']}", txn_id=txn_id, ts=ts))  # fmt: skip
    if event.get("ip"):
        edges.append(GraphEdge(edge_type=EdgeType.LOGGED_FROM, src_id=account,
                               dst_id=f"I:{event['ip']}", txn_id=txn_id, ts=ts))  # fmt: skip
    return edges


async def graph_edges(event: dict[str, Any], deps: Deps) -> None:
    """Every scored payment -> its graph_edges rows (replaced, so a retry never duplicates)."""
    async with deps.sessionmaker() as session:
        await session.execute(delete(GraphEdge).where(GraphEdge.txn_id == event["txn_id"]))
        session.add_all(edges_for(event))
        await session.commit()
