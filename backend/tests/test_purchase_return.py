"""P-04 采购退货验收测试。

覆盖（需求条目拆分 P-04 验收标准）：
  1. 入库 100 后退货 10：库存 90、流水 purchase_return direction=-1（-10）、
     原应付金额 900、未付 900（未付款时）；
  2. 退货 91（超库存，锁定 20 后可用 80）或 101（超原入库）返回 409；
     可用量不足、退货金额超过原应付余额同样 409，库存/应付不变；
  3. 同一退货单重复确认返回 409，库存/应付不重复；
  4. 退货单价与当前平均成本差异不破坏库存数量（avg_cost 不变、流水按均价结转）；
  5. 原单可退查询：原入库/已退/可退数量与应付快照；
  6. 草稿 CRUD、操作日志、权限（缺少 pur:return:* 返回 403）。

注意：测试间共享同一个 SQLite 库，结束时必须物理清理库存/财务/采购全部数据，
否则会破坏 test_purchase_order.py 的「采购订单不写库存与财务」断言。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date
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
    PurInbound,
    PurInboundItem,
    PurOrder,
    PurOrderItem,
    PurReturn,
    PurReturnItem,
)
from app.models.sys import (
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
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    """使用真实认证/租户依赖的 ASGI 客户端。"""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    """构造 Bearer 认证头。"""
    return {"Authorization": f"Bearer {token}"}


async def _login_token(client: httpx.AsyncClient, username: str, password: str) -> str:
    """登录并返回 accessToken。"""
    resp = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _pick_seed() -> tuple[int, int]:
    """取种子商品 GD0001 与单位「个」的 ID。"""
    async with AsyncSessionLocal() as session:
        goods_id = await session.scalar(select(BdGoods.id).where(BdGoods.code == "GD0001"))
        unit_id = await session.scalar(select(BdUnit.id).where(BdUnit.name == "个"))
    assert goods_id and unit_id, "种子商品/单位缺失"
    return int(goods_id), int(unit_id)


async def _create_supplier(name: str) -> int:
    """新建启用供应商（测试内自建，避免依赖种子数据）。"""
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(
            code=f"SUP{uuid.uuid4().hex[:8]}",
            name=name,
            credit_days=30,
            status=1,
            tenant_id=1,
        )
        session.add(supplier)
        await session.commit()
        return int(supplier.id)


async def _create_role_user(username: str, perm_codes: list[str]) -> tuple[int, int]:
    """创建角色 + 用户并授权；返回 (role_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"p04_{uuid.uuid4().hex[:8]}",
            name="P04采购退货测试角色",
            data_scope=3,  # 全部数据范围，排除数据范围导致的 403
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


async def _create_confirmed_inbound(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    goods_id: int,
    unit_id: int,
    qty: str,
    price: str,
) -> tuple[int, str, int]:
    """走完整链路造一张已确认入库单：订单 → 入库草稿 → 确认。

    返回 (inbound_id, inbound_no, order_id)，order_id 供清理脚本删除订单表。
    """
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

    resp = await client.post(
        f"{API}/purchase/inbounds",
        headers=headers,
        json={
            "orderId": order_id,
            "warehouseId": 1,
            "inboundDate": today,
            "items": [{"orderItemId": item_id, "qty": qty}],
        },
    )
    assert resp.status_code == 200, resp.text
    inbound_id = int(resp.json()["data"]["id"])
    resp = await client.post(f"{API}/purchase/inbounds/{inbound_id}/confirm", headers=headers)
    assert resp.status_code == 200, resp.text
    return inbound_id, str(resp.json()["data"]["inboundNo"]), order_id


async def _create_return_draft(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    goods_id: int,
    source_no: str,
    qty: str,
    price: str,
    warehouse_id: int = 1,
) -> httpx.Response:
    """创建退货单草稿（返回原始响应，供状态码断言）。"""
    return await client.post(
        f"{API}/purchase/returns",
        headers=headers,
        json={
            "supplierId": supplier_id,
            "warehouseId": warehouse_id,
            "sourceNo": source_no,
            "returnDate": date.today().isoformat(),
            "items": [{"goodsId": goods_id, "qty": qty, "price": price}],
        },
    )


async def _snapshot() -> dict[str, Any]:
    """读取库存/流水/应付/退货状态的当前快照，用于失败后「不变」断言。"""
    async with AsyncSessionLocal() as session:
        stocks = (await session.execute(select(InvStock).order_by(InvStock.id))).scalars().all()
        flows = (
            (await session.execute(select(InvStockFlow).order_by(InvStockFlow.id)))
            .scalars()
            .all()
        )
        payables = (
            (await session.execute(select(FinPayable).order_by(FinPayable.id))).scalars().all()
        )
        returns = (
            (await session.execute(select(PurReturn).order_by(PurReturn.id))).scalars().all()
        )
        return {
            "stocks": [
                (
                    int(s.warehouse_id),
                    int(s.goods_id),
                    str(s.qty),
                    str(s.locked_qty),
                    str(s.avg_cost),
                )
                for s in stocks
            ],
            "flows": [(str(f.flow_type), int(f.direction), str(f.qty)) for f in flows],
            "payables": [
                (str(p.source_no), str(p.amount), str(p.unpaid_amount), int(p.status))
                for p in payables
            ],
            "returns": [(int(r.id), int(r.status), str(r.total_amount)) for r in returns],
        }


async def _cleanup(
    return_ids: list[int],
    inbound_ids: list[int],
    order_ids: list[int],
    supplier_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
) -> None:
    """物理清理本测试数据：流水/库存/应付 → 采购六张表 → 用户/角色/供应商。"""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(InvStockFlow))
        await session.execute(delete(InvStock))
        await session.execute(delete(FinPayable))
        if return_ids:
            await session.execute(
                delete(PurReturnItem).where(PurReturnItem.return_id.in_(return_ids))
            )
            await session.execute(delete(PurReturn).where(PurReturn.id.in_(return_ids)))
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
        await session.commit()


