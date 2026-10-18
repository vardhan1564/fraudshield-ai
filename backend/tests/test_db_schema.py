"""Spec 05 tables: models match the spec, the Alembic migration matches the models and runs both
ways, audit_log is append-only, and the seed script loads the spec 05 §4 demo data.

Database tests use a separate `<db>_test` database (created if missing), never the dev data.
Like test_services.py they skip locally without Docker and fail in CI.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.models import Base
from scripts.seed import row_counts, seed
from tests.conftest import alembic_config, ensure_test_db, test_db_url

SPEC_TABLES = {
    "users": {"id", "external_id", "name", "phone", "email", "password_hash", "role", "language",
              "city", "city_tier", "age_band", "kyc_level", "avg_txn_amount"},
    "devices": {"id", "user_id", "fingerprint", "os", "is_rooted", "first_seen", "last_seen"},
    "payees": {"id", "vpa_or_account", "account_user_id", "display_name", "account_age_days",
               "report_count", "confirmed_fraud_count", "reputation_score", "graph_flag"},
    "transactions": {"id", "txn_id", "user_id", "payee_id", "device_id", "amount", "currency",
                     "channel", "merchant_category", "ip", "geo_lat", "geo_lon", "context_json",
                     "ts", "risk_score", "decision", "action", "model_version", "scenario",
                     "latency_ms"},
    "model_scores": {"txn_id", "transaction_score", "anomaly_score", "graph_score",
                     "social_eng_score", "meta_score", "shap_json", "explanation",
                     "explanation_lang"},
    "coach_sessions": {"id", "txn_id", "questions", "answers", "red_flags", "outcome",
                       "completed_at"},
    "alerts": {"id", "txn_id", "severity", "status", "assigned_to"},
    "cases": {"id", "alert_id", "label", "notes", "resolved_by", "resolved_at"},
    "reports": {"id", "reporter_id", "payee_id", "phone", "scam_type", "description",
                "evidence_url"},
    "trusted_contacts": {"id", "user_id", "contact_user_id", "approval_limit", "active"},
    "approvals": {"id", "txn_id", "contact_id", "status", "expires_at", "responded_at"},
    "rules": {"id", "name", "condition_json", "action", "priority", "is_active", "updated_by"},
    "model_versions": {"id", "name", "version", "mlflow_run_id", "metrics_json", "is_active",
                       "deployed_at"},
    "audit_log": {"id", "actor_id", "action", "entity", "entity_id", "before_json", "after_json",
                  "ip", "ts"},
    "graph_edges": {"id", "edge_type", "src_id", "dst_id", "amount", "txn_id", "ts"},
    "rings": {"id", "member_ids", "score", "detector", "first_seen", "last_seen", "status"},
    "outbox": {"id", "stream", "payload", "created_at", "sent_at"},
}  # fmt: skip


def test_models_have_every_spec_table_and_column() -> None:
    assert set(Base.metadata.tables) == set(SPEC_TABLES)
    for name, columns in SPEC_TABLES.items():
        table = Base.metadata.tables[name]
        assert columns | {"created_at"} == set(table.columns.keys()), name


def test_enums_hold_spec_values() -> None:
    cols = Base.metadata.tables
    assert cols["users"].c.role.type.enums == ["customer", "analyst", "admin", "partner"]
    assert cols["transactions"].c.decision.type.enums == ["APPROVE", "STEP_UP", "HOLD"]
    assert cols["rings"].c.status.type.enums == ["suspected", "confirmed", "dismissed"]


def test_spec_indexes_exist() -> None:
    def indexed(table: str) -> set[tuple[str, ...]]:
        return {
            tuple(getattr(e, "name", None) or str(e) for e in ix.expressions)
            for ix in Base.metadata.tables[table].indexes
        }

    assert {("user_id", "ts DESC"), ("payee_id", "ts DESC")} <= indexed("transactions")
    assert ("status", "created_at") in indexed("alerts")
    assert ("payee_id",) in indexed("reports")
    assert {("ts",), ("src_id",), ("dst_id",)} <= indexed("graph_edges")


# --- against a real Postgres -------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated() -> Iterator[str]:
    """A test database at `head`, after a full downgrade/upgrade round trip."""
    url = test_db_url()
    asyncio.run(ensure_test_db(url))
    cfg = alembic_config(url)
    command.downgrade(cfg, "base")  # start clean even after an interrupted run
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield url


@pytest.fixture
async def engine(migrated: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated)
    yield eng
    await eng.dispose()


async def test_migration_matches_models(engine: AsyncEngine) -> None:
    def diff(conn: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(conn), Base.metadata))

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_audit_log_is_append_only(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("insert into audit_log (action, entity) values ('login', 'user')"))
    for statement in ("update audit_log set action = 'x'", "delete from audit_log"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with engine.begin() as conn:
                await conn.execute(text(statement))


async def test_seed_loads_spec_counts_and_is_idempotent(engine: AsyncEngine) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        first = await seed(session, rounds=4)  # cheap bcrypt for tests
        again = await seed(session, rounds=4)
        counts = await row_counts(session)
        reported = await session.scalar(text("select count(*) from payees where report_count > 0"))
        roles = dict(
            (await session.execute(text("select role, count(*) from users group by role"))).all()
        )

    assert first.created["users"] == 23 and len(first.credentials) == 23
    assert again.created == {} and again.credentials == []
    assert counts == {"users": 23, "trusted_contacts": 20, "payees": 50, "reports": 12, "rules": 5}
    assert reported == 5
    assert roles == {"admin": 1, "analyst": 2, "customer": 20}
