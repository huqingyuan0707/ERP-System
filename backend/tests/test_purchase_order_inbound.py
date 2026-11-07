"""P-02 采购订单入库回写验收测试（Service 层，供 P-03 采购入库确认事务调用）。

覆盖验收清单第 4 条与数据闭环：
  - 已审订单入库 40 → 状态「部分入库」，再入库 60 → 状态「完成」；
  - 超剩余数量入库被拒绝（409）；
  - 关闭后不可再入库（409）；
  - 回写只改 pur_order_item.inbound_qty 与订单状态，不产生库存流水 / 应收应付，
    订单金额与税额保持不变。
"""

import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.core.exceptions import AppException, ErrorCode
from app.db.session import AsyncSessionLocal
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.sys import SysUser
from app.schemas.purchase_order import (
    PurOrderInboundApply,
    PurOrderInboundLine,
    PurOrderOut,
    PurOrderStatus,
)
from app.services.purchase_order import PurchaseOrderService
from tests.test_purchase_order import (
    ADMIN,
    API,
    _auth,
    _cleanup,
    _client,
    _create_dept,
    _login_token,
    _pick_seed,
    _run,
)


async def _create_approved_order(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    goods_id: int,
    unit_id: int,
    qty: str,
) -> tuple[int, int]:
    """建单行订单并走完提交/审核，返回 (订单 ID, 订单明细 ID)。"""
    resp = await client.post(
        f"{API}/purchase/orders",
        headers=headers,
        json={
            "supplierId": supplier_id,
            "orderDate": date.today().isoformat(),
            "items": [
                {
                    "goodsId": goods_id,
                    "unitId": unit_id,
                    "qty": qty,
                    "price": "10",
                    "taxRate": "0.13",
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = int(resp.json()["data"]["id"])
    resp = await client.post(f"{API}/purchase/orders/{order_id}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"{API}/purchase/orders/{order_id}/audit",
        headers=headers,
        json={"result": "approved"},
    )
    assert resp.status_code == 200, resp.text
    resp = await client.get(f"{API}/purchase/orders/{order_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    return order_id, int(items[0]["id"])


async def _apply_inbound(order_id: int, item_id: int, qty: str) -> PurOrderOut:
    """直接调用入库回写服务（模拟 P-03 入库确认事务内的调用）。"""
    async with AsyncSessionLocal() as session:
        user = await session.scalar(select(SysUser).where(SysUser.username == "admin"))
        assert user is not None, "测试库应有 admin 用户"
        service = PurchaseOrderService(session, 1)
        return await service.apply_inbound(
            order_id,
            PurOrderInboundApply(
                lines=[
                    PurOrderInboundLine(order_item_id=item_id, qty=Decimal(qty)),
                ]
            ),
            user,
            commit=True,
        )


async def _stock_finance_counts() -> tuple[int, ...]:
    """库存与财务四张表的行数快照，用于验证订单模块不越界写账。"""
    async with AsyncSessionLocal() as session:
        counts: list[int] = []
        for model in (InvStockFlow, InvStock, FinReceivable, FinPayable):
            value = await session.scalar(select(func.count()).select_from(model))
            counts.append(int(value or 0))
        return tuple(counts)


async def _inbound_scenario() -> None:
    """验收：40 → 部分入库 → 60 → 完成；超量拒绝；关闭后拒绝；不写库存财务。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02入库部{suffix}")
    goods_a, _goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []

    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            before = await _stock_finance_counts()
            order_id, item_id = await _create_approved_order(
                client, headers, supplier_id, goods_a, unit_id, "100"
            )
            order_ids.append(order_id)

            # 第一次入库 40 → 部分入库，进度 40%
            result = await _apply_inbound(order_id, item_id, "40")
            assert result.status == PurOrderStatus.PARTIAL_INBOUND
            assert Decimal(str(result.inbound_qty)) == Decimal("40")
            assert Decimal(str(result.inbound_progress)) == Decimal("40.00")
            assert Decimal(str(result.items[0].remaining_qty)) == Decimal("60")

            # 第二次入库 60 → 完成，进度 100%
            result = await _apply_inbound(order_id, item_id, "60")
            assert result.status == PurOrderStatus.COMPLETED
            assert Decimal(str(result.inbound_qty)) == Decimal("100")
            assert Decimal(str(result.inbound_progress)) == Decimal("100.00")
            assert Decimal(str(result.items[0].remaining_qty)) == Decimal("0")

            # 已完成订单不可再入库
            with pytest.raises(AppException) as exc_info:
                await _apply_inbound(order_id, item_id, "1")
            assert exc_info.value.code == ErrorCode.CONFLICT

            # 列表页进度：HTTP 出参同步反映
            resp = await client.get(f"{API}/purchase/orders", headers=headers)
            assert resp.status_code == 200, resp.text
            row = next(r for r in resp.json()["data"]["items"] if r["id"] == order_id)
            assert row["status"] == PurOrderStatus.COMPLETED
            assert Decimal(str(row["inboundProgress"])) == Decimal("100.00")

            # 超剩余数量入库被拒绝
            order2_id, item2_id = await _create_approved_order(
                client, headers, supplier_id, goods_a, unit_id, "10"
            )
            order_ids.append(order2_id)
            await _apply_inbound(order2_id, item2_id, "4")
            with pytest.raises(AppException) as exc_info:
                await _apply_inbound(order2_id, item2_id, "7")
            assert exc_info.value.code == ErrorCode.CONFLICT
            assert "超过剩余" in exc_info.value.message
            resp = await client.get(f"{API}/purchase/orders/{order2_id}", headers=headers)
            assert Decimal(str(resp.json()["data"]["items"][0]["inboundQty"])) == Decimal("4")

            # 关闭后不可再入库
            order3_id, item3_id = await _create_approved_order(
                client, headers, supplier_id, goods_a, unit_id, "10"
            )
            order_ids.append(order3_id)
            resp = await client.get(f"{API}/purchase/orders/{order3_id}", headers=headers)
            version = int(resp.json()["data"]["version"])
            resp = await client.post(
                f"{API}/purchase/orders/{order3_id}/close",
                headers=headers,
                json={"reason": "取消采购", "version": version},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == PurOrderStatus.CLOSED
            with pytest.raises(AppException) as exc_info:
                await _apply_inbound(order3_id, item3_id, "1")
            assert exc_info.value.code == ErrorCode.CONFLICT

            # 回写不改金额与税额，也不写库存 / 财务
            resp = await client.get(f"{API}/purchase/orders/{order_id}", headers=headers)
            detail = resp.json()["data"]
            assert Decimal(str(detail["totalAmount"])) == Decimal("1000.00")
            assert Decimal(str(detail["taxAmount"])) == Decimal("115.04")
            assert await _stock_finance_counts() == before, "订单模块不应写库存或财务"
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_inbound_writeback() -> None:
    """验收 4 与数据闭环：入库回写、状态重算、超量/关闭拒绝、不写库存财务。"""
    _run(_inbound_scenario())
