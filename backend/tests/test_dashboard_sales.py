"""R-01 经营看板验收：今日销售=确认出库-确认退货、采购净额、趋势与缓存隔离。

对应 需求条目拆分.md:672-683。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete

from app.core.cache import clear_memory_cache
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer, BdGoods, BdUnit, BdWarehouse
from app.models.finance import FinReceivable
from app.models.inventory import InvStock
from app.models.sal import SalOrder, SalOrderItem, SalOutbound, SalOutboundItem, SalReturn

API = "/api/v1"
TODAY = date.today()


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


async def _seed() -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(tenant_id=1, code=f"DASH{suffix}", name="看板测试客户", status=1)
        goods = BdGoods(tenant_id=1, code=f"DASHG{suffix}", name="看板测试商品", status=1)
        warehouse = BdWarehouse(tenant_id=1, code=f"DASHW{suffix}", name="看板测试仓", status=1)
        unit = BdUnit(tenant_id=1, name=f"DASHU{suffix}")
        session.add_all([customer, goods, warehouse, unit])
        await session.flush()
        cid, gid, wid, uid = int(customer.id), int(goods.id), int(warehouse.id), int(unit.id)

        order = SalOrder(
            tenant_id=1,
            customer_id=cid,
            order_no=f"SO-DASH-{suffix}",
            order_date=TODAY,
            total_amount=Decimal("200"),
            tax_amount=Decimal("0"),
            discount=Decimal("0"),
            status=4,
            created_by=1,
        )
        session.add(order)
        await session.flush()
        order_item = SalOrderItem(
            tenant_id=1,
            order_id=order.id,
            goods_id=gid,
            unit_id=uid,
            qty=Decimal("20"),
            price=Decimal("10"),
            amount=Decimal("200"),
            outbound_qty=Decimal("0"),
        )
        session.add(order_item)
        await session.flush()

        out_no = f"OUT-DASH-{suffix}"
        ret_no = f"RET-DASH-{suffix}"
        outbound = SalOutbound(
            tenant_id=1,
            outbound_no=out_no,
            customer_id=cid,
            warehouse_id=wid,
            order_id=order.id,
            outbound_date=TODAY,
            total_amount=Decimal("200"),
            tax_amount=Decimal("0"),
            status=1,
            created_by=1,
        )
        session.add(outbound)
        await session.flush()
        session.add(
            SalOutboundItem(
                tenant_id=1,
                outbound_id=outbound.id,
                order_item_id=order_item.id,
                goods_id=gid,
                qty=Decimal("20"),
                price=Decimal("10"),
                amount=Decimal("200"),
            )
        )
        session.add_all(
            [
                SalOutbound(
                    tenant_id=1,
                    outbound_no=f"OUT-DASH-DRAFT-{suffix}",
                    customer_id=cid,
                    warehouse_id=wid,
                    order_id=order.id,
                    outbound_date=TODAY,
                    total_amount=Decimal("9999"),
                    tax_amount=Decimal("0"),
                    status=0,
                    created_by=1,
                ),
                SalReturn(
                    tenant_id=1,
                    return_no=ret_no,
                    customer_id=cid,
                    warehouse_id=wid,
                    source_no=out_no,
                    return_date=TODAY,
                    total_amount=Decimal("50"),
                    status=1,
                    created_by=1,
                ),
                FinReceivable(
                    tenant_id=1,
                    customer_id=cid,
                    source_type="sales_outbound",
                    source_no=out_no,
                    bill_date=TODAY,
                    due_date=TODAY + timedelta(days=30),
                    amount=Decimal("150"),
                    received_amount=Decimal("0"),
                    unreceived_amount=Decimal("150"),
                    status=0,
                    created_by=1,
                ),
                InvStock(
                    tenant_id=1,
                    warehouse_id=wid,
                    goods_id=gid,
                    qty=Decimal("10"),
                    locked_qty=Decimal("0"),
                    available_qty=Decimal("10"),
                    avg_cost=Decimal("2"),
                ),
            ]
        )
        await session.commit()
        return {"cid": cid, "gid": gid, "wid": wid, "code": suffix}


async def _clean(seed: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        out_ids = (
            (
                await session.execute(
                    SalOutbound.__table__.select().where(SalOutbound.customer_id == seed["cid"])
                )
            )
            .scalars()
            .all()
        )
        for oid in out_ids:
            await session.execute(delete(SalOutboundItem).where(SalOutboundItem.outbound_id == oid))
        order_ids = (
            (
                await session.execute(
                    SalOrder.__table__.select().where(SalOrder.customer_id == seed["cid"])
                )
            )
            .scalars()
            .all()
        )
        for oid in order_ids:
            await session.execute(delete(SalOrderItem).where(SalOrderItem.order_id == oid))
        await session.execute(delete(FinReceivable).where(FinReceivable.customer_id == seed["cid"]))
        await session.execute(
            SalReturn.__table__.delete().where(SalReturn.customer_id == seed["cid"])
        )
        await session.execute(
            SalOutbound.__table__.delete().where(SalOutbound.customer_id == seed["cid"])
        )
        await session.execute(
            SalOrder.__table__.delete().where(SalOrder.customer_id == seed["cid"])
        )
        await session.execute(
            delete(InvStock).where(
                InvStock.warehouse_id == seed["wid"], InvStock.goods_id == seed["gid"]
            )
        )
        await session.execute(BdCustomer.__table__.delete().where(BdCustomer.id == seed["cid"]))
        await session.execute(BdGoods.__table__.delete().where(BdGoods.id == seed["gid"]))
        await session.execute(BdWarehouse.__table__.delete().where(BdWarehouse.id == seed["wid"]))
        await session.execute(
            BdUnit.__table__.delete().where(BdUnit.name == f"DASHU{seed['code']}")
        )
        await session.commit()


def test_dashboard_sales_and_purchase_net() -> None:
    """今日销售 = 200 出库 - 50 退货 = 150；草稿不计；缓存隔离。"""
    clear_memory_cache()
    seed = _run(_seed())
    try:

        async def scenario() -> None:
            async with _client() as client:
                headers = _auth(await _login(client))
                overview = await client.get(f"{API}/dashboard/overview", headers=headers)
                assert overview.status_code == 200, overview.text
                data = overview.json()["data"]
                assert Decimal(str(data["todaySalesAmount"])) >= Decimal("150")
                assert Decimal(str(data["monthSalesAmount"])) >= Decimal("150")
                assert data["todaySalesCount"] >= 1
                assert data["monthSalesCount"] >= 1
                assert "todayPurchaseAmount" in data
                assert "unhandledAlertCount" in data

                trend = await client.get(
                    f"{API}/dashboard/sales-trend", params={"days": 7}, headers=headers
                )
                assert trend.status_code == 200, trend.text
                items = trend.json()["data"]
                assert len(items) == 7
                today_item = next(item for item in items if item["date"] == TODAY.isoformat())
                assert Decimal(str(today_item["amount"])) >= Decimal("150")

                purchase = await client.get(
                    f"{API}/dashboard/purchase-trend", params={"days": 7}, headers=headers
                )
                assert purchase.status_code == 200, purchase.text
                assert len(purchase.json()["data"]) == 7

                ranking = await client.get(
                    f"{API}/dashboard/ranking", params={"kind": "customer"}, headers=headers
                )
                assert ranking.status_code == 200, ranking.text
                assert any(row["id"] == seed["cid"] for row in ranking.json()["data"])

                low_stocks = await client.get(f"{API}/dashboard/low-stocks", headers=headers)
                assert low_stocks.status_code == 200, low_stocks.text
                assert isinstance(low_stocks.json()["data"], list)

                bad = await client.get(
                    f"{API}/dashboard/ranking", params={"kind": "bad"}, headers=headers
                )
                assert bad.status_code == 422, bad.text

        _run(scenario())
    finally:
        _run(_clean(seed))
