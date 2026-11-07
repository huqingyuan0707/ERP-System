"""P-03 采购入库验收测试。

覆盖（需求条目拆分 P-03 验收标准）：
  1. 订单 100，入库 40：库存 +40、流水 1 条（purchase_in, direction=1）、
     订单「部分入库」、应付 400；
  2. 再入库 60：库存 100、订单「完成」、应付累计 1000；
  3. 入库 61（超剩余）返回 409，库存/订单/应付不变；
  4. 同一入库单重复确认返回 409，库存/流水/应付不重复；
  5. 模拟应付写入失败，库存与订单回滚；应付到期日 = 入库日 + 供应商 credit_days(30)；
  6. 草稿 CRUD：修改重算金额、删除软删、预填只读；权限：缺少 pur:inbound:* 返回 403。

注意：测试间共享同一个 SQLite 库，结束时必须物理清理库存/财务/采购全部数据，
否则会破坏 test_purchase_order.py 的「采购订单不写库存与财务」断言。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, func, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdGoods, BdSupplier, BdUnit
from app.models.finance import FinPayable
from app.models.inventory import InvStock, InvStockFlow
from app.models.pur import (
    PurApply,
    PurApplyItem,
    PurInbound,
    PurInboundItem,
    PurOrder,
    PurOrderItem,
)
from app.models.sys import (
    SysDept,
    SysOperLog,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)


API = "/api/v1"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """在同步测试里跑异步协程（与既有测试写法一致）。"""
    return asyncio.run(coro)


@asynccontextmanager
async def _client(*, raise_app_exceptions: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    """使用真实认证/租户依赖的 ASGI 客户端。

    raise_app_exceptions=False 时未处理异常按全局处理器返回 500 结构，
    供「模拟应付写入失败」场景断言 500 响应。
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    """构造 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    """登录并返回 accessToken。"""
    resp = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _create_role_user(
    username: str, perm_codes: list[str], data_scope: int
) -> tuple[int, int]:
    """创建角色 + 用户并授权；返回 (role_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"p03_{uuid.uuid4().hex[:8]}",
            name="P03采购入库测试角色",
            data_scope=data_scope,
            status=1,
            tenant_id=1,
        )
        session.add(role)
        await session.flush()
        user = SysUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            dept_id=None,
            status=1,
            tenant_id=1,
        )
        session.add(user)
        await session.flush()
        session.add(SysUserRole(user_id=user.id, role_id=role.id))
        for code in perm_codes:
            perm_id = await session.scalar(
                select(SysPermission.id).where(SysPermission.perm_code == code)
            )
            assert perm_id is not None, f"权限码不存在: {code}"
            session.add(SysRolePermission(role_id=role.id, permission_id=perm_id))
        await session.commit()
        return int(role.id), int(user.id)


async def _create_supplier() -> int:
    """新建启用供应商（credit_days=30，供应付到期日断言）。"""
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(
            code=f"SUP{uuid.uuid4().hex[:8]}",
            name="P03测试供应商",
            credit_days=30,
            status=1,
            tenant_id=1,
        )
        session.add(supplier)
        await session.commit()
        return int(supplier.id)


async def _create_approved_order(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    goods_id: int,
    unit_id: int,
    qty: str,
    price: str,
) -> tuple[int, int]:
    """建一张 1 行采购订单并提交、审核通过；返回 (order_id, order_item_id)。"""
    today = date.today().isoformat()
    resp = await client.post(
        f"{API}/purchase/orders",
        headers=headers,
        json={
            "supplierId": supplier_id,
            "orderDate": today,
            "items": [{"goodsId": goods_id, "unitId": unit_id, "qty": qty, "price": price}],
        },
    )
    assert resp.status_code == 200, resp.text
    order_id = int(resp.json()["data"]["id"])
    resp = await client.post(f"{API}/purchase/orders/{order_id}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"{API}/purchase/orders/{order_id}/audit", headers=headers, json={"result": "approved"}
    )
    assert resp.status_code == 200, resp.text
    item_id = int(resp.json()["data"]["items"][0]["id"])
    return order_id, item_id


