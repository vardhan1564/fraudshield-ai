"""FastAPI app factory. Run: `cd backend && uvicorn app.main:app --reload --port 8000`."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.core.metrics import RequestContextMiddleware
from app.core.redis import make_redis
from app.ml_runtime.registry import ModelRegistry
from app.schemas.errors import ErrorResponse
from app.services.rules import load_active_rules

logger = logging.getLogger(__name__)


async def load_models_and_rules(app: FastAPI, settings: Settings) -> None:
    """Load every model once (spec 02 §3). Production fails fast; dev/test keep running and
    the score endpoint answers 503 until the models exist."""
    try:
        app.state.registry = await asyncio.to_thread(ModelRegistry.load, settings.model_dir)
    except FileNotFoundError:
        if settings.app_env == "production":
            raise
        logger.warning("models not loaded; /transactions/score returns 503", exc_info=True)
    try:
        async with app.state.sessionmaker() as session:
            app.state.rules = await load_active_rules(session)
    except (OSError, SQLAlchemyError):
        if settings.app_env == "production":
            raise
        logger.warning("rules not loaded (database unreachable)", exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("startup", extra={"app_env": settings.app_env})
        await load_models_and_rules(app, settings)
        yield
        await app.state.engine.dispose()
        await app.state.redis.aclose()
        logger.info("shutdown")

    app = FastAPI(
        title="FraudShield AI",
        version="0.1.0",
        lifespan=lifespan,
        responses={500: {"model": ErrorResponse}},
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.redis = make_redis(settings)
    app.state.registry = None  # filled at startup (lifespan); None -> 503 MODEL_UNAVAILABLE
    app.state.rules = []

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last so it wraps everything, including CORS and error responses.
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(api_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus scrape endpoint (outside /api/v1; not proxied publicly)."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