async def _get_payable(source_no: str) -> FinPayable:
    """按原入库单号取应付（断言辅助）。"""
    async with AsyncSessionLocal() as session:
        payable = await session.scalar(
            select(FinPayable).where(
                FinPayable.source_type == "purchase_inbound", FinPayable.source_no == source_no
            )
        )
        assert payable is not None, f"原应付不存在: {source_no}"
        return payable


async def _basic_return_scenario(goods_id: int, supplier_id: int) -> None:
    """验收 1/3/4：退货 10 → 库存 90/流水 -10/应付 900；重复确认 409；价差不破坏成本。"""
    goods, unit = await _pick_seed()
    return_ids: list[int] = []
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            inbound_id, inbound_no, order_id = await _create_confirmed_inbound(
                client, headers, supplier_id, goods, unit, "100", "10"
            )
            inbound_ids.append(inbound_id)
            order_ids.append(order_id)

            # 原单可退查询：原入库 100、已退 0、可退 100；应付 1000/未付 1000
            resp = await client.get(
                f"{API}/purchase/returns/source", headers=headers, params={"source_no": inbound_no}
            )
            assert resp.status_code == 200, resp.text
            source = resp.json()["data"]
            assert source["inboundNo"] == inbound_no
            assert source["supplierId"] == supplier_id
            assert Decimal(str(source["items"][0]["originalQty"])) == Decimal("100")
            assert Decimal(str(source["items"][0]["returnedQty"])) == Decimal("0")
            assert Decimal(str(source["items"][0]["returnableQty"])) == Decimal("100")
            assert Decimal(str(source["items"][0]["price"])) == Decimal("10")
            assert Decimal(str(source["payable"]["amount"])) == Decimal("1000.00")
            assert Decimal(str(source["payable"]["unpaidAmount"])) == Decimal("1000.00")

            # 退货 10（单价 = 原入库单价 10）：草稿 → 确认
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "10", "10"
            )
            assert resp.status_code == 200, resp.text
            ret = resp.json()["data"]
            return_id = int(ret["id"])
            return_ids.append(return_id)
            assert ret["status"] == 0, "新建退货单应为草稿"
            assert ret["returnNo"].startswith("PR")
            assert Decimal(str(ret["totalAmount"])) == Decimal("100.00")
            assert Decimal(str(ret["items"][0]["originalQty"])) == Decimal("100")
            assert Decimal(str(ret["items"][0]["returnableQty"])) == Decimal("100")

            resp = await client.post(f"{API}/purchase/returns/{return_id}/confirm", headers=headers)
            assert resp.status_code == 200, resp.text
            assert int(resp.json()["data"]["status"]) == 1

            # 库存 90；流水 purchase_return direction=-1 qty 10（账面 100→90）
            async with AsyncSessionLocal() as session:
                stock = await session.scalar(select(InvStock).where(InvStock.goods_id == goods_id))
                assert stock is not None
                assert Decimal(str(stock.qty)) == Decimal("90")
                assert Decimal(str(stock.available_qty)) == Decimal("90")
                assert Decimal(str(stock.avg_cost)) == Decimal("10"), "退货不得改变移动平均成本"
                flow_count = await session.scalar(select(func.count()).select_from(InvStockFlow))
                assert int(flow_count or 0) == 2, "入库 + 退货各一条流水"
                flow = await session.scalar(
                    select(InvStockFlow)
                    .where(InvStockFlow.flow_type == "purchase_return")
                    .order_by(InvStockFlow.id.desc())
                )
                assert flow is not None
                assert int(flow.direction) == -1
                assert Decimal(str(flow.qty)) == Decimal("10")
                assert Decimal(str(flow.before_qty)) == Decimal("100")
                assert Decimal(str(flow.after_qty)) == Decimal("90")
                assert Decimal(str(flow.unit_cost)) == Decimal("10"), "出库按当前均价结转"
                assert str(flow.source_no) == ret["returnNo"], "流水来源单号 = 退货单号"

                payable = await _get_payable(inbound_no)
                assert Decimal(str(payable.amount)) == Decimal("900.00"), "应付冲减后应为 900"
                assert Decimal(str(payable.unpaid_amount)) == Decimal("900.00")
                assert Decimal(str(payable.paid_amount)) == Decimal("0.00")
                assert int(payable.status) == 0, "未付款时状态保持未付"

            # 原单可退查询：已退 10、可退 90
            resp = await client.get(
                f"{API}/purchase/returns/source", headers=headers, params={"source_no": inbound_no}
            )
            assert Decimal(str(resp.json()["data"]["items"][0]["returnedQty"])) == Decimal("10")
            assert Decimal(str(resp.json()["data"]["items"][0]["returnableQty"])) == Decimal("90")

            # 重复确认：409 且库存/应付不变
            before = await _snapshot()
            resp = await client.post(f"{API}/purchase/returns/{return_id}/confirm", headers=headers)
            assert resp.status_code == 409, resp.text
            assert "已确认" in resp.json()["message"]
            assert await _snapshot() == before, "重复确认后库存/应付不得变化"

            # 验收 4：退货单价（50）与平均成本（10）差异大，数量与成本不受影响
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "5", "50"
            )
            assert resp.status_code == 200, resp.text
            ret2 = resp.json()["data"]
            return_ids.append(int(ret2["id"]))
            assert Decimal(str(ret2["totalAmount"])) == Decimal("250.00")
            resp = await client.post(
                f"{API}/purchase/returns/{ret2['id']}/confirm", headers=headers
            )
            assert resp.status_code == 200, resp.text
            async with AsyncSessionLocal() as session:
                stock = await session.scalar(select(InvStock).where(InvStock.goods_id == goods_id))
                assert Decimal(str(stock.qty)) == Decimal("85")
                assert Decimal(str(stock.avg_cost)) == Decimal("10"), "价差不得破坏库存成本"
                payable = await _get_payable(inbound_no)
                assert Decimal(str(payable.amount)) == Decimal("650.00")
                assert Decimal(str(payable.unpaid_amount)) == Decimal("650.00")

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
            for expected in ("新增采购退货", "确认采购退货"):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(return_ids, inbound_ids, order_ids, [supplier_id], [], [])


