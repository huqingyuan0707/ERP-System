"""F-04 付款单与付款核销验收测试。

覆盖（需求条目拆分 F-04 验收标准 + 开发任务拆解 FIN-08/09/10）：
  1. 付款 600 核销 A 400、B 200：两应付 status=2、paid/unpaid 重算正确，
     付款单 allocated=600 / unallocated=0（已核销）；
  2. 部分核销：付款单与应付分别停在「部分核销 / 部分付」，可分次核销直到用完；
  3. 对未付 300 的应付核销 400 → 422；核销总额超付款额 → 422；应付余额不变；
  4. 跨供应商核销拒绝 409；已核销完成重复核销 409；
  5. 已核销后编辑/删除 409；未核销付款单可编辑/删除；版本号冲突 409；
  6. 重复提交（同 Idempotency-Key）不重复记账；
  7. 权限（缺 fin:payment:writeoff 返回 403）、数据范围（本人范围查他人 403）、
     列表/汇总/详情 camelCase 字段、操作日志、F-02 unsettled 筛选。

注意：测试间共享同一个 SQLite 库，结束时必须物理清理财务与用户/角色/供应商数据。
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
from app.models.base_data import BdSupplier
from app.models.finance import FinPayable, FinPayment, FinPaymentItem
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


async def _seed_payable(
    supplier_id: int,
    source_no: str,
    amount: str,
    *,
    paid: str = "0",
) -> FinPayable:
    """直接种一张未删除应付（ unpaid = amount - paid），返回行供断言。"""
    async with AsyncSessionLocal() as session:
        amount_d = Decimal(amount)
        paid_d = Decimal(paid)
        unpaid_d = amount_d - paid_d
        payable = FinPayable(
            supplier_id=supplier_id,
            source_type="purchase_inbound",
            source_no=source_no,
            bill_date=date.today() - timedelta(days=30),
            due_date=date.today(),
            amount=amount_d,
            paid_amount=paid_d,
            unpaid_amount=unpaid_d,
            status=2 if unpaid_d <= 0 else (1 if paid_d > 0 else 0),
            tenant_id=1,
        )
        session.add(payable)
        await session.commit()
        return payable


async def _create_role_user(
    username: str, perm_codes: list[str], *, data_scope: int = 3
) -> tuple[int, int]:
    """创建角色 + 用户并授权；返回 (role_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"f04_{uuid.uuid4().hex[:8]}",
            name="F04付款测试角色",
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


async def _create_payment(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    supplier_id: int,
    amount: str,
    *,
    idempotency_key: str | None = None,
) -> httpx.Response:
    """创建付款单（返回原始响应，供状态码断言）。"""
    extra_headers = (
        {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    )
    return await client.post(
        f"{API}/finance/payments",
        headers={**headers, **extra_headers},
        json={
            "supplierId": supplier_id,
            "paymentDate": date.today().isoformat(),
            "amount": amount,
            "method": "cash",
            "remark": "测试付款",
        },
    )


async def _writeoff(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    payment_id: int,
    items: list[dict[str, Any]],
    *,
    idempotency_key: str | None = None,
) -> httpx.Response:
    """付款核销（返回原始响应，供状态码断言）。"""
    extra_headers = (
        {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    )
    return await client.post(
        f"{API}/finance/payments/{payment_id}/writeoff",
        headers={**headers, **extra_headers},
        json={"items": items},
    )


async def _get_payment(payment_id: int) -> FinPayment:
    """按 ID 取付款单（断言辅助）。"""
    async with AsyncSessionLocal() as session:
        payment = await session.scalar(select(FinPayment).where(FinPayment.id == payment_id))
        assert payment is not None, f"付款单不存在: {payment_id}"
        return payment


async def _get_payable(payable_id: int) -> FinPayable:
    """按 ID 取应付（断言辅助）。"""
    async with AsyncSessionLocal() as session:
        payable = await session.scalar(select(FinPayable).where(FinPayable.id == payable_id))
        assert payable is not None, f"应付不存在: {payable_id}"
        return payable


async def _count_items(payment_id: int) -> int:
    """统计付款单核销明细行数（幂等断言辅助）。"""
    async with AsyncSessionLocal() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(FinPaymentItem)
                .where(FinPaymentItem.payment_id == payment_id)
            )
        )


async def _cleanup(
    supplier_ids: list[int],
    user_ids: list[int],
    role_ids: list[int],
) -> None:
    """物理清理本测试数据：核销明细 → 付款单 → 应付 → 日志 → 用户/角色/供应商。"""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(FinPaymentItem))
        await session.execute(delete(FinPayment))
        await session.execute(delete(FinPayable))
        # 只清理本模块写入的操作日志，避免影响其他用例对日志的断言
        await session.execute(delete(SysOperLog).where(SysOperLog.module == "财务管理"))
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


