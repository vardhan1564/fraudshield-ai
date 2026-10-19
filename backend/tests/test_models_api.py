"""GET /models and POST /models/{version}/deploy (spec 04 endpoints 24-25)."""

import uuid
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AuditLog, ModelVersion
from app.models.enums import Role
from app.services.model_versions import register_loaded
from tests.test_scoring import app, bearer, client, make_user  # noqa: F401  # pytest fixtures


def fake_registry(version: str) -> Any:
    """What register_loaded and the deploy endpoint read from a ModelRegistry."""
    return SimpleNamespace(
        version=version,
        thresholds={"step_up": 0.016, "hold": 0.25, "validation_out_of_fold": {"pr_auc": 0.99}},
        base=SimpleNamespace(
            txn_meta={"metrics": {"test": {"pr_auc": 0.997}}, "mlflow_run_id": "run123"}
        ),  # fmt: skip
    )


async def test_list_and_deploy(app: FastAPI, client: AsyncClient) -> None:  # noqa: F811
    serving = fake_registry(f"ensemble-test-{uuid.uuid4().hex[:6]}")
    other = fake_registry(f"ensemble-test-{uuid.uuid4().hex[:6]}")
    async with app.state.sessionmaker() as session:
        await register_loaded(session, serving)
        await register_loaded(session, serving)  # every restart: still one row
        await register_loaded(session, other)
    app.state.registry = serving
    admin = await make_user(app, role=Role.ADMIN)
    auth = bearer(app, admin)

    listed = (await client.get("/api/v1/models", params={"page_size": 200}, headers=auth)).json()
    mine = {
        m["version"]: m for m in listed["items"] if m["version"] in (serving.version, other.version)
    }
    assert set(mine) == {serving.version, other.version}
    assert mine[serving.version]["loaded"] is True and mine[other.version]["loaded"] is False
    assert mine[serving.version]["metrics_json"]["hold"] == 0.25
    assert mine[serving.version]["mlflow_run_id"] == "run123"

    r = await client.post(f"/api/v1/models/{serving.version}/deploy", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True and r.json()["deployed_at"] is not None
    async with app.state.sessionmaker() as session:
        active = (
            await session.scalars(
                select(ModelVersion.version).where(
                    ModelVersion.name == "ensemble", ModelVersion.is_active
                )
            )
        ).all()
        audit_row = await session.scalar(
            select(AuditLog).where(AuditLog.entity_id == r.json()["id"])
        )
    assert active == [serving.version]  # exactly one active ensemble
    assert audit_row is not None and audit_row.action == "model.deploy"
    assert audit_row.after_json == {"active": serving.version}

    not_loaded = await client.post(f"/api/v1/models/{other.version}/deploy", headers=auth)
    assert not_loaded.status_code == 409
    assert not_loaded.json()["error"]["code"] == "MODEL_NOT_LOADED"
    missing = await client.post("/api/v1/models/no-such-version/deploy", headers=auth)
    assert missing.status_code == 404

    analyst = bearer(app, await make_user(app, role=Role.ANALYST))
    assert (await client.get("/api/v1/models", headers=analyst)).status_code == 403