async def _snapshot() -> dict[str, Any]:
    """读取库存/流水/应付/订单状态的当前快照，用于失败后「不变」断言。"""
    async with AsyncSessionLocal() as session:
        stocks = (
            (await session.execute(select(InvStock).order_by(InvStock.id))).scalars().all()
        )
        flows = (
            (await session.execute(select(InvStockFlow).order_by(InvStockFlow.id)))
            .scalars()
            .all()
        )
        payables = (
            (await session.execute(select(FinPayable).order_by(FinPayable.id))).scalars().all()
        )
        orders = (
            (
                await session.execute(
                    select(PurOrder.id, PurOrder.status, PurOrderItem.inbound_qty)
                    .select_from(PurOrder)
                    .join(PurOrderItem, PurOrderItem.order_id == PurOrder.id)
                    .order_by(PurOrder.id, PurOrderItem.id)
                )
            )
            .all()
        )
        return {
            "stocks": [
                (int(s.warehouse_id), int(s.goods_id), str(s.qty), str(s.avg_cost))
                for s in stocks
            ],
            "flows": [(str(f.flow_type), int(f.direction), str(f.qty)) for f in flows],
            "payables": [(str(p.source_no), str(p.amount), str(p.unpaid_amount)) for p in payables],
            "orders": [(int(r[0]), int(r[1]), str(r[2])) for r in orders],
        }


async def _cleanup(
    inbound_ids: list[int],
    order_ids: list[int],
    apply_ids: list[int],
    supplier_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
    dept_ids: list[int],
) -> None:
    """物理清理本测试数据：流水/库存/应付 → 采购四张表 → 用户/角色/供应商/部门。"""
    async with AsyncSessionLocal() as session:
        # 库存与流水（流水无外键，直接删；库存行按 warehouse+goods 全量清）
        await session.execute(delete(InvStockFlow))
        await session.execute(delete(InvStock))
        # 应付（退货与入库共用）
        await session.execute(delete(FinPayable))
        if inbound_ids:
            await session.execute(
                delete(PurInboundItem).where(PurInboundItem.inbound_id.in_(inbound_ids))
            )
            await session.execute(delete(PurInbound).where(PurInbound.id.in_(inbound_ids)))
        if order_ids:
            await session.execute(
                delete(PurOrderItem).where(PurOrderItem.order_id.in_(order_ids))
            )
            await session.execute(delete(PurOrder).where(PurOrder.id.in_(order_ids)))
        if apply_ids:
            await session.execute(
                delete(PurApplyItem).where(PurApplyItem.apply_id.in_(apply_ids))
            )
            await session.execute(delete(PurApply).where(PurApply.id.in_(apply_ids)))
        if user_ids:
            await session.execute(delete(SysUserRole).where(SysUserRole.user_id.in_(user_ids)))
            await session.execute(delete(SysUser).where(SysUser.id.in_(user_ids)))
        if role_ids:
            await session.execute(
                delete(SysRolePermission).where(SysRolePermission.role_id.in_(role_ids))
            )
            await session.execute(delete(SysRole).where(SysRole.id.in_(role_ids)))
        if supplier_ids:
            await session.execute(delete(BdSupplier).where(BdSupplier.id.in_(supplier_ids)))
        if dept_ids:
            await session.execute(delete(SysDept).where(SysDept.id.in_(dept_ids)))
        await session.commit()


async def _stock_row(session: Any, goods_id: int) -> InvStock:
    """取指定商品的库存行（断言辅助）。"""
    return await session.scalar(select(InvStock).where(InvStock.goods_id == goods_id))


