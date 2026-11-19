"""R-02 采购报表验收：三视图、合计、退货负数行、草稿排除与筛选。

对应 需求条目拆分.md:685-696。
"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdGoods, BdSupplier, BdUnit, BdWarehouse
from app.models.pur import (
    PurInbound,
    PurInboundItem,
    PurOrder,
    PurOrderItem,
    PurReturn,
    PurReturnItem,
)

API = "/api/v1"
IN_NO = "PI-RPT-TEST"
DRAFT_NO = "PI-RPT-DRAFT"
RET_NO = "PR-RPT-TEST"
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


async def _seed() -> tuple[int, int, int]:
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(tenant_id=1, code="RPTS", name="报表测试供应商", status=1)
        goods = BdGoods(tenant_id=1, code="RPTG", name="报表测试商品", status=1)
        warehouse = BdWarehouse(tenant_id=1, code="RPTW", name="报表测试仓库", status=1)
        unit = BdUnit(tenant_id=1, name="RPTU-报表测试单位")
        session.add_all([supplier, goods, warehouse, unit])
        await session.flush()
        ids = (int(supplier.id), int(goods.id), int(warehouse.id))
        supplier_id, goods_id, warehouse_id = ids

        order = PurOrder(
            tenant_id=1,
            supplier_id=supplier_id,
            order_no="PO-RPT-TEST",
            order_date=TODAY - timedelta(days=6),
            total_amount=Decimal("1000"),
            tax_amount=Decimal("0"),
            status=2,
            created_by=1,
        )
        session.add(order)
        await session.flush()
        order_item = PurOrderItem(
            tenant_id=1,
            order_id=order.id,
            goods_id=goods_id,
            unit_id=unit.id,
            qty=Decimal("100"),
            price=Decimal("10"),
            amount=Decimal("1000"),
            tax_rate=Decimal("0"),
        )
        session.add(order_item)
        await session.flush()

        inbound = PurInbound(
            tenant_id=1,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            order_id=order.id,
            inbound_no=IN_NO,
            inbound_date=TODAY - timedelta(days=5),
            total_amount=Decimal("1000"),
            tax_amount=Decimal("0"),
            status=1,
            created_by=1,
        )
        draft = PurInbound(
            tenant_id=1,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            order_id=order.id,
            inbound_no=DRAFT_NO,
            inbound_date=TODAY - timedelta(days=4),
            total_amount=Decimal("9999"),
            tax_amount=Decimal("0"),
            status=0,
            created_by=1,
        )
        session.add_all([inbound, draft])
        await session.flush()
        session.add_all(
            [
                PurInboundItem(
                    tenant_id=1,
                    inbound_id=inbound.id,
                    order_item_id=order_item.id,
                    goods_id=goods_id,
                    qty=Decimal("100"),
                    price=Decimal("10"),
                    amount=Decimal("1000"),
                ),
                PurInboundItem(
                    tenant_id=1,
                    inbound_id=draft.id,
                    order_item_id=order_item.id,
                    goods_id=goods_id,
                    qty=Decimal("999"),
                    price=Decimal("10"),
                    amount=Decimal("9999"),
                ),
            ]
        )

        ret = PurReturn(
            tenant_id=1,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            source_no=IN_NO,
            return_no=RET_NO,
            return_date=TODAY - timedelta(days=2),
            total_amount=Decimal("100"),
            status=1,
            created_by=1,
        )
        session.add(ret)
        await session.flush()
        session.add(
            PurReturnItem(
                tenant_id=1,
                return_id=ret.id,
                goods_id=goods_id,
                qty=Decimal("10"),
                price=Decimal("10"),
                amount=Decimal("100"),
            )
        )
        await session.commit()
        return ids


async def _clean() -> None:
    async with AsyncSessionLocal() as session:
        return_id = await session.scalar(select(PurReturn.id).where(PurReturn.return_no == RET_NO))
        if return_id is not None:
            await session.execute(delete(PurReturnItem).where(PurReturnItem.return_id == return_id))
        inbound_ids = (
            (
                await session.execute(
                    select(PurInbound.id).where(PurInbound.inbound_no.in_([IN_NO, DRAFT_NO]))
                )
            )
            .scalars()
            .all()
        )
        order_id = await session.scalar(
            select(PurOrder.id).where(PurOrder.order_no == "PO-RPT-TEST")
        )
        if inbound_ids:
            await session.execute(
                delete(PurInboundItem).where(PurInboundItem.inbound_id.in_(inbound_ids))
            )
        if order_id is not None:
            await session.execute(delete(PurOrderItem).where(PurOrderItem.order_id == order_id))
        await session.execute(delete(PurReturn).where(PurReturn.return_no == RET_NO))
        await session.execute(
            delete(PurInbound).where(PurInbound.inbound_no.in_([IN_NO, DRAFT_NO]))
        )
        await session.execute(delete(PurOrder).where(PurOrder.order_no == "PO-RPT-TEST"))
        await session.execute(delete(BdGoods).where(BdGoods.code == "RPTG"))
        await session.execute(delete(BdWarehouse).where(BdWarehouse.code == "RPTW"))
        await session.execute(delete(BdUnit).where(BdUnit.name == "RPTU-报表测试单位"))
        await session.execute(delete(BdSupplier).where(BdSupplier.code == "RPTS"))
        await session.commit()


def test_purchase_report_views_totals_and_draft_excluded() -> None:
    """明细=入库行+退货负数行；草稿不计；汇总/排行与合计口径一致。"""

    async def scenario() -> None:
        goods_id = (await _seed())[1]
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                listed = await client.get(
                    f"{API}/reports/purchase", params={"view": "detail"}, headers=headers
                )
                assert listed.status_code == 200, listed.text
                body = listed.json()["data"]
                assert body["view"] == "detail"
                docs = {row["docNo"] for row in body["items"]}
                assert IN_NO in docs and RET_NO in docs and DRAFT_NO not in docs, docs
                ret = next(row for row in body["items"] if row["docNo"] == RET_NO)
                assert Decimal(str(ret["qty"])) == Decimal("-10")
                assert Decimal(str(ret["amount"])) == Decimal("-100")
                totals = body["totals"]
                assert Decimal(str(totals["inboundQty"])) == Decimal("100")
                assert Decimal(str(totals["returnQty"])) == Decimal("10")
                assert Decimal(str(totals["netQty"])) == Decimal("90")
                assert Decimal(str(totals["netAmount"])) == Decimal("900")

                summary = await client.get(
                    f"{API}/reports/purchase",
                    params={"view": "summary", "groupBy": "goods"},
                    headers=headers,
                )
                assert summary.status_code == 200, summary.text
                rows = summary.json()["data"]["items"]
                assert len(rows) == 1
                assert Decimal(str(rows[0]["netQty"])) == Decimal("90")

                rank = await client.get(
                    f"{API}/reports/purchase", params={"view": "rank"}, headers=headers
                )
                assert rank.status_code == 200, rank.text
                assert rank.json()["data"]["items"][0]["rank"] == 1

                filtered = await client.get(
                    f"{API}/reports/purchase",
                    params={"view": "detail", "goodsId": goods_id, "startDate": TODAY.isoformat()},
                    headers=headers,
                )
                assert filtered.status_code == 200, filtered.text
                assert filtered.json()["data"]["totals"]["rowCount"] == 0
        finally:
            await _clean()

    _run(scenario())
