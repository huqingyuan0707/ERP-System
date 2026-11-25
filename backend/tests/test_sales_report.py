"""R-03 销售报表验收：四视图、合计、退货负数行、草稿排除与筛选。

对应 需求条目拆分.md:698-709。
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

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer, BdGoods, BdUnit, BdWarehouse
from app.models.sal import (
    SalOrder,
    SalOrderItem,
    SalOutbound,
    SalOutboundItem,
    SalReturn,
    SalReturnItem,
)

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
        customer = BdCustomer(tenant_id=1, code=f"RPTC{suffix}", name="报表测试客户", status=1)
        goods = BdGoods(tenant_id=1, code=f"RPTG{suffix}", name="报表测试商品", status=1)
        warehouse = BdWarehouse(tenant_id=1, code=f"RPTW{suffix}", name="报表测试仓库", status=1)
        unit = BdUnit(tenant_id=1, name=f"RPTU{suffix}")
        session.add_all([customer, goods, warehouse, unit])
        await session.flush()
        cid, gid, wid, uid = (
            int(customer.id),
            int(goods.id),
            int(warehouse.id),
            int(unit.id),
        )

        order = SalOrder(
            tenant_id=1,
            customer_id=cid,
            order_no=f"SO-RPT-{suffix}",
            order_date=TODAY - timedelta(days=6),
            total_amount=Decimal("1000"),
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
            qty=Decimal("200"),
            price=Decimal("10"),
            amount=Decimal("2000"),
            outbound_qty=Decimal("0"),
        )
        session.add(order_item)
        await session.flush()

        out_no = f"OUT-RPT-{suffix}"
        draft_no = f"OUT-RPT-DRAFT-{suffix}"
        ret_no = f"RET-RPT-{suffix}"
        outbound = SalOutbound(
            tenant_id=1,
            outbound_no=out_no,
            customer_id=cid,
            warehouse_id=wid,
            order_id=order.id,
            outbound_date=TODAY - timedelta(days=5),
            total_amount=Decimal("1000"),
            tax_amount=Decimal("0"),
            status=1,
            created_by=1,
        )
        draft = SalOutbound(
            tenant_id=1,
            outbound_no=draft_no,
            customer_id=cid,
            warehouse_id=wid,
            order_id=order.id,
            outbound_date=TODAY - timedelta(days=4),
            total_amount=Decimal("999999"),
            tax_amount=Decimal("0"),
            status=0,
            created_by=1,
        )
        ret = SalReturn(
            tenant_id=1,
            return_no=ret_no,
            customer_id=cid,
            warehouse_id=wid,
            source_no=out_no,
            return_date=TODAY - timedelta(days=3),
            total_amount=Decimal("100"),
            status=1,
            created_by=1,
        )
        session.add_all([outbound, draft, ret])
        await session.flush()
        session.add_all(
            [
                SalOutboundItem(
                    tenant_id=1,
                    outbound_id=outbound.id,
                    order_item_id=order_item.id,
                    goods_id=gid,
                    qty=Decimal("100"),
                    price=Decimal("10"),
                    amount=Decimal("1000"),
                ),
                SalOutboundItem(
                    tenant_id=1,
                    outbound_id=draft.id,
                    order_item_id=order_item.id,
                    goods_id=gid,
                    qty=Decimal("99999"),
                    price=Decimal("10"),
                    amount=Decimal("999999"),
                ),
                SalReturnItem(
                    tenant_id=1,
                    return_id=ret.id,
                    goods_id=gid,
                    qty=Decimal("10"),
                    price=Decimal("10"),
                    amount=Decimal("100"),
                ),
            ]
        )
        await session.commit()
        return {
            "cid": cid,
            "gid": gid,
            "wid": wid,
            "code": suffix,
            "out_no": out_no,
            "draft_no": draft_no,
            "ret_no": ret_no,
        }


async def _clean(seed: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        ids = (
            (
                await session.execute(
                    SalOutbound.__table__.select().where(SalOutbound.customer_id == seed["cid"])
                )
            )
            .scalars()
            .all()
        )
        for row in ids:
            await session.execute(delete(SalOutboundItem).where(SalOutboundItem.outbound_id == row))
        ret_ids = (
            (
                await session.execute(
                    SalReturn.__table__.select().where(SalReturn.customer_id == seed["cid"])
                )
            )
            .scalars()
            .all()
        )
        for row in ret_ids:
            await session.execute(delete(SalReturnItem).where(SalReturnItem.return_id == row))
        await session.execute(delete(SalReturn).where(SalReturn.customer_id == seed["cid"]))
        await session.execute(delete(SalOutbound).where(SalOutbound.customer_id == seed["cid"]))
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
        await session.execute(
            SalOrder.__table__.delete().where(SalOrder.customer_id == seed["cid"])
        )
        await session.execute(BdCustomer.__table__.delete().where(BdCustomer.id == seed["cid"]))
        await session.execute(BdGoods.__table__.delete().where(BdGoods.id == seed["gid"]))
        await session.execute(BdWarehouse.__table__.delete().where(BdWarehouse.id == seed["wid"]))
        await session.execute(BdUnit.__table__.delete().where(BdUnit.name == f"RPTU{seed['code']}"))
        await session.commit()


def test_sales_report_views_totals_and_filters() -> None:
    """四视图/合计/退货负数行/草稿排除/筛选/鉴权。"""
    seed = _run(_seed())
    try:

        async def scenario() -> None:
            async with _client() as client:
                headers = _auth(await _login(client))

                detail = await client.get(
                    f"{API}/reports/sales",
                    params={"view": "detail", "customerId": seed["cid"]},
                    headers=headers,
                )
                assert detail.status_code == 200, detail.text
                body = detail.json()["data"]
                assert body["view"] == "detail"
                assert body["total"] == 2, body  # 确认出库 1 行 + 确认退货 1 行；草稿不计
                docs = {item["docNo"] for item in body["items"]}
                assert seed["out_no"] in docs and seed["ret_no"] in docs
                assert seed["draft_no"] not in docs
                totals = body["totals"]
                assert Decimal(str(totals["outboundAmount"])) == Decimal("1000")
                assert Decimal(str(totals["returnAmount"])) == Decimal("100")
                assert Decimal(str(totals["netAmount"])) == Decimal("900")
                assert Decimal(str(totals["netQty"])) == Decimal("90")
                ret = next(item for item in body["items"] if item["type"] == "return")
                assert Decimal(str(ret["qty"])) == Decimal("-10")
                assert Decimal(str(ret["amount"])) == Decimal("-100")

                summary = await client.get(
                    f"{API}/reports/sales",
                    params={"view": "summary", "groupBy": "goods", "customerId": seed["cid"]},
                    headers=headers,
                )
                assert summary.status_code == 200, summary.text
                grouped = summary.json()["data"]["items"]
                assert len(grouped) == 1
                assert Decimal(str(grouped[0]["netAmount"])) == Decimal("900")

                for view in ("customer_rank", "goods_rank"):
                    ranked = await client.get(
                        f"{API}/reports/sales",
                        params={"view": view, "customerId": seed["cid"]},
                        headers=headers,
                    )
                    assert ranked.status_code == 200, ranked.text
                    items = ranked.json()["data"]["items"]
                    assert len(items) == 1
                    assert items[0]["rank"] == 1
                    assert Decimal(str(items[0]["netAmount"])) == Decimal("900")

                # 仓库筛选与日期区间
                by_warehouse = await client.get(
                    f"{API}/reports/sales",
                    params={"view": "detail", "warehouseId": seed["wid"]},
                    headers=headers,
                )
                assert by_warehouse.status_code == 200, by_warehouse.text
                assert by_warehouse.json()["data"]["total"] == 2
                out_of_range = await client.get(
                    f"{API}/reports/sales",
                    params={
                        "view": "detail",
                        "customerId": seed["cid"],
                        "startDate": (TODAY + timedelta(days=1)).isoformat(),
                    },
                    headers=headers,
                )
                assert out_of_range.status_code == 200, out_of_range.text
                assert out_of_range.json()["data"]["total"] == 0

                no_auth = await client.get(f"{API}/reports/sales")
                assert no_auth.status_code == 401, no_auth.status_code
                bad_view = await client.get(
                    f"{API}/reports/sales", params={"view": "bad"}, headers=headers
                )
                assert bad_view.status_code == 422, bad_view.text

        _run(scenario())
    finally:
        _run(_clean(seed))