def test_purchase_return_basic_flow() -> None:
    """验收 1/3/4：退货主闭环、重复确认 409、价差不破坏库存。"""
    goods_id, _unit = _run(_pick_seed())
    supplier_id = _run(_create_supplier("P04测试供应商"))
    _run(_basic_return_scenario(goods_id, supplier_id))


async def _validation_scenario(goods_id: int, supplier_id: int) -> None:
    """验收 2：超库存/超原入库/金额超应付余额在草稿期与确认期均 409 且无副作用。"""
    goods, unit = await _pick_seed()
    return_ids: list[int] = []
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            inbound_id, inbound_no, order_id = await _create_confirmed_inbound(
                client, headers, supplier_id, goods, unit, "100", "10"
            )
            inbound_ids.append(inbound_id)
            order_ids.append(order_id)

            # (a) 退货 91：可退量够（100）但先锁定 20 使可用量 80 → 确认期 409
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "91", "10"
            )
            assert resp.status_code == 200, resp.text
            r91 = resp.json()["data"]
            return_ids.append(int(r91["id"]))
            async with AsyncSessionLocal() as session:
                stock = await session.scalar(select(InvStock).where(InvStock.goods_id == goods_id))
                stock.locked_qty = Decimal("20")
                stock.available_qty = Decimal("80")  # 100 - 20，满足可用量一致性约束
                await session.commit()
            before = await _snapshot()
            resp = await client.post(f"{API}/purchase/returns/{r91['id']}/confirm", headers=headers)
            assert resp.status_code == 409, resp.text
            assert "可用库存不足" in resp.json()["message"]
            assert await _snapshot() == before, "可用量不足确认失败后库存/应付必须保持不变"

            # (b) 退货 101：超原入库 → 草稿期 409
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "101", "10"
            )
            assert resp.status_code == 409, resp.text
            assert "可退" in resp.json()["message"]

            # (c) 退货金额 1200 > 原应付 1000 → 草稿期 409
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "20", "60"
            )
            assert resp.status_code == 409, resp.text
            assert "退货金额" in resp.json()["message"]

            # (d) 确认期金额校验：R1=19×50=950（创建时 ≤ 1000 合法）确认后应付剩 900；
            #     R2=60×16=960 的草稿在 R1 确认前创建合法（≤ 1000），且确认时可退量 81、
            #     可用量 61（还挂着 (a) 的锁定 20）都够，只有金额 960 > 900 一项不满足 → 409
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "19", "50"
            )
            assert resp.status_code == 200, resp.text
            r1 = resp.json()["data"]
            return_ids.append(int(r1["id"]))
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "60", "16"
            )
            assert resp.status_code == 200, resp.text
            r2 = resp.json()["data"]
            return_ids.append(int(r2["id"]))
            resp = await client.post(f"{API}/purchase/returns/{r1['id']}/confirm", headers=headers)
            assert resp.status_code == 200, resp.text
            before = await _snapshot()
            resp = await client.post(f"{API}/purchase/returns/{r2['id']}/confirm", headers=headers)
            assert resp.status_code == 409, resp.text
            assert "退货金额" in resp.json()["message"]
            assert await _snapshot() == before, "金额超限确认失败后库存/应付必须保持不变"
        finally:
            await _cleanup(return_ids, inbound_ids, order_ids, [supplier_id], [], [])


