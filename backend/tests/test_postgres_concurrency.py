"""PostgreSQL 16 concurrency evidence on a disposable dedicated test database."""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import delete, func, select, text
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

# Imports that read application settings happen only after the opt-in/database-name guard.
from app.core.exceptions import AppException, ErrorCode  # noqa: E402
from app.models.base_data import BdGoods, BdUnit, BdWarehouse  # noqa: E402
from app.models.idempotency import IdempotencyKey  # noqa: E402
from app.models.inventory import InvStock, InvStockFlow  # noqa: E402
from app.models.sys import SysOperLog  # noqa: E402
from app.schemas.goods import GoodsUpdate  # noqa: E402
from app.schemas.inventory import ChangeStockIn, LockStockIn  # noqa: E402
from app.services.goods import GoodsService  # noqa: E402
from app.services.idempotency import IdempotencyService  # noqa: E402
from app.services.inventory import InventoryService  # noqa: E402


SessionFactory = async_sessionmaker[AsyncSession]
Worker = Callable[[AsyncSession], Awaitable[Any]]


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def _factory() -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _create_fixture(
    sessions: SessionFactory,
    *,
    qty: Decimal = Decimal("1"),
    locked_qty: Decimal = Decimal("0"),
    unit_precision: int = 4,
) -> dict[str, int]:
    """Create one random tenant's rows without relying on shared seeded identities."""
    token = uuid.uuid4().hex
    tenant_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
    async with sessions() as db:
        unit = BdUnit(tenant_id=tenant_id, name=f"pg-unit-{token}", precision=unit_precision)
        warehouse = BdWarehouse(
            tenant_id=tenant_id,
            code=f"PGW-{token[:20]}",
            name=f"pg-warehouse-{token}",
        )
        db.add_all([unit, warehouse])
        await db.flush()
        goods = BdGoods(
            tenant_id=tenant_id,
            code=f"PGG-{token[:20]}",
            name=f"pg-goods-{token}",
            unit_id=unit.id,
            stock_upper=Decimal("999999"),
        )
        db.add(goods)
        await db.flush()
        stock = InvStock(
            tenant_id=tenant_id,
            warehouse_id=warehouse.id,
            goods_id=goods.id,
            qty=qty,
            locked_qty=locked_qty,
            available_qty=qty - locked_qty,
            avg_cost=Decimal("1"),
        )
        db.add(stock)
        await db.commit()
        return {
            "tenant_id": tenant_id,
            "unit_id": unit.id,
            "warehouse_id": warehouse.id,
            "goods_id": goods.id,
            "stock_id": stock.id,
        }


async def _cleanup_tenant(sessions: SessionFactory, tenant_id: int) -> None:
    """Delete only rows owned by this test tenant; never drop or truncate shared tables."""
    async with sessions() as db:
        await db.execute(delete(InvStockFlow).where(InvStockFlow.tenant_id == tenant_id))
        await db.execute(delete(InvStock).where(InvStock.tenant_id == tenant_id))
        await db.execute(delete(IdempotencyKey).where(IdempotencyKey.tenant_id == tenant_id))
        await db.execute(delete(SysOperLog).where(SysOperLog.tenant_id == tenant_id))
        await db.execute(delete(BdGoods).where(BdGoods.tenant_id == tenant_id))
        await db.execute(delete(BdWarehouse).where(BdWarehouse.tenant_id == tenant_id))
        await db.execute(delete(BdUnit).where(BdUnit.tenant_id == tenant_id))
        await db.commit()


async def _race(sessions: SessionFactory, *workers: Worker) -> list[Any]:
    """Start workers in distinct sessions after every worker is ready."""
    ready = asyncio.Barrier(len(workers) + 1)

    async def invoke(worker: Worker) -> Any:
        async with sessions() as db:
            await ready.wait()
            return await worker(db)

    tasks = [asyncio.create_task(invoke(worker)) for worker in workers]
    await ready.wait()
    return list(await asyncio.gather(*tasks, return_exceptions=True))


def _stock_change(ids: dict[str, int], *, direction: int, qty: str, source_no: str):
    return ChangeStockIn(
        warehouse_id=ids["warehouse_id"],
        goods_id=ids["goods_id"],
        direction=direction,
        qty=Decimal(qty),
        unit_cost=Decimal("1"),
        biz_type="pg_concurrency",
        source_no=source_no,
        biz_time=datetime(2026, 11, 2),
    )


