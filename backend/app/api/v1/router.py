"""All /api/v1 routers. Add new routers here."""

from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    audit_log,
    auth,
    cases,
    health,
    metrics,
    model_versions,
    payees,
    rules,
    transactions,
    ws,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(transactions.router)
api_router.include_router(alerts.router)
api_router.include_router(cases.router)
api_router.include_router(payees.router)
api_router.include_router(rules.router)
api_router.include_router(model_versions.router)
api_router.include_router(metrics.router)
api_router.include_router(audit_log.router)
api_router.include_router(ws.router)
