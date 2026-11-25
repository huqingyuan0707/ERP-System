"""SA-03 销售出库验收测试：草稿锁库存、确认扣库存+订单回写+应收生成。

覆盖 E2E-2 第 3 步：已审订单 100 → 出库 40 → 库存 -40、订单部分出库、应收 400。
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
from app.models.sal import SalOrder, SalOrderItem, SalOutbound
from app.schemas.sal_order import SalOrderStatus

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
            tenant_id=1, code=f"SA03C{suffix}", name="SA03测试客户", status=1, credit_days=30
        )
        goods = BdGoods(tenant_id=1, code=f"SA03G{suffix}", name="SA03测试商品", status=1)
        warehouse = BdWarehouse(tenant_id=1, code=f"SA03W{suffix}", name="SA03测试仓库", status=1)
        unit = BdUnit(tenant_id=1, name=f"SA03U{suffix}")
        session.add_all([customer, goods, warehouse, unit])
        await session.flush()
        ids = {
            "customer": int(customer.id),
            "goods": int(goods.id),
            "warehouse": int(warehouse.id),
            "unit": int(unit.id),
        }

        # 充足库存：100，均价 10
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
            order_no=f"SO-SA03-{suffix}",
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


async def _stock_snapshot(goods_id: int, warehouse_id: int) -> dict[str, Decimal]:
    async with AsyncSessionLocal() as session:
        stock = await session.scalar(
            select(InvStock).where(
                InvStock.goods_id == goods_id, InvStock.warehouse_id == warehouse_id
            )
        )
        flows = (
            (
                await session.execute(
                    select(InvStockFlow).where(
                        InvStockFlow.goods_id == goods_id,
                        InvStockFlow.flow_type == "sale_out",
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "qty": Decimal(str(stock.qty)) if stock else Decimal("0"),
            "locked": Decimal(str(stock.locked_qty)) if stock else Decimal("0"),
            "available": Decimal(str(stock.available_qty)) if stock else Decimal("0"),
            "sale_out_flows": Decimal(str(len(flows))),
        }


async def _clean(ids: dict[str, int]) -> None:
    async with AsyncSessionLocal() as session:
        nos = (
            (
                await session.execute(
                    select(SalOutbound.outbound_no).where(SalOutbound.order_id == ids["order"])
                )
            )
            .scalars()
            .all()
        )
        for no in nos:
            await session.execute(delete(InvStockFlow).where(InvStockFlow.source_no == no))
        await session.execute(delete(FinReceivable).where(FinReceivable.source_no.in_(nos)))
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


def test_sal_outbound_draft_locks_and_confirm_writes_all_ledgers() -> None:
    """草稿锁 40；确认后库存 -40、订单部分出库、应收 400；重复确认 409。"""

    async def scenario() -> None:
        ids = await _seed()
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                today = date.today().isoformat()

                pref = await client.get(
                    f"{API}/sales/outbounds/from-order/{ids['order']}", headers=headers
                )
                assert pref.status_code == 200, pref.text
                assert pref.json()["data"]["items"][0]["remainingQty"] == "100.0000"

                payload = {
                    "orderId": ids["order"],
                    "warehouseId": ids["warehouse"],
                    "outboundDate": today,
                    "items": [{"orderItemId": ids["order_item"], "qty": "40"}],
                }
                created = await client.post(f"{API}/sales/outbounds", json=payload, headers=headers)
                assert created.status_code == 200, created.text
                outbound = created.json()["data"]
                oid = int(outbound["id"])
                assert outbound["status"] == 0
                locked = await _stock_snapshot(ids["goods"], ids["warehouse"])
                assert locked["qty"] == Decimal("100")
                assert locked["locked"] == Decimal("40")
                assert locked["available"] == Decimal("60")

                confirmed = await client.post(
                    f"{API}/sales/outbounds/{oid}/confirm", headers=headers
                )
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["data"]["status"] == 1

                after = await _stock_snapshot(ids["goods"], ids["warehouse"])
                assert after["qty"] == Decimal("60")
                assert after["locked"] == Decimal("0")
                assert after["available"] == Decimal("60")
                assert after["sale_out_flows"] == 1

                async with AsyncSessionLocal() as session:
                    item = await session.scalar(
                        select(SalOrderItem).where(SalOrderItem.id == ids["order_item"])
                    )
                    assert Decimal(str(item.outbound_qty)) == Decimal("40")
                    order = await session.scalar(
                        select(SalOrder).where(SalOrder.id == ids["order"])
                    )
                    assert order.status == SalOrderStatus.PARTIAL_OUT

                    receivable = await session.scalar(
                        select(FinReceivable).where(
                            FinReceivable.source_no == outbound["outboundNo"]
                        )
                    )
                    assert receivable is not None
                    assert Decimal(str(receivable.amount)) == Decimal("400")
                    assert receivable.due_date == date.today() + timedelta(days=30)

                dup = await client.post(f"{API}/sales/outbounds/{oid}/confirm", headers=headers)
                assert dup.status_code == 409, dup.text
        finally:
            await _clean(ids)

    _run(scenario())
