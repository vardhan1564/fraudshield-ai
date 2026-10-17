from fastapi import FastAPI
from httpx import AsyncClient

from app.core.metrics import route_template


def test_route_template_replaces_path_params() -> None:
    scope = {
        "path": "/api/v1/cases/123/notes/9",
        "path_params": {"case_id": "123", "note_id": 9},
        "endpoint": object(),
    }

    assert route_template(scope) == "/api/v1/cases/{case_id}/notes/{note_id}"


def test_route_template_groups_unmatched_paths() -> None:
    assert route_template({"path": "/wp-admin/login.php"}) == "unmatched"


async def test_metrics_label_uses_template_not_raw_id(app: FastAPI, client: AsyncClient) -> None:
    @app.get("/api/v1/_test/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    await client.get("/api/v1/_test/items/txn-777")
    await client.get("/api/v1/does-not-exist")
    await client.get("/docs")  # built-in Starlette route, not an APIRoute
    text = (await client.get("/metrics")).text

    assert 'route="/api/v1/_test/items/{item_id}"' in text
    assert "txn-777" not in text
    assert 'route="unmatched",status="404"' in text
    assert 'route="/docs",status="200"' in text
