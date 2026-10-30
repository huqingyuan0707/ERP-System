"""健康检查接口测试。"""

import asyncio

import httpx

from app.main import app


def _get_health() -> httpx.Response:
    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/v1/health")

    return asyncio.run(_call())


def test_health() -> None:
    response = _get_health()
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["status"] == "ok"
