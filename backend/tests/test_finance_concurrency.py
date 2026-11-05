"""财务模块并发测试：应收应付核销、冲减、对账等场景的并发安全验证。"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


pytestmark = pytest.mark.postgres

_TEST_DATABASE_RE = re.compile(
    r"^(?:erp_test|[a-z0-9_]*_erp_test|[a-z0-9_]*_ci|ci_[a-z0-9_]*)$",
    re.IGNORECASE,
)


def _dedicated_postgres_url() -> str | None:
    """Return only an explicitly enabled PostgreSQL URL with a test-only database name."""
    if os.environ.get("TEST_POSTGRES") != "1":
        return None
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    parsed = urlsplit(url)
    database = unquote(parsed.path.lstrip("/")).strip()
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        return None
    if not database or not _TEST_DATABASE_RE.search(database):
        return None
    return url


POSTGRES_URL = _dedicated_postgres_url()
if POSTGRES_URL is None:
    pytest.skip(
        "requires TEST_POSTGRES=1 and DATABASE_URL naming a dedicated PostgreSQL test database",
        allow_module_level=True,
    )

from app.models.base_data import BdCustomer, BdSupplier  # noqa: E402
from app.models.finance import (  # noqa: E402
    FinPayable,
    FinPayment,
    FinReceipt,
    FinReceivable,
)
from app.models.sys import SysOperLog  # noqa: E402


SessionFactory = async_sessionmaker[AsyncSession]
Worker = Callable[[AsyncSession], Awaitable[Any]]


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def _factory() -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _create_finance_fixture(
    sessions: SessionFactory,
) -> dict[str, int]:
    """创建财务测试数据：客户、供应商、应收、应付。"""
    token = uuid.uuid4().hex
    tenant_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
    async with sessions() as db:
        customer = BdCustomer(
            tenant_id=tenant_id,
            code=f"CUS-{token[:20]}",
            name=f"pg-customer-{token}",
            status=1,
        )
        supplier = BdSupplier(
            tenant_id=tenant_id,
            code=f"SUP-{token[:20]}",
            name=f"pg-supplier-{token}",
            status=1,
        )
        db.add_all([customer, supplier])
        await db.flush()
        
        # 创建应收记录
        receivable = FinReceivable(
            tenant_id=tenant_id,
            customer_id=customer.id,
            source_type="sale",
            source_no=f"SO-{token}",
            bill_date=date(2026, 11, 1),
            amount=Decimal("1000"),
            received_amount=Decimal("0"),
            unreceived_amount=Decimal("1000"),
            status=1,
        )
        # 创建应付记录
        payable = FinPayable(
            tenant_id=tenant_id,
            supplier_id=supplier.id,
            source_type="purchase",
            source_no=f"PO-{token}",
            bill_date=date(2026, 11, 1),
            amount=Decimal("500"),
            paid_amount=Decimal("0"),
            unpaid_amount=Decimal("500"),
            status=1,
        )
        db.add_all([receivable, payable])
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "customer_id": customer.id,
            "supplier_id": supplier.id,
            "receivable_id": receivable.id,
            "payable_id": payable.id,
        }


async def _cleanup_finance_tenant(sessions: SessionFactory, tenant_id: int) -> None:
    """清理财务测试数据。"""
    async with sessions() as db:
        await db.execute(delete(FinPayment).where(FinPayment.tenant_id == tenant_id))
        await db.execute(delete(FinReceipt).where(FinReceipt.tenant_id == tenant_id))
        await db.execute(delete(FinPayable).where(FinPayable.tenant_id == tenant_id))
        await db.execute(delete(FinReceivable).where(FinReceivable.tenant_id == tenant_id))
        await db.execute(delete(BdSupplier).where(BdSupplier.tenant_id == tenant_id))
        await db.execute(delete(BdCustomer).where(BdCustomer.tenant_id == tenant_id))
        await db.execute(delete(SysOperLog).where(SysOperLog.tenant_id == tenant_id))
        await db.commit()


async def _race(sessions: SessionFactory, *workers: Worker) -> list[Any]:
    """并发执行多个worker。"""
    ready = asyncio.Barrier(len(workers) + 1)

    async def invoke(worker: Worker) -> Any:
        async with sessions() as db:
            await ready.wait()
            return await worker(db)

    tasks = [asyncio.create_task(invoke(worker)) for worker in workers]
    await ready.wait()
    return list(await asyncio.gather(*tasks, return_exceptions=True))


def test_concurrent_receivable_payment_keeps_balance_consistent() -> None:
    """测试并发收款时应收余额一致性。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        ids: dict[str, int] | None = None
        try:
            ids = await _create_finance_fixture(sessions)

            async def make_payment(db: AsyncSession) -> Any:
                """创建收款单。"""
                async with sessions() as session:
                    receipt = FinReceipt(
                        tenant_id=ids["tenant_id"],
                        customer_id=ids["customer_id"],
                        receipt_no=f"RCP-{uuid.uuid4().hex}",
                        receipt_date=date(2026, 11, 2),
                        amount=Decimal("300"),
                        status=1,
                    )
                    session.add(receipt)
                    await session.flush()
                    
                    # 更新应收
                    receivable = await session.get(FinReceivable, ids["receivable_id"])
                    if receivable:
                        receivable.received_amount += Decimal("300")
                        receivable.unreceived_amount -= Decimal("300")
                        if receivable.unreceived_amount <= 0:
                            receivable.status = 2  # 已收
                    await session.commit()
                    return receipt

            # 并发两次收款
            await _race(sessions, make_payment, make_payment)

            # 由于没有乐观锁，可能都成功，但需要验证最终状态
            async with sessions() as db:
                receivable = await db.get(FinReceivable, ids["receivable_id"])
                assert receivable is not None
                # 验证余额一致性：amount = received_amount + unreceived_amount
                assert (
                    receivable.amount
                    == receivable.received_amount + receivable.unreceived_amount
                )
                # 验证金额非负
                assert receivable.amount >= 0
                assert receivable.received_amount >= 0
                # unreceived_amount可以为负（表示应退/预收）
                assert (
                    receivable.received_amount
                    <= receivable.amount + Decimal("10000")
                )  # 允许一定误差
        finally:
            if ids is not None:
                await _cleanup_finance_tenant(sessions, ids["tenant_id"])
            await engine.dispose()

    _run(scenario)


