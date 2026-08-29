import httpx
import pytest

from knowledge_api.app import PROTOCOL_VERSION, create_app

TOKEN = "test-runtime-token"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(*, shutdown_callback=None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(TOKEN, shutdown_callback))
    return httpx.AsyncClient(transport=transport, base_url="http://knowledge-api.test")


@pytest.mark.anyio
async def test_health_requires_runtime_token() -> None:
    async with api_client() as client:
        response = await client.get("/health")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "LOCAL_AUTH_REQUIRED"


@pytest.mark.anyio
async def test_health_reports_protocol_version() -> None:
    async with api_client() as client:
        response = await client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "knowledge-api",
        "version": "1.0.0",
        "protocol_version": PROTOCOL_VERSION,
    }


@pytest.mark.anyio
async def test_rejects_incorrect_runtime_token() -> None:
    async with api_client() as client:
        response = await client.get(
            "/api/v1/runtime/status",
            headers={"Authorization": "Bearer incorrect"},
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_tauri_origin_can_preflight_authenticated_api() -> None:
    async with api_client() as client:
        response = await client.options(
            "/api/v1/knowledge-bases",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


@pytest.mark.anyio
async def test_shutdown_is_authenticated_and_invokes_callback() -> None:
    calls: list[str] = []
    async with api_client(shutdown_callback=lambda: calls.append("stopped")) as client:
        unauthorized = await client.post("/api/v1/runtime/shutdown")
        response = await client.post(
            "/api/v1/runtime/shutdown",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 202
    assert response.json() == {"status": "stopping"}
    assert calls == ["stopped"]
