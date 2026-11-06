"""P-02 采购订单验收测试。

覆盖：
  1. 转单预填：已审申请单可预填（单价取 cost_price、applyItemId 溯源）；草稿 409；
     预填不落库、不改申请单状态；
  2. 字段溯源：带 source_apply_id / apply_item_id 建单后正确落库；手工建单两字段为 NULL；
  3. 草稿 CRUD：新建为草稿、金额后端重算；修改草稿明细替换并重算；删除为软删且明细同步软删；
  4. 校验：数量 <= 0 / 重复商品 422；
  5. 不写库存与财务；操作日志完整；
  6. 权限：缺少 pur:order:* 返回 403；删除申请单需 pur:apply:delete（与 edit 分离）。
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
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.pur import PurApply, PurApplyItem, PurOrder, PurOrderItem
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


async def _pick_seed() -> tuple[int, int, int, int]:
    """取两个商品、一个单位，并新建一个供应商；返回 (goods_a, goods_b, unit_id, supplier_id)。"""
    async with AsyncSessionLocal() as session:
        goods_a = await session.scalar(select(BdGoods.id).where(BdGoods.code == "GD0001"))
        goods_b = await session.scalar(select(BdGoods.id).where(BdGoods.code == "GD0002"))
        unit_id = await session.scalar(select(BdUnit.id).where(BdUnit.name == "个"))
        assert goods_a and goods_b and unit_id
        # 种子数据未包含供应商，测试内自建，避免依赖种子
        supplier = BdSupplier(
            code=f"SUP{uuid.uuid4().hex[:8]}", name="P02测试供应商", status=1, tenant_id=1
        )
        session.add(supplier)
        await session.commit()
        return int(goods_a), int(goods_b), int(unit_id), int(supplier.id)


async def _create_dept(name: str) -> int:
    """新建部门，供申请单使用。"""
    async with AsyncSessionLocal() as session:
        dept = SysDept(tenant_id=1, name=name, parent_id=None, sort=1, status=1)
        session.add(dept)
        await session.commit()
        return int(dept.id)


async def _create_role_user(
    username: str, dept_id: int | None, perm_codes: list[str], data_scope: int
) -> tuple[int, int]:
    """创建角色 + 用户并授权；返回 (role_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"p02_{uuid.uuid4().hex[:8]}",
            name="P02采购订单测试角色",
            data_scope=data_scope,
            status=1,
            tenant_id=1,
        )
        session.add(role)
        await session.flush()
        user = SysUser(
            username=username,
            password_hash=hash_password(PASSWORD),
            dept_id=dept_id,
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


async def _create_approved_apply(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    dept_id: int,
    goods_ids: tuple[int, int],
) -> tuple[int, list[int]]:
    """建一张 2 行申请并提交、审核通过；返回 (apply_id, 明细 ID 列表)。"""
    today = date.today().isoformat()
    resp = await client.post(
        f"{API}/purchase/applies",
        headers=headers,
        json={
            "deptId": dept_id,
            "needDate": today,
            "items": [
                {"goodsId": goods_ids[0], "qty": "10", "needDate": today},
                {"goodsId": goods_ids[1], "qty": "2.5", "needDate": today},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    apply_id = int(resp.json()["data"]["id"])
    resp = await client.post(f"{API}/purchase/applies/{apply_id}/submit", headers=headers)
    assert resp.status_code == 200, resp.text
    resp = await client.post(
        f"{API}/purchase/applies/{apply_id}/audit",
        headers=headers,
        json={"result": "approved"},
    )
    assert resp.status_code == 200, resp.text
    item_ids = [int(item["id"]) for item in resp.json()["data"]["items"]]
    return apply_id, item_ids


async def _cleanup(
    order_ids: list[int],
    apply_ids: list[int],
    dept_ids: list[int],
    supplier_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
) -> None:
    """物理清理本测试数据：按 明细→主表→用户/角色→部门/供应商 顺序删除。"""
    async with AsyncSessionLocal() as session:
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
        if dept_ids:
            await session.execute(delete(SysDept).where(SysDept.id.in_(dept_ids)))
        if supplier_ids:
            await session.execute(delete(BdSupplier).where(BdSupplier.id.in_(supplier_ids)))
        await session.commit()


async def _assert_no_stock_or_finance() -> None:
    """采购订单不应写库存与财务：四张表在流程后必须为空。"""
    async with AsyncSessionLocal() as session:
        for model in (InvStockFlow, InvStock, FinReceivable, FinPayable):
            count = await session.scalar(select(func.count()).select_from(model))
            assert int(count or 0) == 0, f"采购订单不应写入 {model.__tablename__}"



async def _from_apply_scenario() -> None:
    """验收 1：转单预填正确，且不落库、不改申请单状态。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02部门{suffix}")
    goods_a, goods_b, _unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            apply_id, item_ids = await _create_approved_apply(
                client, headers, dept_id, (goods_a, goods_b)
            )
            apply_ids.append(apply_id)

            resp = await client.get(
                f"{API}/purchase/orders/from-apply/{apply_id}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()["data"]
            assert data["sourceApplyId"] == apply_id
            assert len(data["items"]) == 2
            # 预填单价取商品成本价
            async with AsyncSessionLocal() as session:
                cost_price = await session.scalar(
                    select(BdGoods.cost_price).where(BdGoods.id == goods_a)
                )
            assert Decimal(str(data["items"][0]["price"])) == Decimal(str(cost_price))
            # 行级溯源 ID 与申请明细一致
            assert [int(i["applyItemId"]) for i in data["items"]] == item_ids

            # 只读：不落库、不改申请单状态
            async with AsyncSessionLocal() as session:
                order_count = await session.scalar(select(func.count()).select_from(PurOrder))
                status = await session.scalar(
                    select(PurApply.status).where(PurApply.id == apply_id)
                )
            assert int(order_count or 0) == 0, "预填不应生成订单"
            assert int(status) == 2, "预填不应改变申请单状态"

            # 草稿申请单不可转单（409）
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=headers,
                json={
                    "deptId": dept_id,
                    "needDate": date.today().isoformat(),
                    "items": [{"goodsId": goods_a, "qty": "1"}],
                },
            )
            assert resp.status_code == 200, resp.text
            draft_id = int(resp.json()["data"]["id"])
            apply_ids.append(draft_id)
            resp = await client.get(
                f"{API}/purchase/orders/from-apply/{draft_id}", headers=headers
            )
            assert resp.status_code == 409, resp.text
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_from_apply() -> None:
    """验收 1：转单预填校验与只读性。"""
    _run(_from_apply_scenario())



async def _crud_scenario() -> None:
    """验收 2/3/4：溯源落库、金额重算、草稿 CRUD、入参校验。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02订单部{suffix}")
    goods_a, goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []
    today = date.today().isoformat()
    async with _client() as client:
        headers = _auth(await _login_token(client, *ADMIN))
        try:
            apply_id, item_ids = await _create_approved_apply(
                client, headers, dept_id, (goods_a, goods_b)
            )
            apply_ids.append(apply_id)

            # 带着溯源字段建单：单据级 + 行级溯源
            resp = await client.post(
                f"{API}/purchase/orders",
                headers=headers,
                json={
                    "supplierId": supplier_id,
                    "orderDate": today,
                    "sourceApplyId": apply_id,
                    "items": [
                        {
                            "goodsId": goods_a,
                            "unitId": unit_id,
                            "qty": "2",
                            "price": "100",
                            "taxRate": "0.13",
                            "applyItemId": item_ids[0],
                        },
                        {
                            "goodsId": goods_b,
                            "unitId": unit_id,
                            "qty": "1",
                            "price": "50",
                            "taxRate": "0",
                            "applyItemId": item_ids[1],
                        },
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            order = resp.json()["data"]
            order_id = int(order["id"])
            order_ids.append(order_id)
            assert order["status"] == 0, "新建订单应为草稿"
            assert order["orderNo"].startswith("PO")
            assert order["sourceApplyId"] == apply_id
            assert order["sourceApplyNo"]
            # 金额后端重算：200.00 + 50.00 = 250.00
            assert Decimal(str(order["totalAmount"])) == Decimal("250.00")
            # 税额：200 - 200/1.13 = 23.01；第二行税率 0
            assert Decimal(str(order["taxAmount"])) == Decimal("23.01")
            async with AsyncSessionLocal() as session:
                saved_apply = await session.scalar(
                    select(PurOrder.source_apply_id).where(PurOrder.id == order_id)
                )
                saved_item = await session.scalar(
                    select(PurOrderItem.apply_item_id)
                    .where(PurOrderItem.order_id == order_id)
                    .order_by(PurOrderItem.id)
                )
            assert int(saved_apply) == apply_id
            assert int(saved_item) == item_ids[0]


            # 手工建单：两个溯源字段为空
            resp = await client.post(
                f"{API}/purchase/orders",
                headers=headers,
                json={
                    "supplierId": supplier_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods_a, "unitId": unit_id, "qty": "1", "price": "10"}
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            manual = resp.json()["data"]
            order_ids.append(int(manual["id"]))
            assert manual["sourceApplyId"] is None
            assert manual["items"][0]["applyItemId"] is None

            # 修改草稿：明细替换后金额重算为 3 * 20 = 60.00
            resp = await client.put(
                f"{API}/purchase/orders/{order_id}",
                headers=headers,
                json={
                    "remark": "P02 修改草稿",
                    "items": [
                        {
                            "goodsId": goods_a,
                            "unitId": unit_id,
                            "qty": "3",
                            "price": "20",
                            "taxRate": "0",
                        }
                    ]
                },
            )
            assert resp.status_code == 200, resp.text
            assert Decimal(str(resp.json()["data"]["totalAmount"])) == Decimal("60.00")
            assert len(resp.json()["data"]["items"]) == 1

            # 校验：数量 <= 0、同一商品重复
            resp = await client.post(
                f"{API}/purchase/orders",
                headers=headers,
                json={
                    "supplierId": supplier_id,
                    "orderDate": today,
                    "items": [{"goodsId": goods_a, "unitId": unit_id, "qty": "0", "price": "1"}],
                },
            )
            assert resp.status_code == 422, "数量必须大于 0"
            resp = await client.post(
                f"{API}/purchase/orders",
                headers=headers,
                json={
                    "supplierId": supplier_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods_a, "unitId": unit_id, "qty": "1", "price": "1"},
                        {"goodsId": goods_a, "unitId": unit_id, "qty": "2", "price": "1"},
                    ],
                },
            )
            assert resp.status_code == 422, "同一商品不得重复录入"

            # 删除草稿：主表与明细同时软删
            resp = await client.delete(f"{API}/purchase/orders/{order_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            async with AsyncSessionLocal() as session:
                deleted = await session.scalar(
                    select(PurOrder.is_deleted).where(PurOrder.id == order_id)
                )
                active_items = await session.scalar(
                    select(func.count())
                    .select_from(PurOrderItem)
                    .where(
                        PurOrderItem.order_id == order_id,
                        PurOrderItem.is_deleted.is_(False),
                    )
                )
            assert bool(deleted) is True
            assert int(active_items or 0) == 0


            # 不写库存与财务
            await _assert_no_stock_or_finance()

            # 操作日志：新增/修改/删除
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
            for expected in ("新增采购订单", "修改采购订单", "删除采购订单"):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_crud() -> None:
    """验收 2/3/4：溯源落库、金额重算、草稿 CRUD、校验与日志。"""
    _run(_crud_scenario())


async def _permission_scenario() -> None:
    """验收 6：缺少 pur:order:* 返回 403；删除申请单需 pur:apply:delete，与 edit 分离。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P02权限部{suffix}")
    goods_a, _goods_b, unit_id, supplier_id = await _pick_seed()
    order_ids: list[int] = []
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    supplier_ids = [supplier_id]
    user_ids: list[int] = []
    role_ids: list[int] = []
    today = date.today().isoformat()
    async with _client() as client:
        admin_headers = _auth(await _login_token(client, *ADMIN))
        try:
            # 无 pur:order:* 的账号：列表/新增/转单预填全部 403
            username_no_order = f"p02nord{suffix}"
            role_id, user_id = await _create_role_user(
                username=username_no_order,
                dept_id=dept_id,
                perm_codes=["rpt:dashboard:list"],
                data_scope=1,
            )
            role_ids.append(role_id)
            user_ids.append(user_id)
            headers = _auth(await _login_token(client, username_no_order, PASSWORD))
            resp = await client.get(f"{API}/purchase/orders", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/orders",
                headers=headers,
                json={
                    "supplierId": supplier_id,
                    "orderDate": today,
                    "items": [{"goodsId": goods_a, "unitId": unit_id, "qty": "1", "price": "1"}],
                },
            )
            assert resp.status_code == 403, resp.text
            resp = await client.get(f"{API}/purchase/orders/from-apply/1", headers=headers)
            assert resp.status_code == 403, resp.text

            # 有 pur:apply:edit 但无 pur:apply:delete：改草稿 200，删草稿 403
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=admin_headers,
                json={
                    "deptId": dept_id,
                    "needDate": today,
                    "items": [{"goodsId": goods_a, "qty": "1"}],
                },
            )
            assert resp.status_code == 200, resp.text
            apply_id = int(resp.json()["data"]["id"])
            apply_ids.append(apply_id)

            username_edit_only = f"p02edit{suffix}"
            role_id, user_id = await _create_role_user(
                username=username_edit_only,
                dept_id=dept_id,
                perm_codes=[
                    "pur:apply:list",
                    "pur:apply:add",
                    "pur:apply:edit",
                    "rpt:dashboard:list",
                ],
                data_scope=3,  # 全部，排除数据范围导致的 403
            )
            role_ids.append(role_id)
            user_ids.append(user_id)
            edit_headers = _auth(await _login_token(client, username_edit_only, PASSWORD))
            resp = await client.put(
                f"{API}/purchase/applies/{apply_id}",
                headers=edit_headers,
                json={"remark": "edit 权限可改"},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.delete(f"{API}/purchase/applies/{apply_id}", headers=edit_headers)
            assert resp.status_code == 403, "无 pur:apply:delete 不应能删除草稿"
        finally:
            await _cleanup(order_ids, apply_ids, dept_ids, supplier_ids, user_ids, role_ids)


def test_purchase_order_permissions() -> None:
    """验收 6：pur:order:* 403；pur:apply:delete 与 pur:apply:edit 权限分离。"""
    _run(_permission_scenario())

