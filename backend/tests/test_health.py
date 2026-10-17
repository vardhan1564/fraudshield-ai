from httpx import AsyncClient


async def test_health_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_response_carries_request_id(client: AsyncClient) -> None:
    generated = await client.get("/api/v1/health")
    echoed = await client.get("/api/v1/health", headers={"X-Request-ID": "abc123"})

    assert len(generated.headers["x-request-id"]) == 32
    assert echoed.headers["x-request-id"] == "abc123"


async def test_docs_and_openapi_load(client: AsyncClient) -> None:
    docs = await client.get("/docs")
    openapi = await client.get("/openapi.json")

    assert docs.status_code == 200
    assert "/api/v1/health" in openapi.json()["paths"]


async def test_metrics_exposes_request_counts_by_route(client: AsyncClient) -> None:
    await client.get("/api/v1/health")

    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'http_requests_total{method="GET",route="/api/v1/health",status="200"}' in response.text
    assert "http_request_duration_seconds_bucket" in response.text
