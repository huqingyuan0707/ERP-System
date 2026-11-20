"""F-06 供应商对账单验收：期初按业务日期重算、快照不可变、重复 409。

对应 需求条目拆分.md:654-666 与 E2E-1 第 6 步。
"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdSupplier, BdWarehouse
from app.models.finance import FinPayable, FinPayment, FinPaymentItem, FinStatement
from app.models.pur import PurInbound, PurOrder, PurReturn

API = "/api/v1"
SUPPLIER_CODE = "F06TEST"
IN_NO_OLD = "PI-F06-OLD"
IN_NO_NEW = "PI-F06-NEW"
RET_NO = "PR-F06"
PAY_NO = "FK-F06"
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


async def _seed() -> int:
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(tenant_id=1, code=SUPPLIER_CODE, name="F06测试供应商", status=1)
        warehouse = BdWarehouse(tenant_id=1, code="F06W", name="F06测试仓库", status=1)
        session.add_all([supplier, warehouse])
        await session.flush()
        sid = int(supplier.id)
        wid = int(warehouse.id)

        order = PurOrder(
            tenant_id=1,
            supplier_id=sid,
            order_no="PO-F06-TEST",
            order_date=TODAY - timedelta(days=45),
            total_amount=Decimal("1000"),
            tax_amount=Decimal("0"),
            status=2,
            created_by=1,
        )
        session.add(order)
        await session.flush()

        # 期初之前：入库 400（应付原始金额）
        old = PurInbound(
            tenant_id=1,
            supplier_id=sid,
            warehouse_id=wid,
            order_id=order.id,
            inbound_no=IN_NO_OLD,
            inbound_date=TODAY - timedelta(days=40),
            total_amount=Decimal("400"),
            tax_amount=Decimal("0"),
            status=1,
            created_by=1,
        )
        # 本期：入库 600
        current = PurInbound(
            tenant_id=1,
            supplier_id=sid,
            warehouse_id=wid,
            order_id=order.id,
            inbound_no=IN_NO_NEW,
            inbound_date=TODAY - timedelta(days=3),
            total_amount=Decimal("600"),
            tax_amount=Decimal("0"),
            status=1,
            created_by=1,
        )
        session.add_all([old, current])
        await session.flush()

        # 期初前应付 400；本期应付 600（模拟已被退货冲减，但对账按原始入库重算）
        session.add_all(
            [
                FinPayable(
                    tenant_id=1,
                    supplier_id=sid,
                    source_type="purchase_inbound",
                    source_no=IN_NO_OLD,
                    bill_date=old.inbound_date,
                    due_date=old.inbound_date + timedelta(days=30),
                    amount=Decimal("400"),
                    paid_amount=Decimal("0"),
                    unpaid_amount=Decimal("400"),
                    status=0,
                    created_by=1,
                ),
                FinPayable(
                    tenant_id=1,
                    supplier_id=sid,
                    source_type="purchase_inbound",
                    source_no=IN_NO_NEW,
                    bill_date=current.inbound_date,
                    due_date=current.inbound_date + timedelta(days=30),
                    amount=Decimal("600"),
                    paid_amount=Decimal("0"),
                    unpaid_amount=Decimal("600"),
                    status=0,
                    created_by=1,
                ),
            ]
        )

        # 期初前付款核销 100（期初 = 400 - 100 = 300）
        payment = FinPayment(
            tenant_id=1,
            supplier_id=sid,
            payment_no=PAY_NO,
            payment_date=TODAY - timedelta(days=35),
            amount=Decimal("100"),
            allocated_amount=Decimal("100"),
            unallocated_amount=Decimal("0"),
            created_by=1,
        )
        session.add(payment)
        await session.flush()
        payable_old_id = await session.scalar(
            select(FinPayable.id).where(FinPayable.source_no == IN_NO_OLD)
        )
        session.add(
            FinPaymentItem(
                tenant_id=1,
                payment_id=payment.id,
                payable_id=int(payable_old_id),
                writeoff_amount=Decimal("100"),
                writeoff_at=datetime.combine(
                    TODAY - timedelta(days=35), datetime.min.time(), tzinfo=timezone.utc
                ),
            )
        )

        # 本期退货 50（按 return_date 归集）
        session.add(
            PurReturn(
                tenant_id=1,
                supplier_id=sid,
                warehouse_id=wid,
                return_no=RET_NO,
                source_no=IN_NO_NEW,
                return_date=TODAY - timedelta(days=2),
                total_amount=Decimal("50"),
                status=1,
                created_by=1,
            )
        )
        await session.commit()
        return sid


async def _clean() -> None:
    async with AsyncSessionLocal() as session:
        statement_ids = (await session.execute(select(FinStatement.id))).scalars().all()
        for sid in statement_ids:
            await session.execute(delete(FinStatement).where(FinStatement.id == sid))
        payment_id = await session.scalar(
            select(FinPayment.id).where(FinPayment.payment_no == PAY_NO)
        )
        if payment_id is not None:
            await session.execute(
                delete(FinPaymentItem).where(FinPaymentItem.payment_id == payment_id)
            )
        await session.execute(delete(FinPayment).where(FinPayment.payment_no == PAY_NO))
        await session.execute(delete(PurReturn).where(PurReturn.return_no == RET_NO))
        await session.execute(
            delete(FinPayable).where(FinPayable.source_no.in_([IN_NO_OLD, IN_NO_NEW]))
        )
        await session.execute(
            delete(PurInbound).where(PurInbound.inbound_no.in_([IN_NO_OLD, IN_NO_NEW]))
        )
        await session.execute(delete(PurOrder).where(PurOrder.order_no == "PO-F06-TEST"))
        await session.execute(delete(BdWarehouse).where(BdWarehouse.code == "F06W"))
        await session.execute(delete(BdSupplier).where(BdSupplier.code == SUPPLIER_CODE))
        await session.commit()


def test_statement_supplier_begin_current_end_and_snapshot() -> None:
    """期初 300（400-100）+ 本期 550（600-50）= 期末 850；重复生成 409。"""

    async def scenario() -> None:
        sid = await _seed()
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                payload = {
                    "partyType": 2,
                    "partyId": sid,
                    "periodStart": (TODAY - timedelta(days=7)).isoformat(),
                    "periodEnd": TODAY.isoformat(),
                    "remark": "F06 验收",
                }

                preview = await client.post(
                    f"{API}/finance/statements/preview", json=payload, headers=headers
                )
                assert preview.status_code == 200, preview.text
                data = preview.json()["data"]
                assert Decimal(str(data["beginAmount"])) == Decimal("300")
                assert Decimal(str(data["currentAmount"])) == Decimal("550")
                assert Decimal(str(data["endAmount"])) == Decimal("850")
                assert len(data["items"]) == 2
                assert all(not item["sourceNo"].startswith("PI-F06-OLD") for item in data["items"])

                created = await client.post(
                    f"{API}/finance/statements", json=payload, headers=headers
                )
                assert created.status_code == 200, created.text
                stmt = created.json()["data"]
                statement_id = stmt["id"]
                assert stmt["statementNo"]
                assert Decimal(str(stmt["endAmount"])) == Decimal("850")
                assert len(stmt["items"]) == 2

                detail = await client.get(
                    f"{API}/finance/statements/{statement_id}", headers=headers
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["data"]["items"] == stmt["items"]

                duplicate = await client.post(
                    f"{API}/finance/statements", json=payload, headers=headers
                )
                assert duplicate.status_code == 409, duplicate.text

                listed = await client.get(
                    f"{API}/finance/statements",
                    params={"partyId": sid, "periodStart": TODAY.isoformat()},
                    headers=headers,
                )
                assert listed.status_code == 200, listed.text
                assert listed.json()["data"]["total"] == 1

                exported = await client.get(
                    f"{API}/finance/statements/{statement_id}/export", headers=headers
                )
                assert exported.status_code == 200, exported.text
        finally:
            await _clean()

    _run(scenario())
