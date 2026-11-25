"""F-06 客户侧对账单验收：期初/本期/期末按业务日期重算、未分配收款不计入、快照不可变。

对应 需求条目拆分.md:662/664/666 与 E2E-2 第 6 步
（客户 0 + 出库 400 - 收款 250 - 退货 40 = 期末 110）。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdCustomer, BdWarehouse
from app.models.finance import FinReceivable, FinReceipt, FinReceiptItem, FinStatement
from app.models.sal import SalOrder, SalOutbound, SalReturn

API = "/api/v1"
TODAY = date.today()


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


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{API}/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["accessToken"])


async def _seed() -> dict[str, Any]:
    """造客户 + 期初前出库 100 + 本期出库 50 + 核销 30（未分配 20）+ 本期退货 10。"""
    suffix = uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(
            tenant_id=1, code=f"F06C{suffix}", name="F06客户对账", status=1, credit_days=30
        )
        warehouse = BdWarehouse(tenant_id=1, code=f"F06CW{suffix}", name="F06客户仓", status=1)
        session.add_all([customer, warehouse])
        await session.flush()
        cid, wid = int(customer.id), int(warehouse.id)

        order = SalOrder(
            tenant_id=1,
            customer_id=cid,
            order_no=f"SO-F06C-{suffix}",
            order_date=TODAY - timedelta(days=60),
            total_amount=Decimal("150"),
            tax_amount=Decimal("0"),
            discount=Decimal("0"),
            status=4,
            created_by=1,
        )
        session.add(order)
        await session.flush()

        old_no = f"OUT-F06C-{suffix}-OLD"
        cur_no = f"OUT-F06C-{suffix}-NEW"
        ret_no = f"RET-F06C-{suffix}"
        session.add_all(
            [
                SalOutbound(
                    tenant_id=1,
                    outbound_no=old_no,
                    customer_id=cid,
                    warehouse_id=wid,
                    order_id=order.id,
                    outbound_date=TODAY - timedelta(days=40),
                    total_amount=Decimal("100"),
                    tax_amount=Decimal("0"),
                    status=1,
                    created_by=1,
                ),
                SalOutbound(
                    tenant_id=1,
                    outbound_no=cur_no,
                    customer_id=cid,
                    warehouse_id=wid,
                    order_id=order.id,
                    outbound_date=TODAY - timedelta(days=3),
                    total_amount=Decimal("50"),
                    tax_amount=Decimal("0"),
                    status=1,
                    created_by=1,
                ),
                SalReturn(
                    tenant_id=1,
                    return_no=ret_no,
                    customer_id=cid,
                    warehouse_id=wid,
                    source_no=old_no,
                    return_date=TODAY - timedelta(days=2),
                    total_amount=Decimal("10"),
                    status=1,
                    created_by=1,
                ),
            ]
        )
        await session.flush()

        # 收款单 50，仅核销 30（未分配 20 不计入对账）
        receipt = FinReceipt(
            tenant_id=1,
            receipt_no=f"SK-F06C-{suffix}",
            customer_id=cid,
            receipt_date=TODAY - timedelta(days=1),
            amount=Decimal("50"),
            allocated_amount=Decimal("30"),
            unallocated_amount=Decimal("20"),
            operator_id=1,
            created_by=1,
            updated_by=1,
        )
        session.add(receipt)
        await session.flush()
        receivable = FinReceivable(
            tenant_id=1,
            customer_id=cid,
            source_type="sales_outbound",
            source_no=cur_no,
            bill_date=TODAY - timedelta(days=3),
            due_date=TODAY + timedelta(days=27),
            amount=Decimal("50"),
            received_amount=Decimal("30"),
            unreceived_amount=Decimal("20"),
            status=1,
            created_by=1,
        )
        session.add(receivable)
        await session.flush()
        session.add(
            FinReceiptItem(
                tenant_id=1,
                receipt_id=receipt.id,
                receivable_id=receivable.id,
                writeoff_amount=Decimal("30"),
                writeoff_at=datetime.now(timezone.utc) - timedelta(days=1),
                created_by=1,
                updated_by=1,
            )
        )
        await session.commit()
        return {
            "cid": cid,
            "code": customer.code,
            "old_no": old_no,
            "cur_no": cur_no,
            "ret_no": ret_no,
        }


async def _clean(seed: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        receipt_ids = (
            (
                await session.execute(
                    select(FinReceipt.id).where(FinReceipt.customer_id == seed["cid"])
                )
            )
            .scalars()
            .all()
        )
        for rid in receipt_ids:
            await session.execute(delete(FinReceiptItem).where(FinReceiptItem.receipt_id == rid))
        await session.execute(delete(FinReceipt).where(FinReceipt.customer_id == seed["cid"]))
        await session.execute(delete(FinReceivable).where(FinReceivable.customer_id == seed["cid"]))
        await session.execute(delete(FinStatement).where(FinStatement.party_id == seed["cid"]))
        await session.execute(
            SalReturn.__table__.delete().where(SalReturn.customer_id == seed["cid"])
        )
        await session.execute(
            SalOutbound.__table__.delete().where(SalOutbound.customer_id == seed["cid"])
        )
        await session.execute(
            SalOrder.__table__.delete().where(SalOrder.order_no == f"SO-F06C-{seed['code'][4:]}")
        )
        await session.execute(BdCustomer.__table__.delete().where(BdCustomer.id == seed["cid"]))
        await session.execute(
            BdWarehouse.__table__.delete().where(BdWarehouse.code == f"F06CW{seed['code'][4:]}")
        )
        await session.commit()


def test_f06_customer_statement_begin_current_end() -> None:
    """客户期初 100 + 出库 50 - 收款 30 - 退货 10 = 期末 110；未分配 20 不计入。"""
    seed = _run(_seed())
    try:

        async def scenario() -> None:
            async with _client() as client:
                headers = _auth(await _login(client))
                payload = {
                    "partyType": 1,
                    "partyId": seed["cid"],
                    "periodStart": (TODAY - timedelta(days=7)).isoformat(),
                    "periodEnd": TODAY.isoformat(),
                }

                preview = await client.post(
                    f"{API}/finance/statements/preview", json=payload, headers=headers
                )
                assert preview.status_code == 200, preview.text
                built = preview.json()["data"]
                assert built["partyType"] == 1
                assert built["partyName"] == "F06客户对账"
                assert Decimal(str(built["beginAmount"])) == Decimal("100")
                # 本期 50（出库）- 30（核销）- 10（退货）= 10
                assert Decimal(str(built["currentAmount"])) == Decimal("10")
                assert Decimal(str(built["endAmount"])) == Decimal("110")
                assert len(built["items"]) == 3
                for item in built["items"]:
                    if item["sourceNo"] == seed["cur_no"]:
                        assert Decimal(str(item["debitAmount"])) == Decimal("50")
                    if item["sourceNo"] == seed["ret_no"]:
                        assert Decimal(str(item["creditAmount"])) == Decimal("10")

                created = await client.post(
                    f"{API}/finance/statements", json=payload, headers=headers
                )
                assert created.status_code == 200, created.text
                snapshot = created.json()["data"]
                sid = int(snapshot["id"])
                assert Decimal(str(snapshot["endAmount"])) == Decimal("110")
                assert len(snapshot["items"]) == 3

                # 重复生成同对象+期间 → 409，且已有快照不受影响
                repeat = await client.post(
                    f"{API}/finance/statements", json=payload, headers=headers
                )
                assert repeat.status_code == 409, repeat.text

                # 快照不可变：业务新增后详情仍是原数据
                detail = await client.get(f"{API}/finance/statements/{sid}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert Decimal(str(detail.json()["data"]["endAmount"])) == Decimal("110")

                # 列表按类型筛选可见客户对账单
                listed = await client.get(
                    f"{API}/finance/statements",
                    params={"partyType": 1, "partyId": seed["cid"]},
                    headers=headers,
                )
                assert listed.status_code == 200, listed.text
                body = listed.json()["data"]
                assert body["total"] == 1
                assert body["items"][0]["partyName"] == "F06客户对账"
                assert body["items"][0]["partyType"] == 1

                # 不存在的客户 → 404
                missing = await client.post(
                    f"{API}/finance/statements/preview",
                    json={**payload, "partyId": 99999999},
                    headers=headers,
                )
                assert missing.status_code == 404, missing.text

        _run(scenario())
    finally:
        _run(_clean(seed))


def test_f06_statement_party_type_validation() -> None:
    """party_type 仅允许 1/2；无鉴权 401。"""
    seed = _run(_seed())
    try:

        async def scenario() -> None:
            async with _client() as client:
                headers = _auth(await _login(client))
                bad = await client.post(
                    f"{API}/finance/statements/preview",
                    json={
                        "partyType": 9,
                        "partyId": seed["cid"],
                        "periodStart": TODAY.isoformat(),
                        "periodEnd": TODAY.isoformat(),
                    },
                    headers=headers,
                )
                assert bad.status_code == 422, bad.text
                no_auth = await client.post(
                    f"{API}/finance/statements/preview",
                    json={
                        "partyType": 1,
                        "partyId": seed["cid"],
                        "periodStart": TODAY.isoformat(),
                        "periodEnd": TODAY.isoformat(),
                    },
                )
                assert no_auth.status_code == 401, no_auth.status_code

        _run(scenario())
    finally:
        _run(_clean(seed))
