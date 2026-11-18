"""F-02 应付管理验收：只读列表/汇总/账龄/详情与负余额展示口径。

对应 需求条目拆分.md:602-613：
  - 账龄按到期日、只统计正数未结项，负数余额不参与分桶；
  - 未付为负时 creditAmount 给出正数应退/预付金额（防御性兼容）；
  - 无编辑/删除入口；越权 403、不存在 404。

注意：当前 fin_payable 有 CHECK(paid_amount <= amount)，因此「已付 1000、
退货冲减 100 后未付 -100」尚无法落库；负余额分支以单元级覆盖，
数据库口径修正需单独迁移，不属本轮 F-02 查询范围。
"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import httpx
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.base_data import BdSupplier
from app.models.finance import FinPayable
from app.services.payable import PayableService

API = "/api/v1"
KEY_OVERDUE = "PI-F02-TEST-OVERDUE"


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


async def _seed_rows() -> None:
    async with AsyncSessionLocal() as session:
        supplier = BdSupplier(tenant_id=1, code="F02TEST", name="F02测试供应商", status=1)
        session.add(supplier)
        await session.flush()
        today = date.today()
        session.add(
            FinPayable(
                tenant_id=1,
                supplier_id=int(supplier.id),
                source_type="purchase_inbound",
                source_no=KEY_OVERDUE,
                bill_date=today - timedelta(days=70),
                due_date=today - timedelta(days=40),
                amount=Decimal("500"),
                paid_amount=Decimal("0"),
                unpaid_amount=Decimal("500"),
                status=0,
                created_by=1,
            )
        )
        await session.commit()


async def _clean_rows() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(FinPayable).where(FinPayable.source_no == KEY_OVERDUE))
        await session.execute(delete(BdSupplier).where(BdSupplier.code == "F02TEST"))
        await session.commit()


def test_payable_list_summary_aging_and_readonly() -> None:
    """列表/汇总/账龄/详情返回真实数据；只读端点无写操作入口。"""

    async def scenario() -> None:
        await _seed_rows()
        try:
            async with _client() as client:
                headers = _auth(await _login(client))
                listed = await client.get(
                    f"{API}/finance/payables",
                    params={"keyword": "F02-TEST", "pageSize": 10},
                    headers=headers,
                )
                assert listed.status_code == 200, listed.text
                body = listed.json()["data"]
                assert body["total"] == 1, body
                assert body["pageSize"] == 10
                row = body["items"][0]
                assert row["sourceNo"] == KEY_OVERDUE
                assert row["agingBucket"] == "31-60天"
                assert row["agingDays"] == 40
                assert Decimal(str(row["creditAmount"])) == 0

                summary = await client.get(
                    f"{API}/finance/payables/summary",
                    params={"keyword": "F02-TEST"},
                    headers=headers,
                )
                assert summary.status_code == 200, summary.text
                totals = summary.json()["data"]
                assert totals["count"] == 1
                assert Decimal(str(totals["totalUnpaid"])) == Decimal("500")
                assert Decimal(str(totals["overdueAmount"])) == Decimal("500")
                assert Decimal(str(totals["creditAmount"])) == 0

                aging = await client.get(
                    f"{API}/finance/payables/aging",
                    params={"keyword": "F02-TEST"},
                    headers=headers,
                )
                assert aging.status_code == 200, aging.text
                buckets = {row["bucket"]: row for row in aging.json()["data"]}
                assert "31-60天" in buckets, buckets
                assert Decimal(str(buckets["31-60天"]["amount"])) == Decimal("500")
                assert "应退/预付" not in buckets

                detail = await client.get(f"{API}/finance/payables/{row['id']}", headers=headers)
                assert detail.status_code == 200, detail.text
                assert detail.json()["data"]["sourceNo"] == KEY_OVERDUE
                assert "writeoffs" in detail.json()["data"] and "returns" in detail.json()["data"]

                missing = await client.get(f"{API}/finance/payables/99999999", headers=headers)
                assert missing.status_code == 404, missing.text
                for method in ("post", "put", "delete"):
                    resp = await client.request(
                        method, f"{API}/finance/payables", json={}, headers=headers
                    )
                    assert resp.status_code == 405, (method, resp.text)
        finally:
            await _clean_rows()

    _run(scenario())


def test_negative_balance_is_reported_as_credit() -> None:
    """负未付行：creditAmount 取正数，账龄单列应退/预付，不进正数分桶。"""
    service = PayableService(MagicMock(), tenant_id=1)
    today = date.today()
    row = FinPayable(
        id=99001,  # 未入库对象，手动给主键供出参校验
        supplier_id=1,
        source_type="purchase_inbound",
        source_no="PI-CREDIT",
        bill_date=today - timedelta(days=100),
        due_date=today - timedelta(days=70),
        amount=Decimal("900"),
        paid_amount=Decimal("1000"),
        unpaid_amount=Decimal("-100"),
        status=2,
    )
    out = service._to_out(row, None)
    assert Decimal(str(out.credit_amount)) == Decimal("100")
    assert Decimal(str(out.unpaid_amount)) == Decimal("-100")
    assert out.aging_bucket == "应退/预付"
    assert out.aging_days == 70