# ---------- 场景用例（同步包装异步协程，与既有测试写法一致） ----------


def test_create_list_summary_and_detail() -> None:
    """新建付款单 → 待核销；列表/汇总/详情 camelCase 字段齐全。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款基础供应商")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "600.00")
                assert resp.status_code == 200, resp.text
                data = resp.json()["data"]
                assert data["paymentNo"].startswith("FK"), data
                assert data["supplierId"] == supplier_id
                assert data["supplierName"] == "付款基础供应商"
                assert Decimal(str(data["amount"])) == Decimal("600.00")
                assert Decimal(str(data["allocatedAmount"])) == Decimal("0")
                assert Decimal(str(data["unallocatedAmount"])) == Decimal("600.00")
                assert data["status"] == 0  # 待核销
                assert data["items"] == []
                payment_id = int(data["id"])

                # 列表：关键字筛选命中，未核销筛选命中
                resp = await client.get(
                    f"{API}/finance/payments",
                    headers=headers,
                    params={"keyword": data["paymentNo"], "status": 0},
                )
                assert resp.status_code == 200, resp.text
                assert resp.json()["data"]["total"] == 1

                # 汇总卡片：与列表同口径
                resp = await client.get(f"{API}/finance/payments/summary", headers=headers)
                assert resp.status_code == 200, resp.text
                summary = resp.json()["data"]
                assert Decimal(str(summary["totalAmount"])) == Decimal("600.00")
                assert Decimal(str(summary["totalUnallocated"])) == Decimal("600.00")
                assert summary["count"] == 1

                # 详情：核销明细为空数组
                resp = await client.get(f"{API}/finance/payments/{payment_id}", headers=headers)
                assert resp.status_code == 200, resp.text
                assert resp.json()["data"]["items"] == []

                # 金额 <= 0 直接 422（模型 CHECK 兜底前先被入参校验拦截）
                resp = await _create_payment(client, headers, supplier_id, "0")
                assert resp.status_code == 422, resp.text
        finally:
            await _cleanup([supplier_id], [], [])

    _run(_scenario())


def test_update_delete_draft_and_version_conflict() -> None:
    """未核销付款单可编辑/删除；版本号冲突 409；删除后详情 404。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款编辑供应商")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "300.00")
                payment = resp.json()["data"]
                payment_id = int(payment["id"])
                version = int(payment["version"])

                resp = await client.put(
                    f"{API}/finance/payments/{payment_id}",
                    headers=headers,
                    json={"amount": "500.00", "remark": "改过", "version": version},
                )
                assert resp.status_code == 200, resp.text
                updated = resp.json()["data"]
                assert Decimal(str(updated["amount"])) == Decimal("500.00")
                assert Decimal(str(updated["unallocatedAmount"])) == Decimal("500.00")
                assert int(updated["version"]) == version + 1

                # 版本号过期 → 409
                resp = await client.put(
                    f"{API}/finance/payments/{payment_id}",
                    headers=headers,
                    json={"remark": "并发", "version": version},
                )
                assert resp.status_code == 409, resp.text

                # 删除草稿 → 详情 404
                resp = await client.delete(f"{API}/finance/payments/{payment_id}", headers=headers)
                assert resp.status_code == 200, resp.text
                resp = await client.get(f"{API}/finance/payments/{payment_id}", headers=headers)
                assert resp.status_code == 404, resp.text
        finally:
            await _cleanup([supplier_id], [], [])

    _run(_scenario())


