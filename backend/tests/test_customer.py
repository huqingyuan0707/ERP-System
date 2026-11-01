"""客户管理验收测试（B-05）。

覆盖：
  - 新增：编码重复 409；信用额度/账期为负 422；
  - 信用额度参与订单审核：已发生应收 + 本次金额超额度时 assert_within_credit 拒绝；
  - 停用后新单据不可选（options 不含停用客户）；
  - 删除前检查销售订单/出库/应收引用，有引用返回 409；无引用可软删；
  - 账期决定应收到期日：calculate_due_date = 出库日期 + 账期；
  - 操作日志：新增/修改/停用/启用/删除均写入 sys_oper_log。
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import delete, select

from app.core.exceptions import AppException, ErrorCode
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer
from app.models.finance import ArApRecord
from app.models.sys import SysOperLog, SysUser
from app.services.customer import CustomerService


API = "/api/v1"
ADMIN = ("admin", "admin123")


def _run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def _client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _login_token(client, username, password):
    resp = await client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _admin_id():
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(SysUser).where(SysUser.username == "admin"))
        ).scalar_one()
        return int(user.id)


async def _create_customer(client, token, payload):
    return await client.post(
        f"{API}/base/customers",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


async def _cleanup(customer_ids):
    if not customer_ids:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(ArApRecord).where(ArApRecord.partner_id.in_(customer_ids))
        )
        await session.execute(
            delete(BdCustomer).where(BdCustomer.id.in_(customer_ids))
        )
        await session.commit()


def test_customer_b05_rules():
    _run(_scenario())


async def _scenario():
    import uuid

    suffix = uuid.uuid4().hex[:8]
    created_ids: list[int] = []
    try:
        async with _client() as client:
            token = await _login_token(client, *ADMIN)
            headers = {"Authorization": f"Bearer {token}"}

            # ------------------------------------------------------------------
            # 1. 新增客户成功
            # ------------------------------------------------------------------
            code_a = f"CUS_TEST_{suffix}_A"
            resp = await _create_customer(
                client,
                token,
                {
                    "code": code_a,
                    "name": f"测试客户A{suffix}",
                    "creditLimit": 300,
                    "creditDays": 30,
                    "status": 1,
                    "remark": "测试A",
                },
            )
            assert resp.status_code == 200, resp.text
            customer_a = resp.json()["data"]
            created_ids.append(int(customer_a["id"]))
            assert customer_a["code"] == code_a
            assert Decimal(str(customer_a["creditLimit"])) == Decimal("300")

            # ------------------------------------------------------------------
            # 2. 编码重复 -> 409
            # ------------------------------------------------------------------
            resp = await _create_customer(
                client,
                token,
                {"code": code_a, "name": f"重复客户{suffix}", "creditLimit": 0, "status": 1},
            )
            assert resp.status_code == 409, resp.text

            # ------------------------------------------------------------------
            # 3. 信用额度为负 -> 422
            # ------------------------------------------------------------------
            resp = await _create_customer(
                client,
                token,
                {"code": f"CUS_TEST_{suffix}_NEG", "name": f"负额度{suffix}",
                 "creditLimit": -1, "status": 1},
            )
            assert resp.status_code == 422, resp.text

            # ------------------------------------------------------------------
            # 4. 账期为负 -> 422
            # ------------------------------------------------------------------
            resp = await _create_customer(
                client,
                token,
                {"code": f"CUS_TEST_{suffix}_NEGD", "name": f"负账期{suffix}",
                 "creditLimit": 0, "creditDays": -1, "status": 1},
            )
            assert resp.status_code == 422, resp.text

            # ------------------------------------------------------------------
            # 5. 停用后新单据不可选（options 不含停用客户）
            # ------------------------------------------------------------------
            resp = await client.put(
                f"{API}/base/customers/{customer_a['id']}",
                headers=headers,
                json={"status": 0},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.get(
                f"{API}/base/customers/options", headers=headers
            )
            assert resp.status_code == 200, resp.text
            option_ids = [item["id"] for item in resp.json()["data"]]
            assert customer_a["id"] not in option_ids, "停用客户不应出现在可选列表"

            # 恢复启用，便于后续引用测试
            resp = await client.put(
                f"{API}/base/customers/{customer_a['id']}",
                headers=headers,
                json={"status": 1},
            )
            assert resp.status_code == 200, resp.text

            # ------------------------------------------------------------------
            # 6. 删除前检查应收引用 -> 409；移除应收后可软删
            # ------------------------------------------------------------------
            async with AsyncSessionLocal() as session:
                session.add(
                    ArApRecord(
                        tenant_id=1,
                        partner_type=2,  # 客户
                        partner_id=customer_a["id"],
                        record_type=2,  # 应收
                        source_type="sal_out",
                        source_no=f"SAL_TEST_{suffix}",
                        amount=Decimal("200"),
                        status=1,
                        biz_date="20261101",
                    )
                )
                await session.commit()

            resp = await client.delete(
                f"{API}/base/customers/{customer_a['id']}", headers=headers
            )
            assert resp.status_code == 409, resp.text

            # 移除应收记录后再次删除，应成功软删
            async with AsyncSessionLocal() as session:
                await session.execute(
                    delete(ArApRecord).where(
                        ArApRecord.partner_id == customer_a["id"]
                    )
                )
                await session.commit()
            resp = await client.delete(
                f"{API}/base/customers/{customer_a['id']}", headers=headers
            )
            assert resp.status_code == 200, resp.text

            async with AsyncSessionLocal() as session:
                orm = (
                    await session.execute(
                        select(BdCustomer).where(BdCustomer.id == customer_a["id"])
                    )
                ).scalar_one()
                assert orm.is_deleted is True, "删除应为软删"

            # ------------------------------------------------------------------
            # 7. 信用额度参与订单审核：已发生应收 200 + 本次 200 > 300 被阻断；
            #    额度调到 500 后通过
            # ------------------------------------------------------------------
            code_b = f"CUS_TEST_{suffix}_B"
            resp = await _create_customer(
                client,
                token,
                {
                    "code": code_b,
                    "name": f"测试客户B{suffix}",
                    "creditLimit": 300,
                    "creditDays": 30,
                    "status": 1,
                },
            )
            assert resp.status_code == 200, resp.text
            customer_b = resp.json()["data"]
            created_ids.append(int(customer_b["id"]))

            # 触发一条「修改客户」操作日志（非状态变更）
            resp = await client.put(
                f"{API}/base/customers/{customer_b['id']}",
                headers=headers,
                json={"contact": "测试联系人B"},
            )
            assert resp.status_code == 200, resp.text

            async with AsyncSessionLocal() as session:
                session.add(
                    ArApRecord(
                        tenant_id=1,
                        partner_type=2,
                        partner_id=customer_b["id"],
                        record_type=2,
                        source_type="sal_out",
                        source_no=f"SAL2_TEST_{suffix}",
                        amount=Decimal("200"),
                        status=1,
                        biz_date="20261101",
                    )
                )
                await session.commit()

            # 已发生应收 200 + 本次 200 = 400 > 300 -> 应抛校验异常
            async with AsyncSessionLocal() as session:
                svc = CustomerService(session, tenant_id=1)
                raised = False
                try:
                    await svc.assert_within_credit(customer_b["id"], Decimal("200"))
                except AppException as exc:
                    raised = True
                    assert exc.code == ErrorCode.VALIDATION_ERROR
                assert raised, "超出信用额度时应阻断订单审核"

                # 调高额度至 500 后应通过
                cust = (
                    await session.execute(
                        select(BdCustomer).where(BdCustomer.id == customer_b["id"])
                    )
                ).scalar_one()
                cust.credit_limit = Decimal("500")
                await session.commit()
            async with AsyncSessionLocal() as session:
                svc = CustomerService(session, tenant_id=1)
                # 不应抛异常
                await svc.assert_within_credit(customer_b["id"], Decimal("200"))

            # ------------------------------------------------------------------
            # 8. 账期决定应收到期日：credit_days=30 -> 出库日期 + 30 天
            # ------------------------------------------------------------------
            async with AsyncSessionLocal() as session:
                svc = CustomerService(session, tenant_id=1)
                bill = date(2026, 11, 1)
                due = await svc.calculate_due_date(customer_b["id"], bill)
                assert due == date(2026, 12, 1), f"应收到期日应为 2026-12-01，实际 {due}"

            # ------------------------------------------------------------------
            # 9. 操作日志：新增/修改/停用/启用/删除均有记录
            # ------------------------------------------------------------------
            async with AsyncSessionLocal() as session:
                logs = (
                    await session.execute(
                        select(SysOperLog).where(
                            SysOperLog.module == "基础资料",
                            SysOperLog.action.in_(
                                ["新增客户", "修改客户", "停用客户", "启用客户", "删除客户"]
                            ),
                        )
                    )
                ).scalars().all()
            actions = {log.action for log in logs}
            for expected in ("新增客户", "修改客户", "停用客户", "启用客户", "删除客户"):
                assert expected in actions, f"缺少操作日志：{expected}"
    finally:
        await _cleanup(created_ids)
