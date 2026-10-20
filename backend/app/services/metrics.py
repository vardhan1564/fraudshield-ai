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

    # Two scans instead of four (T4.2: the Overview must load in < 1.5 s on 100k+ payments):
    # 1. every payment in the window: totals + per hour + p95 in one ROLLUP (the NULL-hour row is
    #    the total); 2. flagged payments only: per type and per city in one GROUPING SETS.
    hour = func.extract("hour", func.timezone("Asia/Kolkata", Transaction.ts)).label("hour")
    rows = await session.execute(
        scored()
        .add_columns(
            hour,
            func.count(),
            func.count().filter(flagged),
            func.coalesce(
                func.sum(Transaction.amount).filter(flagged, Transaction.decision == Decision.HOLD),
                0,
            ),
            func.percentile_cont(0.95).within_group(Transaction.latency_ms),
            func.count().filter(Case.label.is_not(None)),
            func.count().filter(Case.label == CaseLabel.LEGIT),
        )
        .group_by(func.rollup(hour))
    )
    hours: dict[int, tuple[int, int]] = {}
    txn_count = caught = labeled = legit = 0
    saved: Any = 0
    p95: Any = None
    for h, total, flags, hour_saved, hour_p95, hour_labeled, hour_legit in rows:
        if h is None:  # the ROLLUP total row
            txn_count, caught, saved, p95 = int(total), int(flags), hour_saved, hour_p95
            labeled, legit = int(hour_labeled), int(hour_legit)
        else:
            hours[int(h)] = (int(total), int(flags))

    scenario = func.coalesce(Transaction.scenario, "unknown").label("scenario")
    city = func.coalesce(User.city, "unknown").label("city")
    groups = await session.execute(
        scored()
        .join(User, User.id == Transaction.user_id)
        .add_columns(func.grouping(scenario), scenario, city, func.count())
        .where(flagged)
        .group_by(func.grouping_sets(scenario, city))
    )
    type_counts: list[tuple[str, int]] = []
    city_counts: list[tuple[str, int]] = []
    for by_city, name, city_name, n in groups:
        if by_city:  # grouping(scenario) = 1: this row is a city total
            city_counts.append((city_name, int(n)))
        else:
            type_counts.append((name, int(n)))
    types = sorted(type_counts, key=lambda r: (-r[1], r[0]))
    cities = sorted(city_counts, key=lambda r: (-r[1], r[0]))[:TOP_CITIES]
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