async def _partial_inbound_scenario(goods_id: int, unit_id: int) -> None:
    """验收 1/2：入库 40 部分入库、再入库 60 完成；库存/流水/应付三账一致。"""
    supplier_id = await _create_supplier()
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    apply_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            order_id, _item_id = await _create_approved_order(
                client, headers, supplier_id, goods_id, unit_id, "100", "10"
            )
            order_ids.append(order_id)

            # 预填：带出未入库明细与剩余数量
            resp = await client.get(
                f"{API}/purchase/inbounds/from-order/{order_id}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            prefill = resp.json()["data"]
            assert prefill["orderId"] == order_id
            assert prefill["supplierId"] == supplier_id
            assert len(prefill["items"]) == 1
            assert Decimal(str(prefill["items"][0]["remainingQty"])) == Decimal("100")
            assert Decimal(str(prefill["items"][0]["price"])) == Decimal("10")

            # 第一次入库 40：草稿 → 确认
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": _item_id, "qty": "40"}],
                },
            )
            assert resp.status_code == 200, resp.text
            inbound = resp.json()["data"]
            inbound_id = int(inbound["id"])
            inbound_ids.append(inbound_id)
            assert inbound["status"] == 0, "新建入库单应为草稿"
            assert inbound["inboundNo"].startswith("PI")
            assert Decimal(str(inbound["totalAmount"])) == Decimal("400.00")
            assert Decimal(str(inbound["taxAmount"])) == Decimal("0.00")
            assert Decimal(str(inbound["items"][0]["price"])) == Decimal("10"), (
                "入库单价必须取订单明细单价"
            )

            resp = await client.post(
                f"{API}/purchase/inbounds/{inbound_id}/confirm", headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert int(resp.json()["data"]["status"]) == 1

            # 库存 +40；流水 1 条 purchase_in direction=1；订单「部分入库」；应付 400
            async with AsyncSessionLocal() as session:
                stock = await _stock_row(session, goods_id)
                assert stock is not None
                assert Decimal(str(stock.qty)) == Decimal("40")
                assert Decimal(str(stock.available_qty)) == Decimal("40")
                assert Decimal(str(stock.avg_cost)) == Decimal("10")
                flow_count = await session.scalar(
                    select(func.count()).select_from(InvStockFlow)
                )
                flow = await session.scalar(select(InvStockFlow))
                assert int(flow_count or 0) == 1
                assert str(flow.flow_type) == "purchase_in"
                assert int(flow.direction) == 1
                assert Decimal(str(flow.qty)) == Decimal("40")
                order_status = await session.scalar(
                    select(PurOrder.status).where(PurOrder.id == order_id)
                )
                assert int(order_status) == 3, "入库 40/100 后订单应为部分入库"
                payable = await session.scalar(select(FinPayable))
                assert payable is not None
                assert str(payable.source_type) == "purchase_inbound"
                assert str(payable.source_no) == inbound["inboundNo"]
                assert Decimal(str(payable.amount)) == Decimal("400.00")
                assert Decimal(str(payable.unpaid_amount)) == Decimal("400.00")
                assert int(payable.status) == 0
                # 到期日 = 入库日 + credit_days(30)
                assert payable.due_date == date.fromisoformat(inbound["inboundDate"]) + timedelta(
                    days=30
                )

            # 第二次入库 60：订单完成、应付累计 1000
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": _item_id, "qty": "60"}],
                },
            )
            assert resp.status_code == 200, resp.text
            inbound2 = resp.json()["data"]
            inbound_ids.append(int(inbound2["id"]))
            resp = await client.post(
                f"{API}/purchase/inbounds/{inbound2['id']}/confirm", headers=headers
            )
            assert resp.status_code == 200, resp.text

            async with AsyncSessionLocal() as session:
                stock = await _stock_row(session, goods_id)
                assert Decimal(str(stock.qty)) == Decimal("100")
                assert Decimal(str(stock.avg_cost)) == Decimal("10")
                flow_count = await session.scalar(
                    select(func.count()).select_from(InvStockFlow)
                )
                assert int(flow_count or 0) == 2
                order_status = await session.scalar(
                    select(PurOrder.status).where(PurOrder.id == order_id)
                )
                assert int(order_status) == 4, "入库 100/100 后订单应完成"
                payable_count = await session.scalar(select(func.count()).select_from(FinPayable))
                assert int(payable_count or 0) == 2, "每次入库各生成一条应付"
                total_payable = await session.scalar(select(func.sum(FinPayable.amount)))
                assert Decimal(str(total_payable)) == Decimal("1000.00")

            # 操作日志
            async with AsyncSessionLocal() as session:
                actions = set(
                    (
                        await session.execute(
                            select(SysOperLog.action).where(
                                SysOperLog.module == "采购管理", SysOperLog.status == 1
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for expected in ("新增采购入库", "确认采购入库", "采购订单入库回写"):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(
                inbound_ids, order_ids, apply_ids, [supplier_id], [], [], []
            )


def test_purchase_inbound_partial_and_complete() -> None:
    """验收 1/2：部分入库 → 完成，库存/流水/订单/应付三账一致。"""
    goods_id, unit_id = _run(_pick_goods_and_unit())
    _run(_partial_inbound_scenario(goods_id, unit_id))


async def _pick_goods_and_unit() -> tuple[int, int]:
    """取种子商品 GD0001 与单位「个」的 ID。"""
    async with AsyncSessionLocal() as session:
        goods_id = await session.scalar(select(BdGoods.id).where(BdGoods.code == "GD0001"))
        unit_id = await session.scalar(select(BdUnit.id).where(BdUnit.name == "个"))
    assert goods_id and unit_id, "种子商品/单位缺失"
    return int(goods_id), int(unit_id)


async def _over_remaining_scenario(goods_id: int, unit_id: int) -> None:
    """验收 3：入库 61（超剩余）在草稿期与确认期都返回 409，库存/订单/应付不变。

    场景：订单 200 → 入库 40（剩余 160）→
      - 草稿期：直接创建 161 的入库单 → 409（161 > 剩余 160）；
      - 确认期：创建两份草稿 C=100、D=61（创建时剩余 160，均合法），
        先确认 C（剩余 60，订单仍部分入库）再确认 D → 61 > 60 → 409，
        覆盖「草稿合法但确认时剩余不足」的并发窗口。
    """
    supplier_id = await _create_supplier()
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            order_id, item_id = await _create_approved_order(
                client, headers, supplier_id, goods_id, unit_id, "200", "10"
            )
            order_ids.append(order_id)

            async def _create(qty: str) -> int | None:
                resp = await client.post(
                    f"{API}/purchase/inbounds",
                    headers=headers,
                    json={
                        "orderId": order_id,
                        "warehouseId": 1,
                        "inboundDate": date.today().isoformat(),
                        "items": [{"orderItemId": item_id, "qty": qty}],
                    },
                )
                return int(resp.json()["data"]["id"]) if resp.status_code == 200 else None

            # 先入库 40：剩余 160
            first_id = await _create("40")
            assert first_id is not None
            inbound_ids.append(first_id)
            resp = await client.post(f"{API}/purchase/inbounds/{first_id}/confirm", headers=headers)
            assert resp.status_code == 200, resp.text

            # 草稿期拦截：161 > 剩余 160
            before = await _snapshot()
            over_resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "161"}],
                },
            )
            assert over_resp.status_code == 409, over_resp.text
            assert "剩余" in over_resp.json()["message"]
            assert await _snapshot() == before, "草稿期拒绝后不得产生任何写入"

            # 两份合法草稿：C=100、D=61（创建时剩余 160，均合法）
            c_id = await _create("100")
            d_id = await _create("61")
            assert c_id is not None and d_id is not None
            inbound_ids.extend([c_id, d_id])

            # 确认 C 后剩余 60、订单仍部分入库；再确认 D 应 409（确认期校验）
            resp = await client.post(f"{API}/purchase/inbounds/{c_id}/confirm", headers=headers)
            assert resp.status_code == 200, resp.text
            before = await _snapshot()
            resp = await client.post(f"{API}/purchase/inbounds/{d_id}/confirm", headers=headers)
            assert resp.status_code == 409, resp.text
            assert "剩余" in resp.json()["message"]
            assert await _snapshot() == before, "确认期拒绝后库存/订单/应付必须保持不变"
        finally:
            await _cleanup(inbound_ids, order_ids, [], [supplier_id], [], [], [])


