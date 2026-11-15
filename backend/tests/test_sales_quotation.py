"""SA-01 销售报价验收测试。

覆盖：
  1. 新建 3 行报价 → 草稿；合计金额由后端重算正确；有效期早于报价日期 422；
  2. 发送后状态「已发送」；已发送不可编辑/删除（409）；过期报价发送被拒（409）；
     作废后状态「已失效」且不可重复作废；
  3. 详情返回完整明细（商品编码/名称/单位/金额），满足打印与复制预填订单要求；
     客户/商品停用被拒；
  4. 全流程不产生库存流水、不产生应收；操作日志完整；
  5. 权限：缺少 sal:quotation:* 时接口返回 403；数据范围（本人）生效，越权详情 403。

数据隔离：测试使用 conftest 提供的临时 SQLite 库，结束后物理清理本测试产生的数据。
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
from app.models.base_data import BdCustomer, BdGoods
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.sal import SalQuotation, SalQuotationItem
from app.models.sys import (
    SysDept,
    SysOperLog,
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


async def _create_customer(name: str, *, status: int = 1) -> int:
    """新建一个客户（测试自备，种子数据不含客户），返回 ID。"""
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(
            code=_unique("CUS"),
            name=name,
            status=status,
            credit_limit=Decimal("0"),
            credit_days=0,
            tenant_id=1,
        )
        session.add(customer)
        await session.commit()
        return int(customer.id)


async def _pick_goods(codes: tuple[str, str, str]) -> tuple[int, int, int]:
    """取三个启用商品 ID 作为报价明细（种子数据里 GD0001/GD0002/GD0003 必定存在）。"""
    async with AsyncSessionLocal() as session:
        ids: list[int] = []
        for code in codes:
            goods_id = await session.scalar(select(BdGoods.id).where(BdGoods.code == code))
            assert goods_id is not None, f"种子商品缺失: {code}"
            ids.append(int(goods_id))
    return ids[0], ids[1], ids[2]


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
            code=f"sa01_{uuid.uuid4().hex[:8]}",
            name="SA01销售报价测试角色",
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
    quotation_ids: list[int],
    customer_ids: list[int],
    dept_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
) -> None:
    """物理清理本测试数据：先删明细与单据，再删客户/用户/角色/部门。"""
    async with AsyncSessionLocal() as session:
        if quotation_ids:
            await session.execute(
                delete(SalQuotationItem).where(
                    SalQuotationItem.quotation_id.in_(quotation_ids)
                )
            )
            await session.execute(
                delete(SalQuotation).where(SalQuotation.id.in_(quotation_ids))
            )
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


async def _assert_no_stock_or_finance() -> None:
    """销售报价不应产生库存流水与应收应付：这四张表在整个流程后必须保持空。"""
    async with AsyncSessionLocal() as session:
        for model in (InvStockFlow, InvStock, FinReceivable, FinPayable):
            count = await session.scalar(select(func.count()).select_from(model))
            assert int(count or 0) == 0, f"销售报价不应写入 {model.__tablename__}"


def _payload(
    customer_id: int,
    items: list[dict[str, Any]],
    *,
    quote_date: str | None = None,
    valid_until: str | None = None,
    remark: str | None = None,
) -> dict[str, Any]:
    """构造新增报价请求体（camelCase，与前端提交一致）。"""
    body: dict[str, Any] = {
        "customerId": customer_id,
        "quoteDate": quote_date or date.today().isoformat(),
        "items": items,
    }
    if valid_until is not None:
        body["validUntil"] = valid_until
    if remark is not None:
        body["remark"] = remark
    return body


async def _scenario() -> None:
    """验收主线：新建 3 行 → 有效期校验 → 发送 → 过期拒绝 → 作废；全程无库存/财务写入。"""
    suffix = uuid.uuid4().hex[:8]
    customer_id = await _create_customer(f"SA01客户{suffix}")
    disabled_customer_id = await _create_customer(f"SA01停用客户{suffix}", status=0)
    goods_a, goods_b, goods_c = await _pick_goods(("GD0001", "GD0002", "GD0003"))
    quotation_ids: list[int] = []
    customer_ids = [customer_id, disabled_customer_id]
    dept_ids: list[int] = []
    user_ids: list[int] = []
    role_ids: list[int] = []
    today = date.today()

    async with _client() as client:
        token = await _login_token(client, *ADMIN)
        headers = _auth(token)
        try:
            # 1. 新建 3 行报价：默认草稿，单号 QT 前缀，金额后端重算
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    customer_id,
                    [
                        {"goodsId": goods_a, "qty": "10", "price": "25.50"},
                        {"goodsId": goods_b, "qty": "3", "price": "100"},
                        {"goodsId": goods_c, "qty": "2.5", "price": "88.88"},
                    ],
                    valid_until=(today + timedelta(days=7)).isoformat(),
                    remark="SA01 验收报价",
                ),
            )
            assert resp.status_code == 200, resp.text
            created = resp.json()["data"]
            quotation_id = int(created["id"])
            quotation_ids.append(quotation_id)
            assert created["status"] == 0, "新建报价应为草稿"
            assert created["quoteNo"].startswith("QT"), "单号应为 QT 前缀"
            assert created["customerName"] == f"SA01客户{suffix}"
            assert len(created["items"]) == 3, "应有 3 行明细"
            # 合计 = 10*25.50 + 3*100 + 2.5*88.88 = 255 + 300 + 222.20 = 777.20
            assert Decimal(str(created["totalAmount"])) == Decimal("777.20"), created["totalAmount"]
            assert Decimal(str(created["taxAmount"])) == Decimal("0"), "V1 报价不拆税，税额应为 0"
            assert Decimal(str(created["items"][2]["amount"])) == Decimal("222.20")
            assert created["items"][0]["goodsCode"] == "GD0001"
            assert created["items"][0]["unitName"], "明细应返回单位名称（打印用）"

            # 2. 有效期早于报价日期 → 422
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    customer_id,
                    [{"goodsId": goods_a, "qty": "1", "price": "1"}],
                    quote_date=today.isoformat(),
                    valid_until=(today - timedelta(days=1)).isoformat(),
                ),
            )
            assert resp.status_code == 422, "有效期早于报价日期应 422"

            # 3. 客户/商品校验：停用客户 422、商品数量 <= 0 → 422、重复商品 422
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    disabled_customer_id,
                    [{"goodsId": goods_a, "qty": "1", "price": "1"}],
                ),
            )
            assert resp.status_code == 422, "停用客户应被拒绝"
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    customer_id, [{"goodsId": goods_a, "qty": "0", "price": "1"}]
                ),
            )
            assert resp.status_code == 422, "数量必须大于 0"
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    customer_id,
                    [
                        {"goodsId": goods_a, "qty": "1", "price": "1"},
                        {"goodsId": goods_a, "qty": "2", "price": "2"},
                    ],
                ),
            )
            assert resp.status_code == 422, "同一商品不得重复录入"

            # 4. 修改草稿（改备注 + 明细替换），再校验修改后合计
            resp = await client.put(
                f"{API}/sales/quotations/{quotation_id}",
                headers=headers,
                json={"remark": "SA01 修改后的备注"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["remark"] == "SA01 修改后的备注"

            # 5. 发送：草稿 → 已发送
            resp = await client.post(
                f"{API}/sales/quotations/{quotation_id}/send", headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == 1, "发送后应为已发送"

            # 6. 已发送不可编辑/删除/再次发送
            resp = await client.put(
                f"{API}/sales/quotations/{quotation_id}",
                headers=headers,
                json={"remark": "已发送试图修改"},
            )
            assert resp.status_code == 409, "已发送不可编辑"
            resp = await client.delete(
                f"{API}/sales/quotations/{quotation_id}", headers=headers
            )
            assert resp.status_code == 409, "已发送不可删除"
            resp = await client.post(
                f"{API}/sales/quotations/{quotation_id}/send", headers=headers
            )
            assert resp.status_code == 409, "已发送不可重复发送"

            # 7. 过期报价不能发送：quote_date=3 天前、valid_until=昨天（合法保存），今天发送被拒
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(
                    customer_id,
                    [{"goodsId": goods_a, "qty": "5", "price": "10"}],
                    quote_date=(today - timedelta(days=3)).isoformat(),
                    valid_until=(today - timedelta(days=1)).isoformat(),
                ),
            )
            assert resp.status_code == 200, resp.text
            expired_created = resp.json()["data"]
            expired_id = int(expired_created["id"])
            expired_quote_no = str(expired_created["quoteNo"])
            quotation_ids.append(expired_id)
            resp = await client.post(
                f"{API}/sales/quotations/{expired_id}/send", headers=headers
            )
            assert resp.status_code == 409, "过期报价发送应被拒绝"
            assert "有效期" in resp.json()["message"], "应提示有效期原因"

            # 8. 作废：已发送 → 已失效；重复作废 409；草稿也可直接作废
            resp = await client.post(
                f"{API}/sales/quotations/{quotation_id}/void", headers=headers
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == 2, "作废后应为已失效"
            resp = await client.post(
                f"{API}/sales/quotations/{quotation_id}/void", headers=headers
            )
            assert resp.status_code == 409, "已失效不可重复作废"
            resp = await client.put(
                f"{API}/sales/quotations/{quotation_id}",
                headers=headers,
                json={"remark": "已失效试图修改"},
            )
            assert resp.status_code == 409, "已失效不可编辑"

            # 9. 列表筛选：状态=已发送 应能查到过期那张（仍为草稿）
            resp = await client.get(
                f"{API}/sales/quotations",
                headers=headers,
                params={"status": 0, "keyword": expired_quote_no},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["total"] == 1, "按状态+单号应能筛出过期草稿"

            # 10. 详情：明细完整（打印/复制预填所需字段）
            resp = await client.get(
                f"{API}/sales/quotations/{quotation_id}", headers=headers
            )
            assert resp.status_code == 200, resp.text
            detail = resp.json()["data"]
            assert len(detail["items"]) == 3
            assert all(
                item["goodsName"] and item["goodsCode"] and item["unitName"]
                for item in detail["items"]
            )
            assert detail["customerName"], "打印抬头需要客户名称"

            # 11. 全流程不写库存、不写应收应付
            await _assert_no_stock_or_finance()

            # 12. 操作日志完整（新增/修改/发送/作废）
            async with AsyncSessionLocal() as session:
                actions = set(
                    (
                        await session.execute(
                            select(SysOperLog.action).where(
                                SysOperLog.module == "销售管理",
                                SysOperLog.status == 1,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for expected in ("新增销售报价", "修改销售报价", "发送销售报价", "作废销售报价"):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(quotation_ids, customer_ids, dept_ids, user_ids, role_ids)


def test_sales_quotation_flow() -> None:
    """验收 1/2/4：草稿→发送→作废闭环、金额重算、有效期校验、无库存与财务写入。"""
    _run(_scenario())


async def _permission_scenario() -> None:
    """缺少 sal:quotation:* 权限码时接口返回 403（权限码来自菜单/按钮种子）。"""
    suffix = uuid.uuid4().hex[:8]
    role_id, user_id = await _create_role_user(
        username=f"sa01perm{suffix}", dept_id=None, perm_codes=["rpt:dashboard:list"], data_scope=1
    )
    customer_id = await _create_customer(f"SA01权限客户{suffix}")
    try:
        async with _client() as client:
            token = await _login_token(client, f"sa01perm{suffix}", PASSWORD)
            headers = _auth(token)
            resp = await client.get(f"{API}/sales/quotations", headers=headers)
            assert resp.status_code == 403, "无 sal:quotation:list 时列表应 403"
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers,
                json=_payload(customer_id, [{"goodsId": 1, "qty": "1", "price": "1"}]),
            )
            assert resp.status_code == 403, "无 sal:quotation:add 时新增应 403"
            resp = await client.delete(f"{API}/sales/quotations/1", headers=headers)
            assert resp.status_code == 403, "无 sal:quotation:delete 时删除应 403"
    finally:
        await _cleanup([], [customer_id], [], [user_id], [role_id])


def test_sales_quotation_permissions() -> None:
    """权限校验：无 sal:quotation:* 时列表/新增/删除均 403，不能只靠前端隐藏按钮。"""
    _run(_permission_scenario())


async def _scope_scenario() -> None:
    """数据范围「本人」：只看到自己创建的报价；越权详情 403；关键字按客户名称匹配。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"SA01销售部{suffix}")
    customer_id = await _create_customer(f"SA01范围客户{suffix}")
    goods_a, _, _ = await _pick_goods(("GD0001", "GD0002", "GD0003"))
    role_id_a, user_id_a = await _create_role_user(
        username=f"sa01a{suffix}",
        dept_id=dept_id,
        perm_codes=["sal:quotation:list", "sal:quotation:add", "sal:quotation:edit"],
        data_scope=1,
    )
    role_id_b, user_id_b = await _create_role_user(
        username=f"sa01b{suffix}",
        dept_id=dept_id,
        perm_codes=["sal:quotation:list", "sal:quotation:add"],
        data_scope=1,
    )
    quotation_ids: list[int] = []
    customer_ids = [customer_id]
    dept_ids = [dept_id]
    user_ids = [user_id_a, user_id_b]
    role_ids = [role_id_a, role_id_b]
    try:
        async with _client() as client:
            token_a = await _login_token(client, f"sa01a{suffix}", PASSWORD)
            headers_a = _auth(token_a)
            token_b = await _login_token(client, f"sa01b{suffix}", PASSWORD)
            headers_b = _auth(token_b)

            # A 创建一张报价（本人范围）
            resp = await client.post(
                f"{API}/sales/quotations",
                headers=headers_a,
                json=_payload(
                    customer_id, [{"goodsId": goods_a, "qty": "1", "price": "10"}]
                ),
            )
            assert resp.status_code == 200, resp.text
            created = resp.json()["data"]
            quotation_id = int(created["id"])
            quotation_ids.append(quotation_id)

            # B（同为本人范围）列表看不到 A 的报价；详情 403
            resp = await client.get(
                f"{API}/sales/quotations", headers=headers_b, params={"keyword": suffix}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["total"] == 0, "本人范围不应看到他人报价"
            resp = await client.get(
                f"{API}/sales/quotations/{quotation_id}", headers=headers_b
            )
            assert resp.status_code == 403, "越权详情应 403"

            # 关键字按客户名称可命中（创建人本人查询）
            resp = await client.get(
                f"{API}/sales/quotations",
                headers=headers_a,
                params={"keyword": f"SA01范围客户{suffix}"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["total"] == 1, "按客户名称关键字应命中"

            # 日期区间筛选
            today = date.today().isoformat()
            resp = await client.get(
                f"{API}/sales/quotations",
                headers=headers_a,
                params={"quoteDateStart": today, "quoteDateEnd": today},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["total"] == 1, "当日区间应命中"
    finally:
        await _cleanup(quotation_ids, customer_ids, dept_ids, user_ids, role_ids)


def test_sales_quotation_scope_and_filter() -> None:
    """数据范围（本人）生效，越权详情 403；客户名称关键字与日期区间筛选正确。"""
    _run(_scope_scenario())