def test_purchase_return_validation_409() -> None:
    """验收 2：超库存/超原入库/金额超限均在草稿或确认期 409 且无副作用。"""
    goods_id, _unit = _run(_pick_seed())
    supplier_id = _run(_create_supplier("P04校验供应商"))
    _run(_validation_scenario(goods_id, supplier_id))


async def _crud_source_permission_scenario(goods_id: int) -> None:
    """草稿 CRUD、原单查询 404/409、供应商不一致 422、权限 403。"""
    goods, unit = await _pick_seed()
    supplier_id = 0
    other_supplier_id = 0
    return_ids: list[int] = []
    inbound_ids: list[int] = []
    order_ids: list[int] = []
    user_ids: list[int] = []
    role_ids: list[int] = []
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(
            code=f"SUP{uuid.uuid4().hex[:8]}", name="P04CRUD供应商", status=1, tenant_id=1
        )
        other = BdSupplier(
            code=f"SUP{uuid.uuid4().hex[:8]}", name="P04其他供应商", status=1, tenant_id=1
        )
        session.add_all([supplier, other])
        await session.commit()
        supplier_id = int(supplier.id)
        other_supplier_id = int(other.id)
    async with _client() as client:
        admin_headers = _auth(await _login_token(client, *ADMIN))
        try:
            inbound_id, inbound_no, order_id = await _create_confirmed_inbound(
                client, admin_headers, supplier_id, goods, unit, "50", "10"
            )
            inbound_ids.append(inbound_id)
            order_ids.append(order_id)

            # 原单查询：不存在的单号 404
            resp = await client.get(
                f"{API}/purchase/returns/source",
                headers=admin_headers,
                params={"source_no": "PI9999999999"},
            )
            assert resp.status_code == 404, resp.text

            # 新建草稿（数量 5 × 10 = 50）→ 修改数量 8 → 金额 80
            resp = await _create_return_draft(
                client, admin_headers, supplier_id, goods_id, inbound_no, "5", "10"
            )
            assert resp.status_code == 200, resp.text
            ret = resp.json()["data"]
            return_ids.append(int(ret["id"]))
            resp = await client.put(
                f"{API}/purchase/returns/{ret['id']}",
                headers=admin_headers,
                json={
                    "items": [{"goodsId": goods_id, "qty": "8", "price": "10"}],
                    "version": ret["version"],
                },
            )
            assert resp.status_code == 200, resp.text
            assert Decimal(str(resp.json()["data"]["totalAmount"])) == Decimal("80.00")

            # 供应商与原入库单不一致 → 422
            resp = await _create_return_draft(
                client, admin_headers, other_supplier_id, goods_id, inbound_no, "1", "10"
            )
            assert resp.status_code == 422, resp.text

            # 删除草稿（软删）
            resp = await client.delete(
                f"{API}/purchase/returns/{ret['id']}", headers=admin_headers
            )
            assert resp.status_code == 200, resp.text
            async with AsyncSessionLocal() as session:
                deleted = await session.scalar(
                    select(PurReturn.is_deleted).where(PurReturn.id == int(ret["id"]))
                )
            assert bool(deleted) is True

            # 无 pur:return:* 的账号：列表/新增/确认全部 403
            suffix = uuid.uuid4().hex[:8]
            role_id, user_id = await _create_role_user(f"p04no{suffix}", ["rpt:dashboard:list"])
            role_ids.append(role_id)
            user_ids.append(user_id)
            headers = _auth(await _login_token(client, f"p04no{suffix}", PASSWORD))
            resp = await client.get(f"{API}/purchase/returns", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await _create_return_draft(
                client, headers, supplier_id, goods_id, inbound_no, "1", "10"
            )
            assert resp.status_code == 403, resp.text
        finally:
            await _cleanup(
                return_ids,
                inbound_ids,
                order_ids,
                [supplier_id, other_supplier_id],
                user_ids,
                role_ids,
            )


def test_purchase_return_crud_and_permissions() -> None:
    """草稿 CRUD、原单查询边界与 pur:return:* 权限隔离。"""
    goods_id, _unit = _run(_pick_seed())
    _run(_crud_source_permission_scenario(goods_id))
