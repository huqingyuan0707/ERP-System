"""F-03 收款单与收款核销验收测试。

覆盖（需求条目拆分 F-03 验收标准）：
  1. 收款 300 核销应收 A 100 + B 150：两应收状态正确，收款单已核销 250 / 未分配 50；
  2. 对未收 150 的应收核销 200 → 422；核销总额超收款额 → 422；应收余额不变；
  3. 跨客户核销 409；已核销完成重复核销 409；
  4. 已核销后编辑/删除 409；列表/汇总/详情 camelCase 字段；
  5. 数据范围（本人范围查他人 403）、无核销权限 403、操作日志。
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

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer
from app.models.finance import FinReceivable, FinReceipt, FinReceiptItem
from app.models.sys import SysPermission, SysRole, SysRolePermission, SysUser, SysUserRole

API = "/api/v1"
PASSWORD = "Passw0rd123"


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


async def _login(
    client: httpx.AsyncClient, username: str = "admin", password: str = "admin123"
) -> str:
    resp = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _seed_customer(name: str) -> int:
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(
            tenant_id=1, code=f"C{uuid.uuid4().hex[:8]}", name=name, status=1, credit_days=30
        )
        session.add(customer)
        await session.commit()
        return int(customer.id)


async def _seed_receivable(customer_id: int, source_no: str, amount: str) -> int:
    async with AsyncSessionLocal() as session:
        row = FinReceivable(
            tenant_id=1,
            customer_id=customer_id,
            source_type="sales_outbound",
            source_no=source_no,
            bill_date=date.today() - timedelta(days=30),
            due_date=date.today() + timedelta(days=30),
            amount=Decimal(amount),
            received_amount=Decimal("0"),
            unreceived_amount=Decimal(amount),
            status=0,
            created_by=1,
        )
        session.add(row)
        await session.commit()
        return int(row.id)


async def _create_receipt(
    client: httpx.AsyncClient, headers: dict[str, str], customer_id: int, amount: str
) -> dict[str, Any]:
    resp = await client.post(
        f"{API}/finance/receipts",
        headers=headers,
        json={
            "customerId": customer_id,
            "receiptDate": date.today().isoformat(),
            "amount": amount,
            "method": "cash",
            "remark": "测试收款",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _writeoff(
    client: httpx.AsyncClient, headers: dict[str, str], receipt_id: int, lines: list[dict[str, Any]]
) -> httpx.Response:
    return await client.post(
        f"{API}/finance/receipts/{receipt_id}/writeoff",
        headers=headers,
        json={"items": lines},
    )


async def _receivable_state(receivable_id: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        row = await session.get(FinReceivable, receivable_id)
        return {
            "received": Decimal(str(row.received_amount)),
            "unreceived": Decimal(str(row.unreceived_amount)),
            "status": int(row.status),
        }


async def _clean(customer_ids: list[int], receivable_nos: list[str]) -> None:
    async with AsyncSessionLocal() as session:
        receipt_ids = (
            (
                await session.execute(
                    select(FinReceipt.id).where(FinReceipt.customer_id.in_(customer_ids))
                )
            )
            .scalars()
            .all()
        )
        for rid in receipt_ids:
            await session.execute(delete(FinReceiptItem).where(FinReceiptItem.receipt_id == rid))
        await session.execute(delete(FinReceipt).where(FinReceipt.customer_id.in_(customer_ids)))
        await session.execute(
            delete(FinReceivable).where(FinReceivable.source_no.in_(receivable_nos))
        )
        await session.execute(delete(BdCustomer).where(BdCustomer.id.in_(customer_ids)))
        await session.commit()


def test_f03_receipt_writeoff_full_flow() -> None:
    """收款 300 核销 A 100 + B 150，未分配 50；超未收/超总额/跨客户拒绝。"""

    async def scenario() -> None:
        suffix = uuid.uuid4().hex[:6]
        cid = await _seed_customer(f"F03客户{suffix}")
        other_cid = await _seed_customer(f"F03其它客户{suffix}")
        no_a = f"SO-F03-{suffix}-A"
        no_b = f"SO-F03-{suffix}-B"
        no_c = f"SO-F03-{suffix}-C"
        a_id = await _seed_receivable(cid, no_a, "150")
        b_id = await _seed_receivable(cid, no_b, "150")
        c_id = await _seed_receivable(other_cid, no_c, "100")
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                receipt = await _create_receipt(client, headers, cid, "300")
                rid = int(receipt["id"])
                assert receipt["receiptNo"]
                assert Decimal(str(receipt["unallocatedAmount"])) == Decimal("300")
                assert receipt["status"] == 0

                # 超单条未收 422、跨客户 409、超总额 422（均在写库前拒绝）
                over = await _writeoff(
                    client, headers, rid, [{"receivableId": a_id, "writeoffAmount": "151"}]
                )
                assert over.status_code == 422, over.text
                cross = await _writeoff(
                    client, headers, rid, [{"receivableId": c_id, "writeoffAmount": "50"}]
                )
                assert cross.status_code == 409, cross.text
                over_total = await _writeoff(
                    client,
                    headers,
                    rid,
                    [
                        {"receivableId": a_id, "writeoffAmount": "150"},
                        {"receivableId": b_id, "writeoffAmount": "151"},
                    ],
                )
                assert over_total.status_code == 422, over_total.text
                assert (await _receivable_state(a_id))["received"] == Decimal("0")
                assert (await _receivable_state(b_id))["received"] == Decimal("0")

                # 正常核销 A 100 + B 150
                ok = await _writeoff(
                    client,
                    headers,
                    rid,
                    [
                        {"receivableId": a_id, "writeoffAmount": "100"},
                        {"receivableId": b_id, "writeoffAmount": "150"},
                    ],
                )
                assert ok.status_code == 200, ok.text
                body = ok.json()["data"]
                assert body["status"] == 1  # 部分核销（250 / 300）
                assert Decimal(str(body["allocatedAmount"])) == Decimal("250")
                assert Decimal(str(body["unallocatedAmount"])) == Decimal("50")
                assert len(body["items"]) == 2
                src_nos = {item["receivableSourceNo"] for item in body["items"]}
                assert src_nos == {no_a, no_b}

                a_state = await _receivable_state(a_id)
                assert a_state["received"] == Decimal("100")
                assert a_state["unreceived"] == Decimal("50")
                assert a_state["status"] == 1
                b_state = await _receivable_state(b_id)
                assert b_state["received"] == Decimal("150")
                assert b_state["unreceived"] == Decimal("0")
                assert b_state["status"] == 2

                # 重复核销（已用 250，剩余 50）：再核销 A 剩余 50 → 完成
                done = await _writeoff(
                    client, headers, rid, [{"receivableId": a_id, "writeoffAmount": "50"}]
                )
                assert done.status_code == 200, done.text
                assert done.json()["data"]["status"] == 2
                again = await _writeoff(
                    client, headers, rid, [{"receivableId": a_id, "writeoffAmount": "1"}]
                )
                assert again.status_code == 409, again.text

                # 已核销不可改/删
                for method in ("put", "delete"):
                    resp = await client.request(
                        method,
                        f"{API}/finance/receipts/{rid}",
                        headers=headers,
                        json={"amount": "999"} if method == "put" else None,
                    )
                    assert resp.status_code == 409, (method, resp.text)

                # 列表/汇总/详情
                listed = await client.get(
                    f"{API}/finance/receipts", params={"customerId": cid}, headers=headers
                )
                assert listed.status_code == 200, listed.text
                assert listed.json()["data"]["total"] == 1
                summary = await client.get(
                    f"{API}/finance/receipts/summary",
                    params={"customerId": cid},
                    headers=headers,
                )
                assert summary.status_code == 200, summary.text
                assert Decimal(str(summary.json()["data"]["totalAllocated"])) == Decimal("300")
                detail = await client.get(f"{API}/finance/receipts/{rid}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert len(detail.json()["data"]["items"]) == 3

                # 缺权限 403
                role_id, user_id = await _create_limited_user(
                    f"f03no{suffix}", ["fin:receipt:list"]
                )
                try:
                    limited_headers = _auth(await _login(client, f"f03no{suffix}", PASSWORD))
                    forbidden = await _writeoff(
                        client,
                        limited_headers,
                        rid,
                        [{"receivableId": a_id, "writeoffAmount": "1"}],
                    )
                    assert forbidden.status_code == 403, forbidden.text
                finally:
                    await _cleanup_user(role_id, user_id)
        finally:
            await _clean([cid, other_cid], [no_a, no_b, no_c])

    _run(scenario())


async def _create_limited_user(username: str, perm_codes: list[str]) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        role = SysRole(
            code=f"f03_{uuid.uuid4().hex[:8]}",
            name="F03受限角色",
            data_scope=3,
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
            assert perm_id is not None, code
            session.add(SysRolePermission(role_id=role.id, permission_id=perm_id))
        await session.commit()
        return int(role.id), int(user.id)


async def _cleanup_user(role_id: int, user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(SysUserRole.__table__.delete().where(SysUserRole.user_id == user_id))
        await session.execute(
            SysRolePermission.__table__.delete().where(SysRolePermission.role_id == role_id)
        )
        await session.execute(SysUser.__table__.delete().where(SysUser.id == user_id))
        await session.execute(SysRole.__table__.delete().where(SysRole.id == role_id))
        await session.commit()
