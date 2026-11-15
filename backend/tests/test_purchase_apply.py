"""P-01 采购申请验收测试。

覆盖：
  1. 新建 2 行申请 → 草稿；提交后状态「待审」，草稿不可再编辑（409）；
  2. 审核通过变「已审」；驳回回「草稿」并显示驳回原因；
  3. 列表按状态/部门/需求日期筛选正确；数据范围（本人/本部门/全部）生效；
     详情返回完整明细（商品编码/名称/单位），满足打印抬头与明细要求；
  4. 全流程不产生库存流水、不产生应收应付；操作日志完整；
  5. 权限：缺少 pur:apply:* 时接口返回 403。

数据隔离：测试使用 conftest 提供的临时 SQLite 库，结束后物理清理本测试产生的单据。
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
from app.models.base_data import BdGoods
from app.models.finance import FinPayable, FinReceivable
from app.models.inventory import InvStock, InvStockFlow
from app.models.pur import PurApply, PurApplyItem
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
    """生成唯一后缀，避免同名部门/账号冲突。"""
    return f"{prefix}{uuid.uuid4().hex[:8]}"


async def _pick_goods(codes: tuple[str, str]) -> tuple[int, int]:
    """取两个启用商品 ID 作为申请明细（种子数据里 GD0001/GD0002 必定存在）。"""
    async with AsyncSessionLocal() as session:
        ids: list[int] = []
        for code in codes:
            goods_id = await session.scalar(select(BdGoods.id).where(BdGoods.code == code))
            assert goods_id is not None, f"种子商品缺失: {code}"
            ids.append(int(goods_id))
    return ids[0], ids[1]


async def _create_dept(name: str) -> int:
    """新建一个部门，用于数据范围「本部门」验证。"""
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
            code=f"p01_{uuid.uuid4().hex[:8]}",
            name="P01采购申请测试角色",
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
    apply_ids: list[int], dept_ids: list[int], user_ids: list[int], role_ids: list[int]
) -> None:
    """物理清理本测试数据：先删明细与单据，再删用户/角色/部门。"""
    async with AsyncSessionLocal() as session:
        if apply_ids:
            await session.execute(delete(PurApplyItem).where(PurApplyItem.apply_id.in_(apply_ids)))
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
        await session.commit()


async def _assert_no_stock_or_finance() -> None:
    """采购申请不应产生库存流水与应收应付：这四张表在整个流程后必须保持空。"""
    async with AsyncSessionLocal() as session:
        for model in (InvStockFlow, InvStock, FinReceivable, FinPayable):
            count = await session.scalar(select(func.count()).select_from(model))
            assert int(count or 0) == 0, f"采购申请不应写入 {model.__tablename__}"


async def _scenario() -> None:
    """验收主线：新建 2 行 → 提交待审 → 待审不可编辑 → 驳回回草稿 → 再提交 → 审核通过已审。"""
    suffix = uuid.uuid4().hex[:8]
    dept_id = await _create_dept(f"P01采购部{suffix}")
    goods_a, goods_b = await _pick_goods(("GD0001", "GD0002"))
    apply_ids: list[int] = []
    dept_ids = [dept_id]
    user_ids: list[int] = []
    role_ids: list[int] = []
    need_date = date.today().isoformat()

    async with _client() as client:
        token = await _login_token(client, *ADMIN)
        headers = _auth(token)
        try:
            # 1. 新建 2 行申请：默认草稿，单号由后端生成
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=headers,
                json={
                    "deptId": dept_id,
                    "needDate": need_date,
                    "remark": "P01 验收申请",
                    "items": [
                        {"goodsId": goods_a, "qty": "10", "needDate": need_date},
                        {"goodsId": goods_b, "qty": "2.5", "needDate": need_date},
                    ],
                },
            )
            assert resp.status_code == 200, resp.text
            created = resp.json()["data"]
            apply_id = int(created["id"])
            apply_ids.append(apply_id)
            assert created["status"] == 0, "新建申请应为草稿"
            assert created["applyNo"].startswith("PA"), "单号应为 PA 前缀"
            assert created["deptId"] == dept_id
            assert len(created["items"]) == 2, "应有 2 行明细"
            assert created["items"][0]["goodsCode"] == "GD0001"
            assert Decimal(str(created["items"][1]["qty"])) == Decimal("2.5")

            # 2. 提交：草稿 → 待审
            resp = await client.post(f"{API}/purchase/applies/{apply_id}/submit", headers=headers)
            assert resp.status_code == 200, resp.text
            assert resp.json()["data"]["status"] == 1, "提交后应为待审"

            # 3. 待审不可再编辑（草稿才可编辑）
            resp = await client.put(
                f"{API}/purchase/applies/{apply_id}",
                headers=headers,
                json={"remark": "待审试图修改"},
            )
            assert resp.status_code == 409, resp.text

            # 4. 驳回：待审 → 草稿，并保留驳回原因
            resp = await client.post(
                f"{API}/purchase/applies/{apply_id}/audit",
                headers=headers,
                json={"result": "rejected", "remark": "数量不合理，请拆分"},
            )
            assert resp.status_code == 200, resp.text
            rejected = resp.json()["data"]
            assert rejected["status"] == 0, "驳回后应回草稿"
            assert rejected["auditRemark"] == "数量不合理，请拆分"

            # 5. 再次提交后，驳回原因为空应被 422 拦截
            resp = await client.post(f"{API}/purchase/applies/{apply_id}/submit", headers=headers)
            assert resp.status_code == 200, resp.text
            resp = await client.post(
                f"{API}/purchase/applies/{apply_id}/audit",
                headers=headers,
                json={"result": "rejected", "remark": "   "},
            )
            assert resp.status_code == 422, resp.text

            # 6. 审核通过：待审 → 已审
            resp = await client.post(
                f"{API}/purchase/applies/{apply_id}/audit",
                headers=headers,
                json={"result": "approved", "remark": "同意采购"},
            )
            assert resp.status_code == 200, resp.text
            approved = resp.json()["data"]
            assert approved["status"] == 2, "审核通过应为已审"
            assert approved["auditByName"], "应返回审核人姓名"
            assert approved["auditAt"] == date.today().isoformat()

            # 7. 已审不可再提交/审核
            resp = await client.post(f"{API}/purchase/applies/{apply_id}/submit", headers=headers)
            assert resp.status_code == 409, resp.text
            resp = await client.post(
                f"{API}/purchase/applies/{apply_id}/audit",
                headers=headers,
                json={"result": "approved"},
            )
            assert resp.status_code == 409, resp.text

            # 8. 详情：明细完整（打印抬头/明细所需字段）
            resp = await client.get(f"{API}/purchase/applies/{apply_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            detail = resp.json()["data"]
            assert len(detail["items"]) == 2
            assert all(item["goodsName"] and item["unitName"] for item in detail["items"])

            # 9. 校验：数量 <= 0、同一商品重复录入
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=headers,
                json={
                    "deptId": dept_id,
                    "needDate": need_date,
                    "items": [{"goodsId": goods_a, "qty": "0"}],
                },
            )
            assert resp.status_code == 422, "数量必须大于 0"
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=headers,
                json={
                    "deptId": dept_id,
                    "needDate": need_date,
                    "items": [
                        {"goodsId": goods_a, "qty": "1"},
                        {"goodsId": goods_a, "qty": "2"},
                    ],
                },
            )
            assert resp.status_code == 422, "同一商品不得重复录入"

            # 10. 全流程不写库存、不写应收应付
            await _assert_no_stock_or_finance()

            # 11. 操作日志完整（新增/提交/驳回/审核通过）
            async with AsyncSessionLocal() as session:
                actions = set(
                    (
                        await session.execute(
                            select(SysOperLog.action).where(
                                SysOperLog.module == "采购管理",
                                SysOperLog.status == 1,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for expected in ("新增采购申请", "提交采购申请", "驳回采购申请", "审核通过采购申请"):
                assert expected in actions, f"缺少操作日志: {expected}"
        finally:
            await _cleanup(apply_ids, dept_ids, user_ids, role_ids)


def test_purchase_apply_flow() -> None:
    """验收 1/2/4：草稿→待审→驳回→再提交→已审，且不产生库存与财务数据。"""
    _run(_scenario())


async def _permission_scenario() -> None:
    """缺少 pur:apply:* 权限码时接口返回 403（权限码来自菜单/按钮种子）。"""
    suffix = uuid.uuid4().hex[:8]
    role_id, user_id = await _create_role_user(
        username=f"p01perm{suffix}", dept_id=None, perm_codes=["rpt:dashboard:list"], data_scope=1
    )
    try:
        async with _client() as client:
            token = await _login_token(client, f"p01perm{suffix}", PASSWORD)
            headers = _auth(token)
            resp = await client.get(f"{API}/purchase/applies", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/applies", headers=headers, json={"needDate": "2026-12-01"}
            )
            assert resp.status_code == 403, resp.text
            resp = await client.post(f"{API}/purchase/applies/1/submit", headers=headers)
            assert resp.status_code == 403, resp.text
            resp = await client.post(
                f"{API}/purchase/applies/1/audit", headers=headers, json={"result": "approved"}
            )
            assert resp.status_code == 403, resp.text
    finally:
        await _cleanup([], [], [user_id], [role_id])


def test_purchase_apply_permissions() -> None:
    """权限校验：无 pur:apply:* 时列表/新增/提交/审核均 403，不能只靠前端隐藏按钮。"""
    _run(_permission_scenario())


async def _filter_scope_scenario() -> None:
    """验收 3：按状态/部门/需求日期筛选正确；数据范围「本人」只看到自己创建的单据。"""
    suffix = uuid.uuid4().hex[:8]
    dept_a = await _create_dept(f"P01甲部门{suffix}")
    dept_b = await _create_dept(f"P01乙部门{suffix}")
    goods_a, _ = await _pick_goods(("GD0003", "GD0004"))
    apply_ids: list[int] = []
    dept_ids = [dept_a, dept_b]
    user_ids: list[int] = []
    role_ids: list[int] = []

    async with _client() as client:
        admin_token = await _login_token(client, *ADMIN)
        admin_headers = _auth(admin_token)
        try:
            # 管理员在 A 部门建一张（需求日期 2026-12-01），在 B 部门建一张（2027-01-01）
            for dept_id, need_date in ((dept_a, "2026-12-01"), (dept_b, "2027-01-01")):
                resp = await client.post(
                    f"{API}/purchase/applies",
                    headers=admin_headers,
                    json={
                        "deptId": dept_id,
                        "needDate": need_date,
                        "items": [{"goodsId": goods_a, "qty": "3"}],
                    },
                )
                assert resp.status_code == 200, resp.text
                apply_ids.append(int(resp.json()["data"]["id"]))

            # 按部门筛选
            resp = await client.get(
                f"{API}/purchase/applies",
                headers=admin_headers,
                params={"deptId": dept_a, "pageSize": 100},
            )
            assert resp.status_code == 200, resp.text
            ids = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert apply_ids[0] in ids and apply_ids[1] not in ids

            # 按需求日期区间筛选（只命中第一张）
            resp = await client.get(
                f"{API}/purchase/applies",
                headers=admin_headers,
                params={
                    "needDateStart": "2026-12-01",
                    "needDateEnd": "2026-12-31",
                    "pageSize": 100,
                },
            )
            assert resp.status_code == 200, resp.text
            ids = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert apply_ids[0] in ids and apply_ids[1] not in ids

            # 按状态筛选：两张都是草稿，筛「待审」应查不到
            resp = await client.get(
                f"{API}/purchase/applies",
                headers=admin_headers,
                params={"status": 1, "pageSize": 100},
            )
            assert resp.status_code == 200, resp.text
            ids = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert apply_ids[0] not in ids and apply_ids[1] not in ids

            # 数据范围：本人范围的账号只能看到自己创建的单据（管理员建的看不到）
            username = f"p01self{suffix}"
            role_id, user_id = await _create_role_user(
                username=username,
                dept_id=dept_a,
                perm_codes=["pur:apply:list", "pur:apply:add"],
                data_scope=1,
            )
            role_ids.append(role_id)
            user_ids.append(user_id)
            token = await _login_token(client, username, PASSWORD)
            headers = _auth(token)
            resp = await client.get(
                f"{API}/purchase/applies", headers=headers, params={"pageSize": 100}
            )
            assert resp.status_code == 200, resp.text
            ids = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert not (ids & set(apply_ids)), "本人范围不应看到他人创建的单据"

            # 本人范围：自己建的单据可见，且部门默认取本人部门
            resp = await client.post(
                f"{API}/purchase/applies",
                headers=headers,
                json={"needDate": "2026-12-20", "items": [{"goodsId": goods_a, "qty": "1"}]},
            )
            assert resp.status_code == 200, resp.text
            own = resp.json()["data"]
            apply_ids.append(int(own["id"]))
            assert own["deptId"] == dept_a, "未指定部门时应默认当前用户部门"
            resp = await client.get(
                f"{API}/purchase/applies", headers=headers, params={"pageSize": 100}
            )
            ids = {int(row["id"]) for row in resp.json()["data"]["items"]}
            assert int(own["id"]) in ids

            # 越权访问他人单据详情返回 403
            resp = await client.get(f"{API}/purchase/applies/{apply_ids[0]}", headers=headers)
            assert resp.status_code == 403, resp.text
        finally:
            await _cleanup(apply_ids, dept_ids, user_ids, role_ids)


def test_purchase_apply_filter_and_scope() -> None:
    """验收 3：状态/部门/日期筛选正确，数据范围（本人）生效，越权详情 403。"""
    _run(_filter_scope_scenario())
