"""I-01/I-02 库存与流水导出：导出条数与列表一致且筛选条件生效。"""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.base_data import BdGoods, BdUnit, BdWarehouse
from app.models.inventory import InvStock, InvStockFlow
from app.schemas.task import FlowExportQuery, StockExportQuery
from app.services.excel_export import ExcelExportService


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@asynccontextmanager
async def _memory_db() -> AsyncIterator[Any]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all(
            [
                BdUnit(id=1, tenant_id=1, name="件", precision=0),
                BdWarehouse(id=1, tenant_id=1, code="WH1", name="主仓"),
                BdWarehouse(id=2, tenant_id=1, code="WH2", name="次仓"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                BdGoods(id=1, tenant_id=1, code="G1", name="商品一", unit_id=1, stock_lower=10),
                BdGoods(id=2, tenant_id=1, code="G2", name="商品二", unit_id=1, stock_lower=10),
            ]
        )
        await db.flush()
        db.add_all(
            [
                InvStock(
                    id=1,
                    tenant_id=1,
                    warehouse_id=1,
                    goods_id=1,
                    qty=Decimal("100"),
                    locked_qty=Decimal("10"),
                    available_qty=Decimal("90"),
                    avg_cost=Decimal("2"),
                ),
                InvStock(
                    id=2,
                    tenant_id=1,
                    warehouse_id=2,
                    goods_id=2,
                    qty=Decimal("5"),
                    locked_qty=Decimal("0"),
                    available_qty=Decimal("5"),
                    avg_cost=Decimal("1"),
                ),
            ]
        )
        await db.flush()
        now = datetime.now(UTC)
        db.add_all(
            [
                InvStockFlow(
                    id=1,
                    tenant_id=1,
                    warehouse_id=1,
                    goods_id=1,
                    flow_type="purchase_in",
                    direction=1,
                    qty=Decimal("100"),
                    before_qty=Decimal("0"),
                    after_qty=Decimal("100"),
                    unit_cost=Decimal("2"),
                    source_no="PI-1",
                    biz_time=now - timedelta(days=1),
                ),
                InvStockFlow(
                    id=2,
                    tenant_id=1,
                    warehouse_id=1,
                    goods_id=1,
                    flow_type="sale_out",
                    direction=-1,
                    qty=Decimal("10"),
                    before_qty=Decimal("100"),
                    after_qty=Decimal("90"),
                    unit_cost=Decimal("2"),
                    source_no="OUT-1",
                    biz_time=now,
                ),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


def _row_count(path: str) -> int:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        # 第一行表头，其余为数据行
        return (
            sum(1 for row in sheet.iter_rows(values_only=True) if any(v is not None for v in row))
            - 1
        )
    finally:
        workbook.close()


def test_stock_export_matches_query_filters() -> None:
    """库存导出条数与列表一致，belowLower/仓库筛选生效。"""

    async def scenario() -> None:
        async with _memory_db() as db:
            service = ExcelExportService(db, tenant_id=1)

            all_task = await service.export_stock_report(11, StockExportQuery(page=1, pageSize=20))
            assert all_task.status == 3 and all_task.file_path
            assert _row_count(all_task.file_path) == 2

            low_task = await service.export_stock_report(
                11, StockExportQuery(page=1, pageSize=20, below_lower=True)
            )
            assert low_task.status == 3 and low_task.file_path
            assert _row_count(low_task.file_path) == 1

            warehouse_task = await service.export_stock_report(
                11, StockExportQuery(page=1, pageSize=20, warehouse_id=1)
            )
            assert warehouse_task.status == 3 and warehouse_task.file_path
            assert _row_count(warehouse_task.file_path) == 1

    _run(scenario())


def test_flow_export_matches_query_filters() -> None:
    """流水导出条数与筛选一致，方向/来源单号生效。"""

    async def scenario() -> None:
        async with _memory_db() as db:
            service = ExcelExportService(db, tenant_id=1)

            all_task = await service.export_flow_report(11, FlowExportQuery(page=1, pageSize=20))
            assert all_task.status == 3 and all_task.file_path
            assert _row_count(all_task.file_path) == 2

            out_task = await service.export_flow_report(
                11, FlowExportQuery(page=1, pageSize=20, direction=-1)
            )
            assert out_task.status == 3 and out_task.file_path
            assert _row_count(out_task.file_path) == 1

            source_task = await service.export_flow_report(
                11, FlowExportQuery(page=1, pageSize=20, source_no="PI-1")
            )
            assert source_task.status == 3 and source_task.file_path
            assert _row_count(source_task.file_path) == 1

    _run(scenario())
