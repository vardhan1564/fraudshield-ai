"""Model versions (spec 04 endpoints 24-25). The API registers the ensemble it loaded at startup,
so the list always shows what is serving. Deploying another version needs hot reload of new
artifacts, which arrives with retraining (T5.8); until then only the loaded version can be made
active (409 otherwise)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.ml_runtime.registry import ModelRegistry
from app.models import ModelVersion, User
from app.schemas.common import Page
from app.schemas.model_versions import ModelVersionOut
from app.services import audit

NAME = "ensemble"


def metrics_of(registry: ModelRegistry) -> dict[str, Any]:
    t = registry.thresholds
    return {
        "step_up": t["step_up"],
        "hold": t["hold"],
        "validation_out_of_fold": t.get("validation_out_of_fold"),
        "txn_model": registry.base.txn_meta.get("metrics"),
    }


async def register_loaded(session: AsyncSession, registry: ModelRegistry) -> None:
    """Insert the serving version if new; make it active when nothing else is."""
    row = await session.scalar(
        select(ModelVersion).where(
            ModelVersion.name == NAME, ModelVersion.version == registry.version
        )
    )
    if row is None:
        row = ModelVersion(
            name=NAME,
            version=registry.version,
            metrics_json=metrics_of(registry),
            mlflow_run_id=registry.base.txn_meta.get("mlflow_run_id"),
        )
        session.add(row)
        await session.flush()
    active = await session.scalar(
        select(func.count()).where(ModelVersion.name == NAME, ModelVersion.is_active)
    )
    if not active:
        row.is_active, row.deployed_at = True, datetime.now(UTC)
    await session.commit()


def loaded_version(registry: ModelRegistry | None) -> str | None:
    return None if registry is None else registry.version


def out(row: ModelVersion, loaded: str | None) -> ModelVersionOut:
    return ModelVersionOut(
        id=row.id,
        name=row.name,
        version=row.version,
        mlflow_run_id=row.mlflow_run_id,
        metrics_json=row.metrics_json,
        is_active=row.is_active,
        loaded=row.version == loaded,
        deployed_at=row.deployed_at,
        created_at=row.created_at,
    )


async def list_versions(
    session: AsyncSession, loaded: str | None, page: int, page_size: int
) -> Page[ModelVersionOut]:
    total = await session.scalar(select(func.count()).select_from(ModelVersion)) or 0
    rows = await session.scalars(
        select(ModelVersion)
        .order_by(ModelVersion.created_at.desc(), ModelVersion.version)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(items=[out(r, loaded) for r in rows], page=page, page_size=page_size, total=total)


async def deploy(
    session: AsyncSession, version: str, loaded: str | None, admin: User, ip: str | None
) -> ModelVersionOut:
    row = await session.scalar(
        select(ModelVersion).where(ModelVersion.version == version).with_for_update()
    )
    if row is None:
        raise AppError(404, "NOT_FOUND", "Model version not found")
    if version != loaded:
        raise AppError(
            409,
            "MODEL_NOT_LOADED",
            "Only the loaded version can be deployed until hot reload ships with retraining",
        )
    previous = await session.scalar(
        select(ModelVersion.version).where(ModelVersion.name == row.name, ModelVersion.is_active)
    )
    await session.execute(
        update(ModelVersion)
        .where(ModelVersion.name == row.name, ModelVersion.id != row.id)
        .values(is_active=False)
    )
    row.is_active, row.deployed_at = True, datetime.now(UTC)
    audit.record(
        session,
        actor_id=admin.id,
        action="model.deploy",
        entity="model_version",
        entity_id=row.id,
        before={"active": previous},
        after={"active": row.version},
        ip=ip,
    )
    await session.commit()
    return out(row, loaded)