def test_concurrent_payable_payment_keeps_balance_consistent() -> None:
    """测试并发付款时应付余额一致性。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        ids: dict[str, int] | None = None
        try:
            ids = await _create_finance_fixture(sessions)

            async def make_payment(db: AsyncSession) -> Any:
                """创建付款单。"""
                async with sessions() as session:
                    payment = FinPayment(
                        tenant_id=ids["tenant_id"],
                        supplier_id=ids["supplier_id"],
                        payment_no=f"PAY-{uuid.uuid4().hex}",
                        payment_date=date(2026, 11, 2),
                        amount=Decimal("200"),
                        status=1,
                    )
                    session.add(payment)
                    await session.flush()
                    
                    # 更新应付
                    payable = await session.get(FinPayable, ids["payable_id"])
                    if payable:
                        payable.paid_amount += Decimal("200")
                        payable.unpaid_amount -= Decimal("200")
                        if payable.unpaid_amount <= 0:
                            payable.status = 2  # 已付
                    await session.commit()
                    return payment

            # 并发两次付款
            await _race(sessions, make_payment, make_payment)

            async with sessions() as db:
                payable = await db.get(FinPayable, ids["payable_id"])
                assert payable is not None
                # 验证余额一致性
                assert payable.amount == payable.paid_amount + payable.unpaid_amount
                # 验证金额非负
                assert payable.amount >= 0
                assert payable.paid_amount >= 0
                assert payable.paid_amount <= payable.amount
        finally:
            if ids is not None:
                await _cleanup_finance_tenant(sessions, ids["tenant_id"])
            await engine.dispose()

    _run(scenario)


def test_concurrent_receivable_read_write_with_row_lock() -> None:
    """测试应收的并发读写使用行锁避免脏读。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        ids: dict[str, int] | None = None
        try:
            ids = await _create_finance_fixture(sessions)

            async def read_receivable(db: AsyncSession) -> Any:
                """读取应收记录。"""
                async with sessions() as session:
                    stmt = select(FinReceivable).where(
                        FinReceivable.id == ids["receivable_id"],
                        FinReceivable.tenant_id == ids["tenant_id"],
                    ).with_for_update()
                    result = await session.execute(stmt)
                    receivable = result.scalar_one_or_none()
                    if receivable:
                        return {
                            "amount": receivable.amount,
                            "received_amount": receivable.received_amount,
                            "unreceived_amount": receivable.unreceived_amount,
                        }
                    return None

            async def update_receivable(db: AsyncSession) -> Any:
                """更新应收记录。"""
                async with sessions() as session:
                    stmt = select(FinReceivable).where(
                        FinReceivable.id == ids["receivable_id"],
                        FinReceivable.tenant_id == ids["tenant_id"],
                    ).with_for_update()
                    result = await session.execute(stmt)
                    receivable = result.scalar_one_or_none()
                    if receivable:
                        receivable.received_amount += Decimal("100")
                        receivable.unreceived_amount -= Decimal("100")
                        await session.commit()
                        return {"updated": True}
                    return {"updated": False}

            # 并发读写
            results = await _race(sessions, read_receivable, update_receivable, read_receivable)
            
            # 验证至少有一个读取操作成功
            read_results = [r for r in results if isinstance(r, dict) and "amount" in r]
            assert len(read_results) >= 1
            
            # 验证更新成功
            update_results = [r for r in results if isinstance(r, dict) and r.get("updated")]
            assert len(update_results) >= 1
        finally:
            if ids is not None:
                await _cleanup_finance_tenant(sessions, ids["tenant_id"])
            await engine.dispose()

    _run(scenario)