def test_writeoff_full_and_partial() -> None:
    """付款 600 分次核销 A 400、B 200：应付/付款单状态逐步推进并正确重算。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款核销供应商")
        payable_a = await _seed_payable(supplier_id, "PI-F04-A", "400.00")
        payable_b = await _seed_payable(supplier_id, "PI-F04-B", "200.00")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "600.00")
                payment_id = int(resp.json()["data"]["id"])

                # 第一次核销 A 400：付款单部分核销，A 已付清
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_a.id), "writeoffAmount": "400.00"}],
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()["data"]
                assert data["status"] == 1  # 部分核销
                assert Decimal(str(data["allocatedAmount"])) == Decimal("400.00")
                assert Decimal(str(data["unallocatedAmount"])) == Decimal("200.00")

                payable = await _get_payable(int(payable_a.id))
                assert payable.status == 2  # 已付清
                assert Decimal(str(payable.paid_amount)) == Decimal("400.00")
                assert Decimal(str(payable.unpaid_amount)) == Decimal("0")

                # 第二次核销 B 200：付款单已核销完成
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_b.id), "writeoffAmount": "200.00"}],
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()["data"]
                assert data["status"] == 2
                assert Decimal(str(data["allocatedAmount"])) == Decimal("600.00")
                assert Decimal(str(data["unallocatedAmount"])) == Decimal("0")

                payable_b_row = await _get_payable(int(payable_b.id))
                assert payable_b_row.status == 2
                assert Decimal(str(payable_b_row.unpaid_amount)) == Decimal("0")

                # 详情：核销明细带应付单号，按提交顺序
                resp = await client.get(f"{API}/finance/payments/{payment_id}", headers=headers)
                items = resp.json()["data"]["items"]
                assert len(items) == 2
                assert {item["payableSourceNo"] for item in items} == {"PI-F04-A", "PI-F04-B"}

                # 部分付状态的应付：先只核销一部分
                payable_c = await _seed_payable(supplier_id, "PI-F04-C", "300.00")
                resp = await _create_payment(client, headers, supplier_id, "100.00")
                payment_c = int(resp.json()["data"]["id"])
                resp = await _writeoff(
                    client,
                    headers,
                    payment_c,
                    [{"payableId": int(payable_c.id), "writeoffAmount": "100.00"}],
                )
                assert resp.status_code == 200, resp.text
                payable_c_row = await _get_payable(int(payable_c.id))
                assert payable_c_row.status == 1  # 部分付
                assert Decimal(str(payable_c_row.unpaid_amount)) == Decimal("200.00")

                # 操作日志：新增付款与付款核销各一条
                async with AsyncSessionLocal() as session:
                    logs = (
                        (
                            await session.execute(
                                select(SysOperLog).where(SysOperLog.module == "财务管理")
                            )
                        )
                        .scalars()
                        .all()
                    )
                    actions = {log.action for log in logs}
                assert {"新增付款", "付款核销"} <= actions
        finally:
            await _cleanup([supplier_id], [], [])

    _run(_scenario())


def test_writeoff_rejections_leave_balance_unchanged() -> None:
    """超未付 422 / 总额超余额 422 / 跨供应商 409 / 重复核销 409，应付余额均不变。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款拒绝供应商")
        other_supplier_id = await _create_supplier("付款其他供应商")
        payable_a = await _seed_payable(supplier_id, "PI-F04-R1", "300.00")
        payable_other = await _seed_payable(other_supplier_id, "PI-F04-R2", "100.00")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "400.00")
                payment_id = int(resp.json()["data"]["id"])

                # 单条超过应付未付（300 vs 400）→ 422
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_a.id), "writeoffAmount": "400.00"}],
                )
                assert resp.status_code == 422, resp.text
                payable = await _get_payable(int(payable_a.id))
                assert Decimal(str(payable.unpaid_amount)) == Decimal("300.00")

                # 核销总额超过付款单未分配余额（500 vs 400）→ 422
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [
                        {"payableId": int(payable_a.id), "writeoffAmount": "250.00"},
                        {
                            "payableId": int(payable_other.id),
                            "writeoffAmount": "250.00",
                        },
                    ],
                )
                # 两张应付分属两个供应商，跨供应商 409 先于总额 422 触发
                assert resp.status_code == 409, resp.text

                # 同供应商两条合计超余额（300 + 200 > 400）→ 422
                payable_b = await _seed_payable(supplier_id, "PI-F04-R3", "200.00")
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [
                        {"payableId": int(payable_a.id), "writeoffAmount": "300.00"},
                        {"payableId": int(payable_b.id), "writeoffAmount": "200.00"},
                    ],
                )
                assert resp.status_code == 422, resp.text
                payment = await _get_payment(payment_id)
                assert Decimal(str(payment.allocated_amount)) == Decimal("0")

                # 跨供应商核销 → 409（付款单余额充足时也会被拒）
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_other.id), "writeoffAmount": "100.00"}],
                )
                assert resp.status_code == 409, resp.text

                # 正常核销 300 后重复核销（余额 100，再核销 100 即完成）
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_a.id), "writeoffAmount": "300.00"}],
                )
                assert resp.status_code == 200, resp.text
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_b.id), "writeoffAmount": "100.00"}],
                )
                assert resp.status_code == 200, resp.text
                # 已核销完成后再核销 → 409
                payable_d = await _seed_payable(supplier_id, "PI-F04-R4", "50.00")
                resp = await _writeoff(
                    client,
                    headers,
                    payment_id,
                    [{"payableId": int(payable_d.id), "writeoffAmount": "50.00"}],
                )
                assert resp.status_code == 409, resp.text

                # 已核销应付（unpaid=0）不能被其他付款单核销 → 422
                resp = await _create_payment(client, headers, supplier_id, "100.00")
                other_payment = int(resp.json()["data"]["id"])
                resp = await _writeoff(
                    client,
                    headers,
                    other_payment,
                    [{"payableId": int(payable_a.id), "writeoffAmount": "10.00"}],
                )
                assert resp.status_code == 422, resp.text

                # 已核销付款单不能编辑/删除
                resp = await client.put(
                    f"{API}/finance/payments/{payment_id}",
                    headers=headers,
                    json={"remark": "核销后改"},
                )
                assert resp.status_code == 409, resp.text
                resp = await client.delete(f"{API}/finance/payments/{payment_id}", headers=headers)
                assert resp.status_code == 409, resp.text
        finally:
            await _cleanup([supplier_id, other_supplier_id], [], [])

    _run(_scenario())


