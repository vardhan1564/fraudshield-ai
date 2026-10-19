"""All /api/v1 routers. Add new routers here."""

from fastapi import APIRouter

from app.api.v1 import auth, health, transactions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(transactions.router)
