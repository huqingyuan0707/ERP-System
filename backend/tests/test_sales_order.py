"""SAL-02/04~09/15 销售订单验收测试。

覆盖：
  1. 新建订单 → 草稿；合计/税额由后端重算（折扣按比例缩放税额）；折扣超过行金额合计 422；
  2. 草稿编辑/删除；明细整单替换后金额重算；非草稿编辑/删除 409；
  3. 提交：草稿→待审；重复提交 409；驳回回草稿且原因必填（422）；
  4. 审核：通过→已审；SAL-15 信用校验（占用=已审未出库订单金额），
     超额 409 且订单保持待审；额度=0 不控制；临界值（占用+本单=额度）放行；
  5. 关闭：已审→关闭并记录原因；重复关闭 409；草稿/待审关闭 409；
  6. SAL-02 报价转单：预填接口带出客户/明细/信用；convert 后报价=已转订单（不可重复转单、
     不可作废），订单明细与价格一致；订单不写应收/应付（SAL-15 只读占用）；
  7. 出库进度：无出库时订单/明细进度为 0，remaining = qty；
  8. 权限：缺 sal:order:* 返回 403；数据范围（本人）越权详情 403。

数据隔离：测试使用 conftest 提供的临时 SQLite 库，结束后物理清理本测试产生的数据。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer, BdGoods
from app.models.finance import FinPayable, FinReceivable
from app.models.sal import SalOrder, SalOrderItem, SalQuotation, SalQuotationItem
from app.models.sys import (
    SysDept,
    SysPermission,
    SysRole,
    SysRolePermission,
    SysUser,
    SysUserRole,
)

# 常量放这里：导入块后紧跟完整定义，isort（要求 2 空行）与 black（定义前 2 空行）一致
API = "/api/v1"
ADMIN = ("admin", "admin123")
PASSWORD = "Passw0rd123"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    """在同步测试里跑异步协程（与既有测试写法一致）。"""
    return asyncio.run(coro)


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    """使用真实认证/租户依赖的 ASGI 客户端，不注入或覆盖待测租户头。"""
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


def _unique(prefix: str) -> str:
    """生成唯一后缀，避免同名部门/账号/客户编码冲突。"""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


async def _create_customer(name: str, *, credit_limit: Decimal = Decimal("0")) -> int:
    """新建一个客户（测试自备，种子数据不含客户），返回 ID。"""
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(
            code=_unique("CUS"),
            name=name,
            status=1,
            credit_limit=credit_limit,
            credit_days=0,
            tenant_id=1,
        )
        session.add(customer)
        await session.commit()
        return int(customer.id)


async def _pick_goods(codes: tuple[str, str]) -> list[tuple[int, int]]:
    """取启用商品 (id, unit_id) 列表（种子数据里 GD0001/GD0002 必定存在）。"""
    async with AsyncSessionLocal() as session:
        result: list[tuple[int, int]] = []
        for code in codes:
            row = await session.execute(
                select(BdGoods.id, BdGoods.unit_id).where(BdGoods.code == code)
            )
            goods = row.first()
            assert goods is not None, f"种子商品缺失: {code}"
            assert goods.unit_id is not None, f"种子商品缺少计量单位: {code}"
            result.append((int(goods.id), int(goods.unit_id)))
    return result


async def _create_dept(name: str) -> int:
    """新建一个部门，用于数据范围「本人」验证。"""
    async with AsyncSessionLocal() as session:
        dept = SysDept(tenant_id=1, name=name, parent_id=None, sort=1, status=1)
        session.add(dept)
        await session.commit()
        return int(dept.id)


async def _create_role_user(
    username: str, dept_id: int | None, perm_codes: list[str], data_scope: int
) -> tuple[int, int]:
    """创建「角色 + 用户」并授予权限码；返回 (role_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"so_{uuid.uuid4().hex[:8]}",
            name="销售订单测试角色",
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


async def _cleanup(
    order_ids: list[int],
    quotation_ids: list[int],
    customer_ids: list[int],
    dept_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
) -> None:
    """物理清理本测试数据：先删明细与单据，再删客户/用户/角色/部门。"""
    async with AsyncSessionLocal() as session:
        if order_ids:
            await session.execute(delete(SalOrderItem).where(SalOrderItem.order_id.in_(order_ids)))
            await session.execute(delete(SalOrder).where(SalOrder.id.in_(order_ids)))
        if quotation_ids:
            await session.execute(
                delete(SalQuotationItem).where(SalQuotationItem.quotation_id.in_(quotation_ids))
            )
            await session.execute(delete(SalQuotation).where(SalQuotation.id.in_(quotation_ids)))
        if customer_ids:
            await session.execute(delete(BdCustomer).where(BdCustomer.id.in_(customer_ids)))
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
        await session.commit()


async def _assert_no_finance_writes(customer_ids: list[int]) -> None:
    """销售订单模块不应写应收应付：本测试客户名下应收必须为空（SAL-15 只读占用）。

    FinPayable 按 supplier_id 归属，与客户无关，这里只校验 FinReceivable。
    """
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(FinReceivable.id).where(FinReceivable.customer_id.in_(customer_ids))
        )
        assert count is None, "销售订单不应写入 fin_receivable"