def test_purchase_inbound_over_remaining() -> None:
    """验收 3：超剩余数量在草稿与确认两个环节都返回 409 且无副作用。"""
    goods_id, unit_id = _run(_pick_goods_and_unit())
    _run(_over_remaining_scenario(goods_id, unit_id))


async def _duplicate_confirm_scenario(goods_id: int, unit_id: int) -> None:
    """验收 4：同一入库单重复确认返回 409，库存/流水/应付不重复。"""
    supplier_id = await _create_supplier()
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            order_id, item_id = await _create_approved_order(
                client, headers, supplier_id, goods_id, unit_id, "50", "10"
            )
            order_ids.append(order_id)
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "50"}],
                },
            )
            assert resp.status_code == 200, resp.text
            inbound_id = int(resp.json()["data"]["id"])
            inbound_ids.append(inbound_id)
            resp = await client.post(
                f"{API}/purchase/inbounds/{inbound_id}/confirm", headers=headers
            )
            assert resp.status_code == 200, resp.text

            before = await _snapshot()
            resp = await client.post(
                f"{API}/purchase/inbounds/{inbound_id}/confirm", headers=headers
            )
            assert resp.status_code == 409, resp.text
            assert "已确认" in resp.json()["message"]
            after = await _snapshot()
            assert before == after, "重复确认后库存/流水/应付不得变化"
        finally:
            await _cleanup(inbound_ids, order_ids, [], [supplier_id], [], [], [])


