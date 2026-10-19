"""GET /models and POST /models/{version}/deploy (spec 04 endpoints 24-25)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ModelVersionOut(BaseModel):
    id: uuid.UUID
    name: str
    version: str
    mlflow_run_id: str | None
    metrics_json: dict[str, Any] | None
    is_active: bool
    loaded: bool  # the version this API process is serving
    deployed_at: datetime | None
    created_at: datetime
