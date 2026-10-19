"""Alert queue and case work for analysts (spec 04 endpoints 11, 13, 14; PRD F1-F2).

A PATCH changes the alert (status, assignee) and its case (label, notes) in one transaction
together with an audit row and, when the label changes, a `stream:cases.labeled` event (outbox
if Redis is down). After the commit, analysts get `case.updated` over WebSocket.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.privacy import mask_phone
from app.models import Alert, Case, ModelScore, Payee, Transaction, User
from app.models.enums import AlertStatus, Language, Role
from app.schemas.cases import AlertItem, CaseCustomer, CaseDetail, CaseTransaction, CaseUpdate
from app.schemas.common import Page
from app.schemas.transactions import Reason
from app.services import audit, explain, notify
from app.streaming import bus

logger = logging.getLogger(__name__)

STAFF = (Role.ANALYST, Role.ADMIN)


def reasons(shap_json: dict[str, Any] | None, language: Language) -> list[Reason]:
    return explain.top_reasons((shap_json or {}).get("top", []), language)


async def list_alerts(
    session: AsyncSession,
    language: Language,
    status: AlertStatus | None,
    page: int,
    page_size: int,
) -> Page[AlertItem]:
    """Open + assigned alerts by default; most severe first, then oldest (SLA order)."""
    where = Alert.status == status if status is not None else Alert.status != AlertStatus.CLOSED
    total = await session.scalar(select(func.count()).select_from(Alert).where(where)) or 0
    rows = await session.execute(
        select(Alert, Case.id, Transaction, ModelScore.shap_json)
        .join(Transaction, Transaction.id == Alert.txn_id)
        .outerjoin(Case, Case.alert_id == Alert.id)
        .outerjoin(ModelScore, ModelScore.txn_id == Transaction.id)
        .where(where)
        .order_by(Alert.severity.desc(), Alert.created_at, Alert.id)  # enum order low < high
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        AlertItem(
            alert_id=alert.id,
            case_id=case_id,
            txn_id=txn.txn_id,
            severity=alert.severity,
            status=alert.status,
            assigned_to=alert.assigned_to,
            risk_score=txn.risk_score,
            decision=txn.decision,
            amount=float(txn.amount),
            channel=txn.channel,
            top_reasons=reasons(shap_json, language),
            created_at=alert.created_at,
        )
        for alert, case_id, txn, shap_json in rows
    ]
    return Page(items=items, page=page, page_size=page_size, total=total)


async def _load(
    session: AsyncSession, case_id: uuid.UUID, lock: bool = False
) -> tuple[Case, Alert, Transaction, User, Payee, ModelScore | None]:
    query = (
        select(Case, Alert, Transaction, User, Payee, ModelScore)
        .join(Alert, Alert.id == Case.alert_id)
        .join(Transaction, Transaction.id == Alert.txn_id)
        .join(User, User.id == Transaction.user_id)
        .join(Payee, Payee.id == Transaction.payee_id)
        .outerjoin(ModelScore, ModelScore.txn_id == Transaction.id)
        .where(Case.id == case_id)
    )
    if lock:  # two analysts saving at once: the second waits, then sees the first's change
        query = query.with_for_update(of=[Case, Alert])
    row = (await session.execute(query)).first()
    if row is None:
        raise AppError(404, "NOT_FOUND", "Case not found")
    case, alert, txn, user, payee, scores = row
    return case, alert, txn, user, payee, scores


async def get_case(session: AsyncSession, case_id: uuid.UUID, language: Language) -> CaseDetail:
    case, alert, txn, user, payee, scores = await _load(session, case_id)
    return CaseDetail(
        case_id=case.id,
        alert_id=alert.id,
        severity=alert.severity,
        status=alert.status,
        assigned_to=alert.assigned_to,
        label=case.label,
        notes=case.notes,
        resolved_by=case.resolved_by,
        resolved_at=case.resolved_at,
        created_at=case.created_at,
        transaction=CaseTransaction(
            id=txn.id,
            txn_id=txn.txn_id,
            amount=float(txn.amount),
            currency=txn.currency,
            channel=txn.channel,
            merchant_category=txn.merchant_category,
            payee_vpa=payee.vpa_or_account,
            risk_score=txn.risk_score,
            decision=txn.decision,
            action=txn.action,
            model_version=txn.model_version,
            ts=txn.ts,
            customer=CaseCustomer(
                id=user.id,
                external_id=user.external_id,
                name=user.name,
                phone=mask_phone(user.phone),
            ),  # fmt: skip
        ),
        top_reasons=reasons(None if scores is None else scores.shap_json, language),
        explanation=None if scores is None else scores.explanation,
    )


def _state(case: Case, alert: Alert) -> dict[str, Any]:
    return {
        "status": alert.status.value,
        "assigned_to": None if alert.assigned_to is None else str(alert.assigned_to),
        "label": None if case.label is None else case.label.value,
        "notes": case.notes,
    }


async def update_case(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    case_id: uuid.UUID,
    body: CaseUpdate,
    actor: User,
    ip: str | None,
) -> CaseDetail:
    """`null` for status/label/notes means "no change"; `assigned_to: null` unassigns."""
    case, alert, txn, user, payee, _scores = await _load(session, case_id, lock=True)
    before = _state(case, alert)
    sent = body.model_fields_set

    if "assigned_to" in sent:
        if body.assigned_to is not None:
            assignee = await session.get(User, body.assigned_to)
            if assignee is None or assignee.role not in STAFF:
                raise AppError(400, "VALIDATION_ERROR", "assigned_to must be an analyst or admin")
            if alert.status == AlertStatus.OPEN and body.status is None:
                alert.status = AlertStatus.ASSIGNED
        alert.assigned_to = body.assigned_to
    if body.notes is not None:
        case.notes = body.notes
    labeled = body.label is not None and body.label != case.label
    if labeled:
        case.label = body.label
        case.resolved_by = actor.id
        case.resolved_at = datetime.now(UTC)
    if body.status is not None:
        if body.status == AlertStatus.CLOSED and case.label is None:
            raise AppError(
                400, "VALIDATION_ERROR", "Label the case (fraud or legit) before closing"
            )
        alert.status = body.status

    after = _state(case, alert)
    changed = {k: after[k] for k in after if after[k] != before[k]}
    if changed:
        audit.record(
            session,
            actor_id=actor.id,
            action="case.update",
            entity="case",
            entity_id=case.id,
            before={k: before[k] for k in changed},
            after=changed,
            ip=ip,
        )
    if labeled and case.label is not None and case.resolved_at is not None:
        event = {
            "case_id": str(case.id),
            "alert_id": str(alert.id),
            "txn_pk": str(txn.id),
            "txn_id": txn.txn_id,
            "label": case.label.value,
            "previous_label": before["label"],
            "labeled_by": str(actor.id),
            "labeled_at": case.resolved_at.isoformat(),
            "user_id": str(user.id),
            "payee_vpa": payee.vpa_or_account,
            "amount": float(txn.amount),
        }
        await bus.publish(redis, session, settings.stream_cases, event)  # or outbox row
    await session.commit()

    if changed:
        try:  # the change is saved; a missed live update only means a manual refresh
            await notify.to_analysts(
                redis,
                "case.updated",
                {"case_id": str(case.id), "status": after["status"], "label": after["label"]},
            )
        except RedisError:
            logger.warning("case.updated not pushed (redis down)", extra={"case_id": str(case.id)})
    return await get_case(session, case_id, actor.language)
