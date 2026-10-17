from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel

from app.core.errors import AppError


class _Body(BaseModel):
    amount: float


def _add_test_routes(app: FastAPI) -> None:
    @app.post("/_test/validate")
    async def validate(body: _Body) -> dict[str, float]:
        return {"amount": body.amount}

    @app.get("/_test/app-error")
    async def app_error() -> None:
        raise AppError(409, "DUPLICATE_TXN", "Transaction already scored", {"txn_id": "t1"})

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")


async def test_unknown_route_returns_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Not Found", "details": {}}
    }


async def test_wrong_method_returns_method_not_allowed(client: AsyncClient) -> None:
    response = await client.post("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_bad_body_returns_400_validation_error(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.post("/_test/validate", json={"amount": "lots"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"][0]["loc"] == ["body", "amount"]
    assert "lots" not in response.text  # submitted input is not echoed back


async def test_app_error_keeps_its_code_and_details(app: FastAPI, client: AsyncClient) -> None:
    _add_test_routes(app)

    response = await client.get("/_test/app-error")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "DUPLICATE_TXN",
            "message": "Transaction already scored",
            "details": {"txn_id": "t1"},
        }
    }


async def test_unhandled_error_returns_500_without_leaking(
    app: FastAPI, client: AsyncClient
) -> None:
    _add_test_routes(app)

    response = await client.get("/_test/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret internal detail" not in response.text
