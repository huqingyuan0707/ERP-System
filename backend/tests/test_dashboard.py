"""R-01 经营看板只读接口回归。

验证接口已注册、字段为 camelCase、聚合 SQL 可执行，且销售指标不返回模拟数据。
"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.main import app

API = "/api/v1"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{API}/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


def test_dashboard_overview_uses_real_aggregates() -> None:
    """概览字段对齐前端 camelCase，销售模块未上线前不使用模拟数据。"""

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            resp = await client.get(f"{API}/dashboard/overview", headers=headers)
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            for key in (
                "todaySalesAmount",
                "monthSalesAmount",
                "todaySalesCount",
                "monthSalesCount",
                "totalInventoryValue",
                "totalInventoryQty",
                "lowStockCount",
                "totalReceivable",
                "totalPayable",
                "totalCustomers",
                "totalSuppliers",
            ):
                assert key in data, (key, data)
            assert float(data["todaySalesAmount"]) == 0
            assert float(data["monthSalesAmount"]) == 0
            assert data["todaySalesCount"] == 0 and data["monthSalesCount"] == 0
            assert float(data["totalInventoryValue"]) >= 0
            assert float(data["totalInventoryQty"]) >= 0
            assert data["lowStockCount"] >= 0
            assert data["totalCustomers"] >= 0 and data["totalSuppliers"] >= 0

    _run(scenario())


def test_dashboard_trend_and_category() -> None:
    """趋势按天连续、分类字段为 camelCase，days 边界返回 422。"""

    async def scenario() -> None:
        async with _client() as client:
            headers = _auth(await _login(client))
            trend = await client.get(
                f"{API}/dashboard/sales-trend", params={"days": 7}, headers=headers
            )
            assert trend.status_code == 200, trend.text
            items = trend.json()["data"]
            assert len(items) == 7, items
            assert all({"date", "amount", "count"} <= set(item) for item in items)
            dates = [item["date"] for item in items]
            assert dates == sorted(dates) and len(set(dates)) == 7

            category = await client.get(f"{API}/dashboard/inventory-category", headers=headers)
            assert category.status_code == 200, category.text
            rows = category.json()["data"]
            assert isinstance(rows, list)
            assert all({"categoryName", "value", "qty"} <= set(row) for row in rows)

            bad_days = await client.get(
                f"{API}/dashboard/sales-trend", params={"days": 0}, headers=headers
            )
            assert bad_days.status_code == 422, bad_days.text

    _run(scenario())