def test_purchase_inbound_duplicate_confirm() -> None:
    """验收 4：重复确认 409，库存/流水/应付不重复。"""
    goods_id, unit_id = _run(_pick_goods_and_unit())
    _run(_duplicate_confirm_scenario(goods_id, unit_id))


async def _payable_rollback_scenario(goods_id: int, unit_id: int) -> None:
    """验收 5：模拟应付写入失败，库存与订单回滚。"""
    from app.services import purchase_inbound as inbound_module

    supplier_id = await _create_supplier()
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    async with _client(raise_app_exceptions=False) as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            order_id, item_id = await _create_approved_order(
                client, headers, supplier_id, goods_id, unit_id, "10", "10"
            )
            order_ids.append(order_id)
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "10"}],
                },
            )
            assert resp.status_code == 200, resp.text
            inbound_id = int(resp.json()["data"]["id"])
            inbound_ids.append(inbound_id)

            # monkeypatch _generate_payable 抛异常，模拟应付写入失败
            original = inbound_module.PurchaseInboundService._generate_payable

            async def _boom(self: Any, _inbound: Any) -> None:
                raise RuntimeError("模拟应付写入失败")

            inbound_module.PurchaseInboundService._generate_payable = _boom
            try:
                resp = await client.post(
                    f"{API}/purchase/inbounds/{inbound_id}/confirm", headers=headers
                )
            finally:
                inbound_module.PurchaseInboundService._generate_payable = original
            assert resp.status_code == 500, resp.text

            # 库存/流水/订单/应付全部回滚：库存行不存在、订单仍已审
            async with AsyncSessionLocal() as session:
                stock = await _stock_row(session, goods_id)
                assert stock is None, "应付失败后库存行必须回滚"
                flow_count = await session.scalar(
                    select(func.count()).select_from(InvStockFlow)
                )
                assert int(flow_count or 0) == 0, "应付失败后流水必须回滚"
                payable_count = await session.scalar(select(func.count()).select_from(FinPayable))
                assert int(payable_count or 0) == 0, "应付失败后不得留下应付记录"
                order = await session.scalar(select(PurOrder).where(PurOrder.id == order_id))
                assert int(order.status) == 2, "订单应保持已审状态"
                item = await session.scalar(
                    select(PurOrderItem).where(PurOrderItem.order_id == order_id)
                )
                assert Decimal(str(item.inbound_qty)) == Decimal("0"), "累计入库必须回滚"
                inbound = await session.scalar(
                    select(PurInbound).where(PurInbound.id == inbound_id)
                )
                assert int(inbound.status) == 0, "入库单应保持草稿状态，可修复后重新确认"
        finally:
            await _cleanup(inbound_ids, order_ids, [], [supplier_id], [], [], [])


