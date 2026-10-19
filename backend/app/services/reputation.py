"""Payee reputation from crowd reports (spec 01 F7; spec 04 endpoints 17-18).

    reputation_score = min(1, 0.1*report_count + 0.5*confirmed_fraud_cases + 0.3*graph_flag)

A report updates the payee row and the online feature state at once, so the next payment to that
payee already sees the new count (report_count >= 5 -> at least STEP_UP, PRD §8). It is also
published to stream:reports.created; confirmed fraud cases and the rep:{vpa} cache follow in T5.5.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.privacy import mask_phone
from app.models import Payee, Report, User
from app.models.enums import Role
from app.schemas.payees import (
    PayeeReputation,
    ReportCreate,
    ReportCreated,
    ReportView,
    RiskLevel,
)
from app.services import audit, features
from app.streaming import bus

logger = logging.getLogger(__name__)

RECENT = 10


def score(report_count: int, confirmed_fraud: int, graph_flag: bool) -> float:
    """Rounded: 0.1 * 7 is 0.7000000000000001 in floating point, which would read as "high"."""
    raw = 0.1 * report_count + 0.5 * confirmed_fraud + 0.3 * float(graph_flag)
    return round(min(1.0, raw), 4)


def level(value: float) -> RiskLevel:
    """Same bands as the dashboard's risk colours (spec 06 §2)."""
    if value > 0.7:
        return "high"
    if value >= 0.3:
        return "medium"
    return "low"


async def file_report(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    body: ReportCreate,
    reporter: User,
    ip: str | None,
) -> ReportCreated:
    payee = await session.scalar(
        select(Payee).where(Payee.vpa_or_account == body.payee_vpa).with_for_update()
    )
    if payee is None:
        payee = Payee(vpa_or_account=body.payee_vpa)
        session.add(payee)
        await session.flush()
    already = await session.scalar(
        select(Report.id).where(Report.payee_id == payee.id, Report.reporter_id == reporter.id)
    )
    if already is not None:  # one voice per customer: nobody pushes a payee over 5 alone
        raise AppError(409, "ALREADY_REPORTED", "You have already reported this payee")

    report = Report(
        reporter_id=reporter.id,
        payee_id=payee.id,
        phone=body.phone,
        scam_type=body.scam_type.value,
        description=body.description,
        evidence_url=None if body.evidence_url is None else str(body.evidence_url),
    )
    session.add(report)
    payee.report_count += 1
    payee.reputation_score = score(
        payee.report_count, payee.confirmed_fraud_count, payee.graph_flag
    )
    await session.flush()
    audit.record(
        session,
        actor_id=reporter.id,
        action="report.create",
        entity="report",
        entity_id=report.id,
        after={
            "payee_id": str(payee.id),
            "scam_type": report.scam_type,
            "report_count": payee.report_count,
        },  # fmt: skip
        ip=ip,
    )
    await bus.publish(
        redis,
        session,
        settings.stream_reports,
        {
            "report_id": str(report.id),
            "payee_id": str(payee.id),
            "payee_vpa": payee.vpa_or_account,
            "scam_type": report.scam_type,
            "report_count": payee.report_count,
            "reputation_score": payee.reputation_score,
            "reporter_id": str(reporter.id),
        },
    )
    await session.commit()
    try:  # scoring reads the count from Redis (features.py); T5.5 re-syncs it if this fails
        await features.put_report_count(redis, payee.vpa_or_account, payee.report_count)
    except RedisError:
        logger.warning("report count not cached (redis down)", extra={"payee_id": str(payee.id)})
    return ReportCreated(
        report_id=report.id,
        payee_vpa=payee.vpa_or_account,
        report_count=payee.report_count,
        reputation_score=payee.reputation_score,
        risk_level=level(payee.reputation_score),
    )


async def lookup(session: AsyncSession, vpa: str, staff: bool) -> PayeeReputation:
    """Anyone may look a payee up; only staff see the individual reports."""
    vpa = vpa.strip().lower()
    payee = await session.scalar(select(Payee).where(Payee.vpa_or_account == vpa))
    if payee is None:
        return PayeeReputation(
            vpa=vpa, known=False, reputation_score=0.0, risk_level="low", report_count=0,
            confirmed_fraud_count=0, graph_flag=False, account_age_days=None,
            reports_by_type={}, recent_reports=[] if staff else None,
        )  # fmt: skip
    by_type = await session.execute(
        select(Report.scam_type, func.count())
        .where(Report.payee_id == payee.id)
        .group_by(Report.scam_type)
        .order_by(Report.scam_type)
    )
    recent = None
    if staff:
        rows = await session.scalars(
            select(Report)
            .where(Report.payee_id == payee.id)
            .order_by(Report.created_at.desc())
            .limit(RECENT)
        )
        recent = [
            ReportView(
                report_id=r.id,
                scam_type=r.scam_type,
                description=r.description,
                phone=mask_phone(r.phone),
                created_at=r.created_at,
            )  # fmt: skip
            for r in rows
        ]
    value = score(payee.report_count, payee.confirmed_fraud_count, payee.graph_flag)
    return PayeeReputation(
        vpa=vpa,
        known=True,
        reputation_score=value,
        risk_level=level(value),
        report_count=payee.report_count,
        confirmed_fraud_count=payee.confirmed_fraud_count,
        graph_flag=payee.graph_flag,
        account_age_days=payee.account_age_days,
        reports_by_type={kind: int(n) for kind, n in by_type},
        recent_reports=recent,
    )


def is_staff(role: Role) -> bool:
    return role in (Role.ANALYST, Role.ADMIN)