def test_concurrent_stock_writes_allow_at_most_one_and_keep_flow_consistent() -> None:
    async def scenario() -> None:
        engine, sessions = _factory()
        ids: dict[str, int] | None = None
        try:
            ids = await _create_fixture(sessions)

            async def outbound(db: AsyncSession) -> Any:
                return await InventoryService(db, ids["tenant_id"]).change_stock(
                    _stock_change(ids, direction=-1, qty="1", source_no=uuid.uuid4().hex)
                )

            results = await _race(sessions, outbound, outbound)
            successes = [result for result in results if not isinstance(result, BaseException)]
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(successes) == 1
            assert len(failures) == 1
            assert isinstance(failures[0], AppException)
            assert failures[0].code == ErrorCode.VALIDATION_ERROR

            async with sessions() as db:
                stock = await db.scalar(
                    select(InvStock).where(
                        InvStock.id == ids["stock_id"],
                        InvStock.tenant_id == ids["tenant_id"],
                    )
                )
                flows = (
                    await db.scalars(
                        select(InvStockFlow)
                        .where(InvStockFlow.tenant_id == ids["tenant_id"])
                        .order_by(InvStockFlow.id)
                    )
                ).all()
                assert stock is not None
                assert stock.qty == stock.locked_qty == stock.available_qty == Decimal("0")
                assert stock.qty >= 0 and stock.locked_qty >= 0 and stock.available_qty >= 0
                assert len(flows) == 1
                assert flows[0].before_qty == Decimal("1")
                assert flows[0].after_qty == stock.available_qty
                assert flows[0].direction == -1 and flows[0].qty == Decimal("1")

            lock_ids = await _create_fixture(sessions)
            try:
                payload = LockStockIn(
                    warehouse_id=lock_ids["warehouse_id"],
                    goods_id=lock_ids["goods_id"],
                    qty=Decimal("1"),
                    source_no=uuid.uuid4().hex,
                    biz_time=datetime(2026, 11, 2),
                )

                async def lock(db: AsyncSession) -> Any:
                    return await InventoryService(db, lock_ids["tenant_id"]).lock_stock(payload)

                lock_results = await _race(sessions, lock, lock)
                lock_successes = [
                    result for result in lock_results if not isinstance(result, BaseException)
                ]
                lock_failures = [
                    result for result in lock_results if isinstance(result, BaseException)
                ]
                assert len(lock_successes) == 1
                assert len(lock_failures) == 1
                assert isinstance(lock_failures[0], AppException)
                assert lock_failures[0].code == ErrorCode.VALIDATION_ERROR
                async with sessions() as db:
                    stock = await db.get(InvStock, lock_ids["stock_id"])
                    flow_count = await db.scalar(
                        select(func.count())
                        .select_from(InvStockFlow)
                        .where(InvStockFlow.tenant_id == lock_ids["tenant_id"])
                    )
                    assert stock is not None
                    assert stock.qty == stock.locked_qty == Decimal("1")
                    assert stock.available_qty == Decimal("0")
                    assert flow_count == 1
            finally:
                await _cleanup_tenant(sessions, lock_ids["tenant_id"])
        finally:
            if ids is not None:
                await _cleanup_tenant(sessions, ids["tenant_id"])
            await engine.dispose()

    _run(scenario)


def test_same_idempotency_key_overlaps_and_executes_business_once() -> None:
    async def scenario() -> None:
        engine, sessions = _factory()
        ids: dict[str, int] | None = None
        release_operation = asyncio.Event()
        operation_started = asyncio.Event()
        try:
            ids = await _create_fixture(sessions, qty=Decimal("0"))
            idem_key = uuid.uuid4().hex
            request = _stock_change(ids, direction=1, qty="2", source_no=uuid.uuid4().hex)

            async def submit(db: AsyncSession) -> Any:
                async def operation() -> Any:
                    operation_started.set()
                    await asyncio.wait_for(release_operation.wait(), timeout=5)
                    return await InventoryService(db, ids["tenant_id"]).change_stock(
                        request, commit=False
                    )

                return await IdempotencyService(db).execute(
                    tenant_id=ids["tenant_id"],
                    user_id=ids["tenant_id"],
                    biz_type="pg_same_key",
                    idempotency_key=idem_key,
                    request_body=request,
                    operation=operation,
                )

            ready = asyncio.Barrier(3)

            async def invoke() -> Any:
                async with sessions() as db:
                    await ready.wait()
                    return await submit(db)

            tasks = [asyncio.create_task(invoke()) for _ in range(2)]
            await ready.wait()
            try:
                await asyncio.wait_for(operation_started.wait(), timeout=5)
                await asyncio.sleep(0.2)
            finally:
                release_operation.set()
            results = list(await asyncio.gather(*tasks, return_exceptions=True))
            successes = [result for result in results if not isinstance(result, BaseException)]
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(successes) == 1
            assert len(failures) == 1
            assert isinstance(failures[0], AppException)
            assert failures[0].code == ErrorCode.CONFLICT

            async with sessions() as db:
                stock = await db.get(InvStock, ids["stock_id"])
                flows = await db.scalar(
                    select(func.count())
                    .select_from(InvStockFlow)
                    .where(InvStockFlow.tenant_id == ids["tenant_id"])
                )
                idem_rows = await db.scalar(
                    select(func.count())
                    .select_from(IdempotencyKey)
                    .where(IdempotencyKey.tenant_id == ids["tenant_id"])
                )
                assert stock is not None and stock.qty == stock.available_qty == Decimal("2")
                assert flows == 1 and idem_rows == 1

            async with sessions() as db:
                replay_called = False

                async def must_not_run() -> Any:
                    nonlocal replay_called
                    replay_called = True
                    raise AssertionError("a successful replay must not execute business code")

                replay = await IdempotencyService(db).execute(
                    tenant_id=ids["tenant_id"],
                    user_id=ids["tenant_id"],
                    biz_type="pg_same_key",
                    idempotency_key=idem_key,
                    request_body=request,
                    operation=must_not_run,
                )
                assert replay == successes[0]
                assert not replay_called
        finally:
            release_operation.set()
            if ids is not None:
                await _cleanup_tenant(sessions, ids["tenant_id"])
            await engine.dispose()

    _run(scenario)