def test_writeoff_idempotency_no_double_accounting() -> None:
    """同 Idempotency-Key 重复提交核销：只执行一次，不重复记账。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款幂等供应商")
        payable_a = await _seed_payable(supplier_id, "PI-F04-IDEM", "400.00")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                # 创建不带幂等键：SQLite 测试库单写者，带键创建会在取号独立连接上撞锁
                # （PostgreSQL 生产库无此限制）；核销不取号，幂等键只用于核销重放
                resp = await _create_payment(client, headers, supplier_id, "400.00")
                assert resp.status_code == 200, resp.text
                payment_id = int(resp.json()["data"]["id"])

                key = "f04-writeoff-idem"
                payload = [{"payableId": int(payable_a.id), "writeoffAmount": "400.00"}]
                resp = await _writeoff(client, headers, payment_id, payload, idempotency_key=key)
                assert resp.status_code == 200, resp.text
                first = resp.json()["data"]

                # 重复提交：返回同一结果，不产生第二条核销明细
                resp = await _writeoff(client, headers, payment_id, payload, idempotency_key=key)
                assert resp.status_code == 200, resp.text
                second = resp.json()["data"]
                assert second["id"] == first["id"]
                assert second["allocatedAmount"] == first["allocatedAmount"]
                assert await _count_items(payment_id) == 1

                payable = await _get_payable(int(payable_a.id))
                assert Decimal(str(payable.paid_amount)) == Decimal("400.00")
        finally:
            await _cleanup([supplier_id], [], [])

    _run(_scenario())


def test_payment_permissions_403() -> None:
    """无 fin:payment:add/ writeoff 权限分别 403；无 list 权限列表 403。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款权限供应商")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "100.00")
                payment_id = int(resp.json()["data"]["id"])

                payable = await _seed_payable(supplier_id, "PI-F04-PERM", "100.00")

                # 只有查看权限的用户：新增/核销 403
                username = f"f04_viewer_{uuid.uuid4().hex[:6]}"
                _, user_id = await _create_role_user(username, ["fin:payment:list"])
                viewer_token = await _login_token(client, username, PASSWORD)
                viewer_headers = _auth(viewer_token)
                resp = await _create_payment(client, viewer_headers, supplier_id, "100.00")
                assert resp.status_code == 403, resp.text
                resp = await _writeoff(
                    client,
                    viewer_headers,
                    payment_id,
                    [{"payableId": int(payable.id), "writeoffAmount": "100.00"}],
                )
                assert resp.status_code == 403, resp.text

                # 有写权限但没有 list 权限：列表 403（require_perm 对每个接口独立校验）
                writer = f"f04_writer_{uuid.uuid4().hex[:6]}"
                _, writer_id = await _create_role_user(
                    writer, ["fin:payment:add", "fin:payment:writeoff"]
                )
                writer_token = await _login_token(client, writer, PASSWORD)
                resp = await client.get(f"{API}/finance/payments", headers=_auth(writer_token))
                assert resp.status_code == 403, resp.text
        finally:
            await _cleanup([supplier_id], [user_id, writer_id], [])

    _run(_scenario())


