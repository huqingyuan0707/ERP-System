"""F-01 应收管理验收：只读列表/汇总/账龄/详情与负余额展示口径。

对应 需求条目拆分.md:589-600：
  - 账龄按到期日、只统计正数未结项，负数余额不参与分桶；
  - 未收为负时 creditAmount 给出正数应退/预收金额；
  - 无编辑/删除入口；越权 403、不存在 404。
"""

import asyncio
import uuid
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
from app.models.base_data import BdCustomer
from app.models.finance import FinReceivable
from app.services.receivable import ReceivableService

API = "/api/v1"


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


async def _seed_rows() -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:6]
    overdue_no = f"RO-F01-{suffix}-OVERDUE"
    credit_no = f"RO-F01-{suffix}-CREDIT"
    async with AsyncSessionLocal() as session:
        customer = BdCustomer(tenant_id=1, code=f"F01C{suffix}", name="F01测试客户", status=1)
        session.add(customer)
        await session.flush()
        today = date.today()
        session.add(
            FinReceivable(
                tenant_id=1,
                customer_id=int(customer.id),
                source_type="sales_outbound",
                source_no=overdue_no,
                bill_date=today - timedelta(days=70),
                due_date=today - timedelta(days=40),
                amount=Decimal("500"),
                received_amount=Decimal("0"),
                unreceived_amount=Decimal("500"),
                status=0,
                created_by=1,
            )
        )
        await session.commit()
        return {"code": f"F01C{suffix}", "overdue": overdue_no, "credit": credit_no}


async def _clean_rows(ids: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(FinReceivable).where(
                FinReceivable.source_no.in_([ids["overdue"], ids["credit"]])
            )
        )
        await session.execute(delete(BdCustomer).where(BdCustomer.code == ids["code"]))
        await session.commit()


def test_receivable_list_summary_aging_and_detail() -> None:
    """列表/汇总/账龄/详情返回真实数据；负未收单列应退/预收。"""
    ids = _run(_seed_rows())
    try:

        async def scenario() -> None:
            async with _client() as client:
                headers = _auth(await _login(client))
                prefix = ids["overdue"][: -len("OVERDUE")]

                listed = await client.get(
                    f"{API}/finance/receivables",
                    params={"keyword": prefix, "pageSize": 10},
                    headers=headers,
                )
                assert listed.status_code == 200, listed.text
                body = listed.json()["data"]
                assert body["total"] == 1, body
                overdue = body["items"][0]
                assert overdue["sourceNo"] == ids["overdue"]
                assert overdue["agingBucket"] == "31-60天"
                assert overdue["agingDays"] == 40
                assert Decimal(str(overdue["creditAmount"])) == 0

                summary = await client.get(
                    f"{API}/finance/receivables/summary",
                    params={"keyword": prefix},
                    headers=headers,
                )
                assert summary.status_code == 200, summary.text
                totals = summary.json()["data"]
                assert totals["count"] == 1
                assert Decimal(str(totals["totalUnreceived"])) == Decimal("500")
                assert Decimal(str(totals["overdueAmount"])) == Decimal("500")

                aging = await client.get(
                    f"{API}/finance/receivables/aging",
                    params={"keyword": prefix},
                    headers=headers,
                )
                assert aging.status_code == 200, aging.text
                buckets = {row["bucket"]: row for row in aging.json()["data"]}
                assert Decimal(str(buckets["31-60天"]["amount"])) == Decimal("500")
                assert "应退/预收" not in buckets

                detail = await client.get(
                    f"{API}/finance/receivables/{overdue['id']}", headers=headers
                )
                assert detail.status_code == 200, detail.text
                assert detail.json()["data"]["sourceNo"] == ids["overdue"]
                assert detail.json()["data"]["writeoffs"] == []
                assert detail.json()["data"]["returns"] == []

                missing = await client.get(f"{API}/finance/receivables/99999999", headers=headers)
                assert missing.status_code == 404, missing.text
                for method in ("post", "put", "delete"):
                    resp = await client.request(
                        method, f"{API}/finance/receivables", json={}, headers=headers
                    )
                    assert resp.status_code == 405, (method, resp.text)
                no_auth = await client.get(f"{API}/finance/receivables")
                assert no_auth.status_code == 401, no_auth.status_code

        _run(scenario())
    finally:
        _run(_clean_rows(ids))


def test_negative_balance_is_reported_as_credit() -> None:
    """负未推行：creditAmount 取正数，账龄单列应退/预收，不进正数分桶。"""
    service = ReceivableService(MagicMock(), tenant_id=1)
    today = date.today()
    row = FinReceivable(
        id=99001,
        customer_id=1,
        source_type="sales_outbound",
        source_no="SO-CREDIT",
        bill_date=today - timedelta(days=100),
        due_date=today - timedelta(days=70),
        amount=Decimal("900"),
        received_amount=Decimal("1000"),
        unreceived_amount=Decimal("-100"),
        status=2,
    )
    out = service._to_out(row, None)
    assert Decimal(str(out.credit_amount)) == Decimal("100")
    assert Decimal(str(out.unreceived_amount)) == Decimal("-100")
    assert out.aging_bucket == "应退/预收"
    assert out.aging_days == 70
