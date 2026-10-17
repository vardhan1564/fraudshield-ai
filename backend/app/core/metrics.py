"""Request middleware: request id, Prometheus metrics and one JSON log line per request."""

import logging
import time
import uuid

from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

logger = logging.getLogger("app.request")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    # Fine buckets below 100 ms: the scoring budget is p95 < 100 ms.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0, 2.5),
)


def route_template(scope: Scope) -> str:
    """Metric label for a request: "/api/v1/cases/123" -> "/api/v1/cases/{id}".

    Never the raw path, which keeps cardinality low and ids out of metrics. Built from the
    path and its params because FastAPI's matched route holds only the path relative to its
    router (no "/api/v1" prefix). Unmatched requests (404 scans) share one label.
    """
    if "endpoint" not in scope:  # set by every matched route, incl. /docs and /metrics
        return "unmatched"
    params = {str(value): name for name, value in scope.get("path_params", {}).items()}
    segments = scope["path"].split("/")
    return "/".join(f"{{{params[s]}}}" if s in params else s for s in segments)


class RequestContextMiddleware:
    """Pure ASGI middleware (cheaper than BaseHTTPMiddleware on the hot path)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming[:64] if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = route_template(scope)
            method = scope["method"]
            REQUESTS.labels(method, route, str(status)).inc()
            LATENCY.labels(method, route).observe(elapsed)
            # Path only, no query string (no PII in logs).
            logger.info(
                "request",
                extra={
                    "method": method,
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
            request_id_var.reset(token)
