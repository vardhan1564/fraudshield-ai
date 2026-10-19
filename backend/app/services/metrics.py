"""GET /metrics/overview (spec 04 §2.8): the dashboard's KPIs and charts for a time window.

Definitions (spec 04 note, T3.8):
- flagged: decision STEP_UP or HOLD, minus payments an analyst labelled legit.
- fraud_caught: flagged payments. amount_saved_inr: their amount when HOLD (money stopped).
- false_positive_rate: legit labels / all labels in the window (null with no labels yet).
- p95_latency_ms: of the scoring path, from transactions.latency_ms.
- by_hour: IST hour of day; by_type: flagged by simulator scenario (aggregates only, never per
  payment); by_city: flagged by the customer's city, top 10.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Alert, Case, Transaction, User
from app.models.enums import CaseLabel, Decision
from app.schemas.metrics import CityCount, HourCount, MetricsOverview, TypeCount

MAX_WINDOW = timedelta(days=92)
DEFAULT_WINDOW = timedelta(hours=24)
TOP_CITIES = 10


def window(
    date_from: datetime | None, date_to: datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    end = date_to or now
    start = date_from or end - DEFAULT_WINDOW
    if start >= end:
        raise AppError(400, "VALIDATION_ERROR", "`from` must be before `to`")
    if end - start > MAX_WINDOW:
        raise AppError(400, "VALIDATION_ERROR", "The window can be at most 92 days")
    return start, end


async def overview(
    session: AsyncSession, date_from: datetime | None, date_to: datetime | None, now: datetime
) -> MetricsOverview:
    start, end = window(date_from, date_to, now)
    in_window = and_(Transaction.ts >= start, Transaction.ts < end)
    flagged: ColumnElement[bool] = and_(
        Transaction.decision.in_([Decision.STEP_UP, Decision.HOLD]),
        or_(Case.label.is_(None), Case.label != CaseLabel.LEGIT),
    )

    def scored() -> Any:  # FROM transactions + its alert and case, in the window
        return (
            select()
            .select_from(Transaction)
            .outerjoin(Alert, Alert.txn_id == Transaction.id)
            .outerjoin(Case, Case.alert_id == Alert.id)
            .where(in_window)
        )

    totals = (
        await session.execute(
            scored().add_columns(
                func.count(),
                func.count().filter(flagged),
                func.coalesce(
                    func.sum(Transaction.amount).filter(
                        flagged, Transaction.decision == Decision.HOLD
                    ),
                    0,
                ),
                func.percentile_cont(0.95).within_group(Transaction.latency_ms),
                func.count().filter(Case.label.is_not(None)),
                func.count().filter(Case.label == CaseLabel.LEGIT),
            )
        )
    ).one()
    txn_count, caught, saved, p95, labeled, legit = totals

    hour = func.extract("hour", func.timezone("Asia/Kolkata", Transaction.ts)).label("hour")
    hours = {
        int(h): (int(total), int(flags))
        for h, total, flags in await session.execute(
            scored().add_columns(hour, func.count(), func.count().filter(flagged)).group_by(hour)
        )
    }
    scenario = func.coalesce(Transaction.scenario, "unknown").label("scenario")
    types = await session.execute(
        scored().add_columns(scenario, func.count()).where(flagged)
        .group_by(scenario).order_by(func.count().desc(), scenario)
    )  # fmt: skip
    city = func.coalesce(User.city, "unknown").label("city")
    cities = await session.execute(
        scored().join(User, User.id == Transaction.user_id).add_columns(city, func.count())
        .where(flagged).group_by(city).order_by(func.count().desc(), city).limit(TOP_CITIES)
    )  # fmt: skip
    return MetricsOverview(
        date_from=start,
        date_to=end,
        txn_count=int(txn_count),
        fraud_caught=int(caught),
        amount_saved_inr=float(saved),
        false_positive_rate=None if not labeled else round(legit / labeled, 4),
        p95_latency_ms=None if p95 is None else round(float(p95), 1),
        by_hour=[
            HourCount(hour=h, total=hours.get(h, (0, 0))[0], flagged=hours.get(h, (0, 0))[1])
            for h in range(24)
        ],  # fmt: skip
        by_type=[TypeCount(scenario=s, count=int(n)) for s, n in types],
        by_city=[CityCount(city=c, flagged=int(n)) for c, n in cities],
    )