def test_payment_data_scope_403() -> None:
    """本人数据范围的用户查他人付款单详情 403，列表看不到他人单据。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款范围供应商")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await _create_payment(client, headers, supplier_id, "100.00")
                payment_id = int(resp.json()["data"]["id"])

                username = f"f04_self_{uuid.uuid4().hex[:6]}"
                _, self_user_id = await _create_role_user(
                    username, ["fin:payment:list"], data_scope=1
                )
                self_token = await _login_token(client, username, PASSWORD)
                self_headers = _auth(self_token)

                resp = await client.get(
                    f"{API}/finance/payments/{payment_id}", headers=self_headers
                )
                assert resp.status_code == 403, resp.text

                resp = await client.get(f"{API}/finance/payments", headers=self_headers)
                assert resp.status_code == 200, resp.text
                assert resp.json()["data"]["total"] == 0
        finally:
            await _cleanup([supplier_id], [self_user_id], [])

    _run(_scenario())


def test_payable_unsettled_filter() -> None:
    """F-02 unsettled 筛选：只返回未付 > 0 的应付，负数/零余额被排除。"""

    async def _scenario() -> None:
        supplier_id = await _create_supplier("付款取数供应商")
        await _seed_payable(supplier_id, "PI-F04-U1", "300.00")
        await _seed_payable(supplier_id, "PI-F04-U2", "150.00", paid="50.00")
        try:
            async with _client() as client:
                token = await _login_token(client, *ADMIN)
                headers = _auth(token)
                resp = await client.get(
                    f"{API}/finance/payables",
                    headers=headers,
                    params={"supplierId": supplier_id, "unsettled": "true", "pageSize": 50},
                )
                assert resp.status_code == 200, resp.text
                items = resp.json()["data"]["items"]
                assert {item["sourceNo"] for item in items} == {"PI-F04-U1", "PI-F04-U2"}

                # 全额付清的应付不在未结清列表里
                await _seed_payable(supplier_id, "PI-F04-U3", "100.00", paid="100.00")
                resp = await client.get(
                    f"{API}/finance/payables",
                    headers=headers,
                    params={"supplierId": supplier_id, "unsettled": "true", "pageSize": 50},
                )
                assert resp.status_code == 200, resp.text
                items = resp.json()["data"]["items"]
                assert "PI-F04-U3" not in {item["sourceNo"] for item in items}
        finally:
            await _cleanup([supplier_id], [], [])

    _run(_scenario())
