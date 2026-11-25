"""SA-04 销售退货验收：按原出库单退货、回补库存、冲减应收。

覆盖 E2E-2 第 4 步：已出库 40 → 退货 10 → 库存 +10（60→70）、应收 400→300、
累计可退量校验、重复确认 409。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer, BdGoods, BdUnit, BdWarehouse
from app.models.finance import FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.sal import SalOrder, SalOrderItem, SalOutbound, SalReturn
from app.schemas.sal_order import SalOrderStatus
from app.schemas.sal_outbound import SalOutboundStatus

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


async def _seed() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        suffix = uuid.uuid4().hex[:6]
        customer = BdCustomer(
            tenant_id=1, code=f"SA04C{suffix}", name="SA04测试客户", status=1, credit_days=30
        )
        goods = BdGoods(tenant_id=1, code=f"SA04G{suffix}", name="SA04测试商品", status=1)
        warehouse = BdWarehouse(tenant_id=1, code=f"SA04W{suffix}", name="SA04测试仓库", status=1)
        unit = BdUnit(tenant_id=1, name=f"SA04U{suffix}")
        session.add_all([customer, goods, warehouse, unit])
        await session.flush()
        ids = {
            "customer": int(customer.id),
            "goods": int(goods.id),
            "warehouse": int(warehouse.id),
            "unit": int(unit.id),
        }
        session.add(
            InvStock(
                tenant_id=1,
                warehouse_id=ids["warehouse"],
                goods_id=ids["goods"],
                qty=Decimal("100"),
                locked_qty=Decimal("0"),
                available_qty=Decimal("100"),
                avg_cost=Decimal("10"),
                created_by=1,
            )
        )
        order = SalOrder(
            tenant_id=1,
            customer_id=ids["customer"],
            order_no=f"SO-SA04-{suffix}",
            order_date=date.today() - timedelta(days=2),
            status=SalOrderStatus.APPROVED,
            total_amount=Decimal("1000"),
            tax_amount=Decimal("0"),
            discount=Decimal("0"),
            audit_by=1,
            audit_at=date.today(),
            created_by=1,
        )
        session.add(order)
        await session.flush()
        item = SalOrderItem(
            tenant_id=1,
            order_id=order.id,
            goods_id=ids["goods"],
            unit_id=ids["unit"],
            qty=Decimal("100"),
            price=Decimal("10"),
            amount=Decimal("1000"),
            tax_rate=Decimal("0"),
            outbound_qty=Decimal("0"),
            created_by=1,
        )
        session.add(item)
        await session.flush()
        ids["order"] = int(order.id)
        ids["order_item"] = int(item.id)
        await session.commit()
        return ids


async def _clean(ids: dict[str, int]) -> None:
    async with AsyncSessionLocal() as session:
        out_nos = (
            (
                await session.execute(
                    select(SalOutbound.outbound_no).where(SalOutbound.order_id == ids["order"])
                )
            )
            .scalars()
            .all()
        )
        ret_nos = (
            (
                await session.execute(
                    select(SalReturn.return_no).where(SalReturn.customer_id == ids["customer"])
                )
            )
            .scalars()
            .all()
        )
        all_nos = list(out_nos) + list(ret_nos)
        for no in all_nos:
            await session.execute(delete(InvStockFlow).where(InvStockFlow.source_no == no))
        await session.execute(delete(FinReceivable).where(FinReceivable.source_no.in_(out_nos)))
        await session.execute(delete(SalReturn).where(SalReturn.customer_id == ids["customer"]))
        await session.execute(delete(SalOutbound).where(SalOutbound.order_id == ids["order"]))
        await session.execute(delete(SalOrderItem).where(SalOrderItem.order_id == ids["order"]))
        await session.execute(delete(SalOrder).where(SalOrder.id == ids["order"]))
        await session.execute(
            delete(InvStock).where(
                InvStock.goods_id == ids["goods"], InvStock.warehouse_id == ids["warehouse"]
            )
        )
        await session.execute(delete(BdGoods).where(BdGoods.id == ids["goods"]))
        await session.execute(delete(BdWarehouse).where(BdWarehouse.id == ids["warehouse"]))
        await session.execute(delete(BdUnit).where(BdUnit.id == ids["unit"]))
        await session.execute(delete(BdCustomer).where(BdCustomer.id == ids["customer"]))
        await session.commit()


def test_sal_return_replenishes_stock_and_reduces_receivable() -> None:
    """出库 40（应收 400）→ 退货 10 → 库存 70、应收 300；超退 409、重复确认 409。"""

    async def scenario() -> None:
        ids = await _seed()
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                today = date.today().isoformat()

                # 1) 建并确认出库 40
                out_resp = await client.post(
                    f"{API}/sales/outbounds",
                    headers=headers,
                    json={
                        "orderId": ids["order"],
                        "warehouseId": ids["warehouse"],
                        "outboundDate": today,
                        "items": [{"orderItemId": ids["order_item"], "qty": "40"}],
                    },
                )
                assert out_resp.status_code == 200, out_resp.text
                out = out_resp.json()["data"]
                out_no = out["outboundNo"]
                confirmed = await client.post(
                    f"{API}/sales/outbounds/{out['id']}/confirm", headers=headers
                )
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["data"]["status"] == SalOutboundStatus.CONFIRMED

                # 2) 原单可退明细：可退 40
                source = await client.get(f"{API}/sales/returns/source/{out_no}", headers=headers)
                assert source.status_code == 200, source.text
                src = source.json()["data"]
                assert src["items"][0]["returnableQty"] == "40.0000"

                # 3) 建并确认退货 10
                ret_resp = await client.post(
                    f"{API}/sales/returns",
                    headers=headers,
                    json={
                        "sourceNo": out_no,
                        "warehouseId": ids["warehouse"],
                        "returnDate": today,
                        "items": [{"goodsId": ids["goods"], "qty": "10"}],
                    },
                )
                assert ret_resp.status_code == 200, ret_resp.text
                ret = ret_resp.json()["data"]
                assert Decimal(str(ret["totalAmount"])) == Decimal("100")
                ret_confirm = await client.post(
                    f"{API}/sales/returns/{ret['id']}/confirm", headers=headers
                )
                assert ret_confirm.status_code == 200, ret_confirm.text

                # 4) 库存回补 + 应收冲减
                async with AsyncSessionLocal() as session:
                    stock = await session.scalar(
                        select(InvStock).where(
                            InvStock.goods_id == ids["goods"],
                            InvStock.warehouse_id == ids["warehouse"],
                        )
                    )
                    assert Decimal(str(stock.qty)) == Decimal("70")
                    receivable = await session.scalar(
                        select(FinReceivable).where(FinReceivable.source_no == out_no)
                    )
                    assert Decimal(str(receivable.amount)) == Decimal("300")
                    assert Decimal(str(receivable.unreceived_amount)) == Decimal("300")

                # 5) 再退超量（>30 剩余可退）→ 409
                over = await client.post(
                    f"{API}/sales/returns",
                    headers=headers,
                    json={
                        "sourceNo": out_no,
                        "warehouseId": ids["warehouse"],
                        "returnDate": today,
                        "items": [{"goodsId": ids["goods"], "qty": "31"}],
                    },
                )
                assert over.status_code == 409, over.text

                # 6) 重复确认 → 409
                dup = await client.post(f"{API}/sales/returns/{ret['id']}/confirm", headers=headers)
                assert dup.status_code == 409, dup.text
        finally:
            await _clean(ids)

    _run(scenario())
