"""/models (spec 04 endpoints 24-25): model versions and deploy."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request

from app.api.deps import SessionDep, require_role
from app.models import User
from app.models.enums import Role
from app.schemas.common import Page, PageNo, PageSize
from app.schemas.errors import ErrorResponse
from app.schemas.model_versions import ModelVersionOut
from app.services import model_versions

router = APIRouter(prefix="/models", tags=["models"])
ERRORS: dict[int | str, dict[str, Any]] = {
    c: {"model": ErrorResponse} for c in (401, 403, 404, 409)
}
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]


@router.get("", response_model=Page[ModelVersionOut], responses=ERRORS)
async def list_models(
    request: Request,
    _admin: Admin,
    session: SessionDep,
    page: PageNo = 1,
    page_size: PageSize = 50,
) -> Page[ModelVersionOut]:
    """Versions newest first; `loaded` marks the one this API process serves."""
    loaded = model_versions.loaded_version(request.app.state.registry)
    return await model_versions.list_versions(session, loaded, page, page_size)


@router.post("/{version}/deploy", response_model=ModelVersionOut, responses=ERRORS)
async def deploy_model(
    version: Annotated[str, Path(max_length=128)],
    request: Request,
    admin: Admin,
    session: SessionDep,
) -> ModelVersionOut:
    """Make a version the active one (audited). Until hot reload arrives with retraining (T5.8),
    only the loaded version can be deployed: 409 MODEL_NOT_LOADED for others."""
    loaded = model_versions.loaded_version(request.app.state.registry)
    ip = request.client.host if request.client else None
    return await model_versions.deploy(session, version, loaded, admin, ip)