def test_purchase_inbound_payable_failure_rollback() -> None:
    """验收 5：应付写入失败时库存/流水/订单/入库单整体回滚。"""
    goods_id, unit_id = _run(_pick_goods_and_unit())
    _run(_payable_rollback_scenario(goods_id, unit_id))


async def _crud_and_permission_scenario(goods_id: int, unit_id: int) -> None:
    """草稿 CRUD + 权限：修改重算金额、删除软删、缺少 pur:inbound:* 返回 403。"""
    supplier_id = await _create_supplier()
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    user_ids: list[int] = []
    role_ids: list[int] = []
    async with _client() as client:
        admin_headers = _auth(await _login_token(client, *ADMIN))
        try:
            order_id, item_id = await _create_approved_order(
                client, admin_headers, supplier_id, goods_id, unit_id, "20", "5"
            )
            order_ids.append(order_id)

            # 新增草稿：金额 100
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=admin_headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "20"}],
                },
            )
            assert resp.status_code == 200, resp.text
            inbound = resp.json()["data"]
            inbound_id = int(inbound["id"])
            inbound_ids.append(inbound_id)
            assert Decimal(str(inbound["totalAmount"])) == Decimal("100.00")

            # 修改草稿：数量 10 → 金额 50（单价仍取订单）
            resp = await client.put(
                f"{API}/purchase/inbounds/{inbound_id}",
                headers=admin_headers,
                json={
                    "items": [{"orderItemId": item_id, "qty": "10"}],
                    "version": inbound["version"],
                },
            )
            assert resp.status_code == 200, resp.text
            assert Decimal(str(resp.json()["data"]["totalAmount"])) == Decimal("50.00")

            # 删除草稿：软删（ID 保留在 inbound_ids 里，软删明细行仍被清理脚本引用）
            resp = await client.delete(
                f"{API}/purchase/inbounds/{inbound_id}", headers=admin_headers
            )
            assert resp.status_code == 200, resp.text
            async with AsyncSessionLocal() as session:
                deleted = await session.scalar(
                    select(PurInbound.is_deleted).where(PurInbound.id == inbound_id)
                )
            assert bool(deleted) is True

            # 已确认入库单不可改/删（409）
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=admin_headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "5"}],
                },
            )
            assert resp.status_code == 200, resp.text
            confirmed_id = int(resp.json()["data"]["id"])
            inbound_ids.append(confirmed_id)
            resp = await client.post(
                f"{API}/purchase/inbounds/{confirmed_id}/confirm", headers=admin_headers
            )
            assert resp.status_code == 200, resp.text
            resp = await client.put(
                f"{API}/purchase/inbounds/{confirmed_id}",
                headers=admin_headers,
                json={"remark": "x"},
            )
            assert resp.status_code == 409, "已确认入库单不允许修改"
            resp = await client.delete(
                f"{API}/purchase/inbounds/{confirmed_id}", headers=admin_headers
            )
            assert resp.status_code == 409, "已确认入库单不允许删除"

            # 无 pur:inbound:* 的账号：列表/新增/确认全部 403
            suffix = uuid.uuid4().hex[:8]
            role_id, user_id = await _create_role_user(
                f"p03no{suffix}", ["rpt:dashboard:list"], data_scope=1
            )
            role_ids.append(role_id)
            user_ids.append(user_id)
            headers = _auth(await _login_token(client, f"p03no{suffix}", PASSWORD))
            resp = await client.get(f"{API}/purchase/inbounds", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/inbounds",
                headers=headers,
                json={
                    "orderId": order_id,
                    "warehouseId": 1,
                    "inboundDate": date.today().isoformat(),
                    "items": [{"orderItemId": item_id, "qty": "1"}],
                },
            )
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/inbounds/{confirmed_id}/confirm", headers=headers
            )
            assert resp.status_code == 403, resp.text
        finally:
            await _cleanup(
                inbound_ids, order_ids, [], [supplier_id], user_ids, role_ids, []
            )


def test_purchase_inbound_crud_and_permissions() -> None:
    """草稿 CRUD、状态机与 pur:inbound:* 权限隔离。"""
    goods_id, unit_id = _run(_pick_goods_and_unit())
    _run(_crud_and_permission_scenario(goods_id, unit_id))