def test_goods_delete_and_unit_change_race_stock_write_without_dirty_state_or_deadlock() -> None:
    async def scenario() -> None:
        engine, sessions = _factory()
        tenants: list[int] = []
        try:
            for operation_name in ("delete", "unit_change"):
                ids = await _create_fixture(sessions, qty=Decimal("0"), unit_precision=4)
                tenants.append(ids["tenant_id"])
                if operation_name == "unit_change":
                    async with sessions() as db:
                        whole = BdUnit(
                            tenant_id=ids["tenant_id"],
                            name=f"whole-{uuid.uuid4().hex}",
                            precision=0,
                        )
                        db.add(whole)
                        await db.commit()
                        target_unit_id = whole.id
                else:
                    target_unit_id = -1

                async def stock_write(
                    db: AsyncSession, fixture: dict[str, int] = ids
                ) -> Any:
                    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    return await InventoryService(db, fixture["tenant_id"]).change_stock(
                        _stock_change(
                            fixture,
                            direction=1,
                            qty="0.5",
                            source_no=uuid.uuid4().hex,
                        )
                    )

                async def mutate_goods(
                    db: AsyncSession,
                    fixture: dict[str, int] = ids,
                    action: str = operation_name,
                    new_unit_id: int = target_unit_id,
                ) -> Any:
                    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                    service = GoodsService(db, fixture["tenant_id"])
                    if action == "delete":
                        return await service.delete_goods(fixture["goods_id"])
                    return await service.update_goods(
                        fixture["goods_id"], GoodsUpdate(unit_id=new_unit_id)
                    )

                results = await asyncio.wait_for(
                    _race(sessions, stock_write, mutate_goods), timeout=15
                )
                assert all(
                    not isinstance(result, (asyncio.TimeoutError, TimeoutError))
                    for result in results
                )
                for result in results:
                    if isinstance(result, BaseException):
                        assert isinstance(result, AppException)
                        assert result.code in {
                            ErrorCode.CONFLICT,
                            ErrorCode.NOT_FOUND,
                            ErrorCode.VALIDATION_ERROR,
                        }

                async with sessions() as db:
                    goods = await db.get(BdGoods, ids["goods_id"])
                    stock = await db.get(InvStock, ids["stock_id"])
                    flow_count = await db.scalar(
                        select(func.count())
                        .select_from(InvStockFlow)
                        .where(InvStockFlow.tenant_id == ids["tenant_id"])
                    )
                    assert goods is not None and stock is not None
                    assert stock.qty == stock.available_qty
                    assert stock.locked_qty == Decimal("0")
                    assert stock.qty >= 0 and stock.available_qty >= 0
                    assert flow_count in {0, 1}
                    if operation_name == "delete":
                        if goods.is_deleted:
                            assert stock.qty == Decimal("0") and flow_count == 0
                        else:
                            assert stock.qty == Decimal("0.5") and flow_count == 1
                    elif goods.unit_id == target_unit_id:
                        assert stock.qty == Decimal("0") and flow_count == 0
                    else:
                        assert goods.unit_id == ids["unit_id"]
                        assert stock.qty == Decimal("0.5") and flow_count == 1
        finally:
            for tenant_id in tenants:
                await _cleanup_tenant(sessions, tenant_id)
            await engine.dispose()

    _run(scenario)