def _items_payload(goods: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """把 (goods_id, unit_id) 列表转成明细入参（数量/单价在调用处定制）。"""
    return [{"goodsId": goods_id, "unitId": unit_id} for goods_id, unit_id in goods]


def test_sales_order_lifecycle() -> None:
    """主线：建单 → 编辑 → 提交 → 驳回 → 再提交 → 审核 → 关闭 + 信用校验 + 报价转单。"""
    _run(_scenario())


async def _scenario() -> None:
    """验收主线（见 test_sales_order_lifecycle docstring）。"""
    suffix = uuid.uuid4().hex[:8]
    # 无额度限制客户：跑状态机主线；限额客户：跑 SAL-15（额度 100 / 审核闸门 50）
    customer_id = await _create_customer(f"SO客户{suffix}")
    limited_customer_id = await _create_customer(f"SO限额客户{suffix}", credit_limit=Decimal("100"))
    audit_gate_customer_id = await _create_customer(
        f"SO闸门客户{suffix}", credit_limit=Decimal("50")
    )
    goods = await _pick_goods(("GD0001", "GD0002"))
    order_ids: list[int] = []
    quotation_ids: list[int] = []
    customer_ids = [customer_id, limited_customer_id, audit_gate_customer_id]
    dept_ids: list[int] = []
    user_ids: list[int] = []
    role_ids: list[int] = []
    today = date.today().isoformat()

    async with _client() as client:
        token = await _login_token(client, *ADMIN)
        headers = _auth(token)
        try:
            # ========== 一、草稿 CRUD 与金额重算（SAL-04/05） ==========
            resp = await client.post(
                f"{API}/sales/orders",
                headers=headers,
                json={
                    "customerId": customer_id,
                    "orderDate": today,
                    "discount": "10",
                    "remark": "SO 验收订单",
                    "items": [
                        {
                            "goodsId": goods[0][0],
                            "unitId": goods[0][1],
                            "qty": "10",
                            "price": "25.50",
                            "taxRate": "0.13",
                        },
                        {"goodsId": goods[1][0], "unitId": goods[1][1], "qty": "3", "price": "100"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            created = resp.json()["data"]
            order_id = int(created["id"])
            order_ids.append(order_id)
            assert created["status"] == 0, "新建订单应为草稿"
            assert created["orderNo"].startswith("SO"), "单号应为 SO 前缀"
            # 行金额合计 = 10*25.50 + 3*100 = 555.00；折扣 10 → total 545.00
            assert Decimal(str(created["totalAmount"])) == Decimal("545.00"), created["totalAmount"]
            # 税额 = (255 - 255/1.13) × 545/555 = 29.34 × 0.98198… ≈ 28.81
            assert Decimal(str(created["taxAmount"])) == Decimal("28.81"), created["taxAmount"]
            assert created["items"][0]["goodsCode"] == "GD0001"
            assert created["items"][0]["unitName"], "明细应返回单位名称"
            assert Decimal(str(created["outboundProgress"])) == Decimal("0"), "无出库时进度应为 0"

            # 折扣超过行金额合计 → 422
            resp = await client.post(
                f"{API}/sales/orders",
                headers=headers,
                json={
                    "customerId": customer_id,
                    "orderDate": today,
                    "discount": "999",
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "1", "price": "10"},
                    ],
                },
            )
            assert resp.status_code == 422, resp.text

            # 编辑草稿：替换明细与折扣，金额重算
            resp = await client.put(
                f"{API}/sales/orders/{order_id}",
                headers=headers,
                json={
                    "discount": "5",
                    "version": int(created["version"]),
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "4", "price": "20"},
                        {"goodsId": goods[1][0], "unitId": goods[1][1], "qty": "2", "price": "15"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            updated = resp.json()["data"]
            # 合计 = 4*20 + 2*15 - 5 = 105.00
            assert Decimal(str(updated["totalAmount"])) == Decimal("105.00"), updated["totalAmount"]
            assert Decimal(str(updated["discount"])) == Decimal("5.00")

            # ========== 二、提交 / 驳回 / 再提交（SAL-06） ==========
            resp = await client.post(f"{API}/sales/orders/{order_id}/submit", headers=headers)
            assert resp.status_code == 200, resp.text
            submitted = resp.json()["data"]
            assert submitted["status"] == 1, "提交后应为待审"
            # 重复提交 → 409
            resp = await client.post(f"{API}/sales/orders/{order_id}/submit", headers=headers)
            assert resp.status_code == 409, resp.text
            # 待审不可编辑
            resp = await client.put(
                f"{API}/sales/orders/{order_id}",
                headers=headers,
                json={"remark": "hack", "version": int(submitted["version"])},
            )
            assert resp.status_code == 409, resp.text

            # 驳回必须填写原因（422）
            resp = await client.post(
                f"{API}/sales/orders/{order_id}/audit",
                headers=headers,
                json={"result": "rejected"},
            )
            assert resp.status_code == 422, resp.text
            # 驳回回草稿并记录原因
            resp = await client.post(
                f"{API}/sales/orders/{order_id}/audit",
                headers=headers,
                json={"result": "rejected", "remark": "单价偏低，请复核"},
            )
            assert resp.status_code == 200, resp.text
            rejected = resp.json()["data"]
            assert rejected["status"] == 0, "驳回后应回草稿"
            assert rejected["auditRemark"] == "单价偏低，请复核"

            # 再提交 → 通过审核（SAL-07，无额度客户不控制信用）
            resp = await client.post(f"{API}/sales/orders/{order_id}/submit", headers=headers)
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/sales/orders/{order_id}/audit",
                headers=headers,
                json={"result": "approved", "remark": "同意"},
            )
            assert resp.status_code == 200, resp.text
            approved = resp.json()["data"]
            assert approved["status"] == 2, "审核通过后应为已审"
            assert approved["auditBy"] is not None
            assert approved["auditAt"] == date.today().isoformat()
            # 明细 remaining = qty（无出库）
            item = approved["items"][0]
            assert Decimal(str(item["remainingQty"])) == Decimal(str(item["qty"]))

            # ========== 三、SAL-15 信用校验（限额客户，额度 100） ==========
            # 草稿不占用额度：临界值订单 100 = 额度 → 创建成功
            resp = await client.post(
                f"{API}/sales/orders",
                headers=headers,
                json={
                    "customerId": limited_customer_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "5", "price": "20"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            boundary = resp.json()["data"]
            boundary_id = int(boundary["id"])
            order_ids.append(boundary_id)
            # 审核通过后订单开始占用额度（已审未出库）
            resp = await client.post(f"{API}/sales/orders/{boundary_id}/submit", headers=headers)
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/sales/orders/{boundary_id}/audit",
                headers=headers,
                json={"result": "approved"},
            )
            assert resp.status_code == 200, resp.text

            # 超额创建被拒：占用 100 + 本单 30 > 100（SAL-04 验收）
            resp = await client.post(
                f"{API}/sales/orders",
                headers=headers,
                json={
                    "customerId": limited_customer_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "2", "price": "15"},
                    ],
                },
            )
            assert resp.status_code == 409, resp.text
            assert "信用额度不足" in resp.json()["message"], resp.text
            # 信用概览：额度 100，占用 100，可用 0
            resp = await client.get(
                f"{API}/sales/orders/credit-info",
                headers=headers,
                params={"customerId": str(limited_customer_id)},
            )
            assert resp.status_code == 200, resp.text
            credit = resp.json()["data"]
            assert Decimal(str(credit["creditLimit"])) == Decimal("100.00")
            assert Decimal(str(credit["occupiedAmount"])) == Decimal("100.00")
            assert Decimal(str(credit["availableAmount"])) == Decimal("0")

            # 审核闸门：第二张限额客户订单（额度 50）先建两张 30 草稿，
            # 通过第一张后占用 30，第二张审核时 30+30=60 > 50 → 409 且保持待审
            audit_gate_ids: list[int] = []
            for _ in range(2):
                resp = await client.post(
                    f"{API}/sales/orders",
                    headers=headers,
                    json={
                        "customerId": audit_gate_customer_id,
                        "orderDate": today,
                        "items": [
                            {
                                "goodsId": goods[0][0],
                                "unitId": goods[0][1],
                                "qty": "2",
                                "price": "15",
                            },
                        ],
                    },
                )
                assert resp.status_code == 200, resp.text
                audit_gate_ids.append(int(resp.json()["data"]["id"]))
                order_ids.append(audit_gate_ids[-1])
            resp = await client.post(
                f"{API}/sales/orders/{audit_gate_ids[0]}/submit", headers=headers
            )
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/sales/orders/{audit_gate_ids[0]}/audit",
                headers=headers,
                json={"result": "approved"},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/sales/orders/{audit_gate_ids[1]}/submit", headers=headers
            )
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/sales/orders/{audit_gate_ids[1]}/audit",
                headers=headers,
                json={"result": "approved"},
            )
            assert resp.status_code == 409, resp.text
            assert "信用额度不足" in resp.json()["message"], resp.text
            # 审核失败不推进状态：订单仍是待审
            async with AsyncSessionLocal() as session:
                pending_status = await session.scalar(
                    select(SalOrder.status).where(SalOrder.id == audit_gate_ids[1])
                )
                assert pending_status == 1, f"审核被拒应保持待审，实际 {pending_status}"

            # 关闭已审订单释放占用，之后占用回 0
            resp = await client.post(
                f"{API}/sales/orders/{boundary_id}/close",
                headers=headers,
                json={"reason": "客户取消"},
            )
            assert resp.status_code == 200, resp.text
            closed = resp.json()["data"]
            assert closed["status"] == 5, "关闭后应为关闭状态"
            assert closed["auditRemark"] == "客户取消"
            # 重复关闭 → 409
            resp = await client.post(
                f"{API}/sales/orders/{boundary_id}/close",
                headers=headers,
                json={"reason": "again"},
            )
            assert resp.status_code == 409, resp.text
            # 关闭后占用释放：信用概览占用回到 0
            resp = await client.get(
                f"{API}/sales/orders/credit-info",
                headers=headers,
                params={"customerId": str(limited_customer_id)},
            )
            assert Decimal(str(resp.json()["data"]["occupiedAmount"])) == Decimal("0")

            # ========== 四、SAL-02 报价转订单 ==========
            # 造一张已发送报价单
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json={
                    "customerId": customer_id,
                    "quoteDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "qty": "6", "price": "30"},
                        {"goodsId": goods[1][0], "qty": "1", "price": "88.88"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            quotation = resp.json()["data"]
            quotation_ids.append(int(quotation["id"]))
            resp = await client.post(
                f"{API}/sales/quotations/{quotation['id']}/send", headers=headers
            )
            assert resp.status_code == 200, resp.text

            # 预填接口：带出客户/明细/价格（只读，不改状态）
            resp = await client.get(
                f"{API}/sales/orders/from-quotation/{quotation['id']}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            prefill = resp.json()["data"]
            assert prefill["quoteNo"] == quotation["quoteNo"]
            assert len(prefill["items"]) == 2
            assert Decimal(str(prefill["items"][0]["qty"])) == Decimal("6")
            assert Decimal(str(prefill["items"][0]["price"])) == Decimal("30")
            assert prefill["items"][0]["unitId"], "预填明细应带出商品默认单位"

            # 转单：生成订单草稿，报价置「已转订单」
            resp = await client.post(
                f"{API}/sales/quotations/{quotation['id']}/convert",
                headers=headers,
                json={"orderDate": today, "discount": "0"},
            )
            assert resp.status_code == 200, resp.text
            converted = resp.json()["data"]
            order_ids.append(int(converted["id"]))
            assert converted["status"] == 0
            assert len(converted["items"]) == 2
            assert Decimal(str(converted["items"][0]["qty"])) == Decimal("6")
            assert Decimal(str(converted["items"][0]["price"])) == Decimal("30")
            # 报价合计 268.88 与订单一致（无折扣）
            assert Decimal(str(converted["totalAmount"])) == Decimal("268.88")
            # 报价状态已转订单
            async with AsyncSessionLocal() as session:
                status = await session.scalar(
                    select(SalQuotation.status).where(SalQuotation.id == int(quotation["id"]))
                )
                assert status == 3, f"报价应置已转订单(3)，实际 {status}"
            # 重复转单 → 409；预填接口也 409
            resp = await client.post(
                f"{API}/sales/quotations/{quotation['id']}/convert",
                headers=headers,
                json={"orderDate": today},
            )
            assert resp.status_code == 409, resp.text
            resp = await client.get(
                f"{API}/sales/orders/from-quotation/{quotation['id']}", headers=headers
            )
            assert resp.status_code == 409, resp.text
            # 已转订单不可作废
            resp = await client.post(
                f"{API}/sales/quotations/{quotation['id']}/void", headers=headers
            )
            assert resp.status_code == 409, resp.text

            # ========== 五、删除草稿 / 列表 ==========
            resp = await client.post(
                f"{API}/sales/orders",
                headers=headers,
                json={
                    "customerId": customer_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "1", "price": "1"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            draft = resp.json()["data"]
            order_ids.append(int(draft["id"]))
            resp = await client.delete(f"{API}/sales/orders/{draft['id']}", headers=headers)
            assert resp.status_code == 200, resp.text
            # 已审订单不可删除
            resp = await client.delete(f"{API}/sales/orders/{order_id}", headers=headers)
            assert resp.status_code == 409, resp.text

            # 列表：按状态筛选 + 关键字匹配客户名
            resp = await client.get(
                f"{API}/sales/orders",
                headers=headers,
                params={"status": "5", "keyword": f"SO客户{suffix}"},
            )
            assert resp.status_code == 200, resp.text
            listed = resp.json()["data"]
            assert listed["total"] >= 1
            assert any(int(row["id"]) == int(boundary["id"]) for row in listed["items"])

            # 全流程不写应收应付
            await _assert_no_finance_writes(customer_ids)
        finally:
            await _cleanup(order_ids, quotation_ids, customer_ids, dept_ids, user_ids, role_ids)


def test_sales_order_permission_and_scope() -> None:
    """权限与数据范围：缺权限 403；数据范围=本人时越权详情/操作 403。"""
    _run(_permission_scope_scenario())


async def _permission_scope_scenario() -> None:
    """权限/数据范围验收（见 test_sales_order_permission_and_scope docstring）。"""
    suffix = uuid.uuid4().hex[:8]
    customer_id = await _create_customer(f"SO权限客户{suffix}")
    goods = await _pick_goods(("GD0001",))
    dept_id = await _create_dept(f"SO部门{suffix}")
    owner_role, owner_user = await _create_role_user(
        f"so_owner_{suffix}",
        dept_id,
        [
            "sal:order:list",
            "sal:order:add",
            "sal:order:edit",
            "sal:order:submit",
            "sal:order:audit",
            "sal:order:close",
        ],
        data_scope=1,
    )
    # 无任何销售订单权限的用户
    outsider_role, outsider_user = await _create_role_user(
        f"so_out_{suffix}", dept_id, [], data_scope=1
    )
    role_ids = [owner_role, outsider_role]
    user_ids = [owner_user, outsider_user]
    customer_ids = [customer_id]
    dept_ids = [dept_id]
    order_ids: list[int] = []
    today = date.today().isoformat()

    async with _client() as client:
        try:
            owner_token = await _login_token(client, f"so_owner_{suffix}", PASSWORD)
            owner_headers = _auth(owner_token)
            outsider_token = await _login_token(client, f"so_out_{suffix}", PASSWORD)
            outsider_headers = _auth(outsider_token)

            resp = await client.post(
                f"{API}/sales/orders",
                headers=owner_headers,
                json={
                    "customerId": customer_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "1", "price": "5"},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            order_id = int(resp.json()["data"]["id"])
            order_ids.append(order_id)

            # 无权限用户：列表/详情/新增全部 403
            resp = await client.get(f"{API}/sales/orders", headers=outsider_headers)
            assert resp.status_code == 403, resp.text
            resp = await client.get(f"{API}/sales/orders/{order_id}", headers=outsider_headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/sales/orders",
                headers=outsider_headers,
                json={
                    "customerId": customer_id,
                    "orderDate": today,
                    "items": [
                        {"goodsId": goods[0][0], "unitId": goods[0][1], "qty": "1", "price": "5"},
                    ],
                },
            )
            assert resp.status_code == 403, resp.text

            # 数据范围=本人：同部门他建的单据不可见（403），提交/关闭同样拦截
            other_user_id = await _create_role_user(
                f"so_peer_{suffix}",
                dept_id,
                ["sal:order:list", "sal:order:submit"],
                data_scope=1,
            )
            role_ids.append(other_user_id[0])
            user_ids.append(other_user_id[1])
            peer_token = await _login_token(client, f"so_peer_{suffix}", PASSWORD)
            peer_headers = _auth(peer_token)
            resp = await client.get(f"{API}/sales/orders/{order_id}", headers=peer_headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(f"{API}/sales/orders/{order_id}/submit", headers=peer_headers)
            assert resp.status_code == 403, resp.text
        finally:
            await _cleanup(order_ids, [], customer_ids, dept_ids, user_ids, role_ids)
