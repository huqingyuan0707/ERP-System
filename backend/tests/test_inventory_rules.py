"""库存隔离、精度、停用与事务防护；每个场景独立 SQLite 内存库。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import inventory as inventory_endpoint
from app.core.deps import get_current_tenant, get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.db.base import Base
from app.models.base_data import BdGoods, BdUnit, BdWarehouse
from app.models.idempotency import IdempotencyKey
from app.models.inventory import InvStock, InvStockFlow
from app.models.sys import SysUser
from app.schemas.inventory import ChangeStockIn, LockStockIn, UnlockStockIn
from app.services.idempotency import IdempotencyService
from app.services.inventory import InventoryService


@asynccontextmanager
async def memory_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add_all(
            [
                BdUnit(id=1, tenant_id=1, name="整数单位", precision=0),
                BdUnit(id=2, tenant_id=2, name="异租户单位", precision=2),
                BdUnit(id=3, tenant_id=1, name="删除单位", precision=2, is_deleted=True),
                BdWarehouse(id=1, tenant_id=1, code="WH1", name="本租户仓"),
                BdWarehouse(id=2, tenant_id=2, code="WH2", name="异租户仓"),
                BdWarehouse(id=3, tenant_id=1, code="WH3", name="停用仓", status=0),
                BdWarehouse(id=4, tenant_id=1, code="WH4", name="删除仓", is_deleted=True),
            ]
        )
        await db.flush()
        db.add_all(
            [
                BdGoods(id=1, tenant_id=1, code="G1", name="整数商品", unit_id=1),
                BdGoods(id=2, tenant_id=2, code="G2", name="异租户商品"),
                BdGoods(id=3, tenant_id=1, code="G3", name="停用商品", status=0),
                BdGoods(id=4, tenant_id=1, code="G4", name="删除商品", is_deleted=True),
                BdGoods(id=5, tenant_id=1, code="G5", name="错误单位", unit_id=2),
                BdGoods(id=6, tenant_id=1, code="G6", name="删除单位", unit_id=3),
                BdGoods(id=7, tenant_id=1, code="G7", name="无单位"),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


def payload(op, warehouse_id=1, goods_id=1, qty="1"):
    data = {
        "warehouse_id": warehouse_id,
        "goods_id": goods_id,
        "qty": Decimal(qty),
        "biz_time": datetime(2026, 11, 1),
    }
    if op == "change":
        return ChangeStockIn(
            **data, direction=1, biz_type="purchase_in", source_no="TEST", unit_cost=2
        )
    return (LockStockIn if op == "lock" else UnlockStockIn)(**data)


@pytest.mark.parametrize("op", ["change", "lock", "unlock"])
@pytest.mark.parametrize(
    "warehouse_id,goods_id", [(2, 1), (4, 1), (999, 1), (1, 2), (1, 4), (1, 5), (1, 6), (1, 999)]
)
def test_rejects_cross_tenant_deleted_and_invalid_references(op, warehouse_id, goods_id):
    async def scenario():
        async with memory_db() as db:
            service = InventoryService(db, 1)
            with pytest.raises(AppException) as error:
                await getattr(service, f"{op}_stock")(payload(op, warehouse_id, goods_id))
            assert error.value.code == 422
            assert await db.scalar(select(func.count()).select_from(InvStock)) == 0
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("op", ["change", "lock"])
@pytest.mark.parametrize("warehouse_id,goods_id", [(3, 1), (1, 3)])
def test_disabled_references_reject_new_writes(op, warehouse_id, goods_id):
    async def scenario():
        async with memory_db() as db:
            with pytest.raises(AppException) as error:
                await getattr(InventoryService(db, 1), f"{op}_stock")(
                    payload(op, warehouse_id, goods_id)
                )
            assert error.value.code == 422
            assert await db.scalar(select(func.count()).select_from(InvStock)) == 0

    asyncio.run(scenario())


def test_disabled_references_allow_historical_unlock():
    async def scenario():
        async with memory_db() as db:
            db.add(
                InvStock(
                    tenant_id=1,
                    warehouse_id=3,
                    goods_id=3,
                    qty=5,
                    locked_qty=3,
                    available_qty=2,
                    avg_cost=2,
                )
            )
            await db.commit()
            result = await InventoryService(db, 1).unlock_stock(payload("unlock", 3, 3))
            assert result.locked_qty == 2 and result.available_qty == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("op", ["change", "lock", "unlock"])
@pytest.mark.parametrize("goods_id,qty", [(1, "1.1"), (7, "1.00001")])
def test_quantity_precision_checked_before_stock_creation(op, goods_id, qty):
    async def scenario():
        async with memory_db() as db:
            with pytest.raises(AppException) as error:
                await getattr(InventoryService(db, 1), f"{op}_stock")(
                    payload(op, goods_id=goods_id, qty=qty)
                )
            assert error.value.code == 422
            assert await db.scalar(select(func.count()).select_from(InvStock)) == 0

    asyncio.run(scenario())


def test_trailing_zero_and_default_four_decimal_precision():
    async def scenario():
        async with memory_db() as db:
            service = InventoryService(db, 1)
            result = await service.change_stock(payload("change", qty="3.0000"))
            assert result.qty == Decimal("3")
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 1
            locked = await service.lock_stock(payload("lock", qty="1.0000"))
            assert locked.available_qty == 2
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 1
            released = await service.unlock_stock(payload("unlock", qty="1.0000"))
            assert released.locked_qty == 0
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 1
            result = await service.change_stock(payload("change", goods_id=7, qty="0.12340"))
            assert result.qty == Decimal("0.1234")

    asyncio.run(scenario())


def test_stock_queries_and_flows_are_tenant_scoped():
    async def scenario():
        async with memory_db() as db:
            db.add(
                InvStock(
                    tenant_id=2,
                    warehouse_id=1,
                    goods_id=1,
                    qty=90,
                    locked_qty=10,
                    available_qty=80,
                    avg_cost=1,
                )
            )
            await db.commit()
            service = InventoryService(db, 1)
            with pytest.raises(AppException):
                await service.lock_stock(payload("lock"))
            with pytest.raises(AppException):
                await service.unlock_stock(payload("unlock"))
            own = await service.change_stock(payload("change"))
            assert own.qty == 1
            other = await db.scalar(select(InvStock).where(InvStock.tenant_id == 2))
            assert other.qty == 90 and other.locked_qty == 10
            flows = (await db.scalars(select(InvStockFlow))).all()
            assert len(flows) == 1 and flows[0].tenant_id == 1

    asyncio.run(scenario())


def test_idempotency_cache_failure_rolls_back_stock_and_flow():
    async def scenario():
        async with memory_db() as db:
            service = InventoryService(db, 1)
            idem = IdempotencyService(db)
            request = payload("change", qty="2")

            async def operation():
                return await service.change_stock(request, commit=False)

            args = {
                "tenant_id": 1,
                "user_id": 1,
                "biz_type": "change_stock",
                "idempotency_key": "test",
                "request_body": request,
                "operation": operation,
            }
            with (
                patch.object(
                    idem, "confirm_success", new=AsyncMock(side_effect=RuntimeError("cache failed"))
                ),
                pytest.raises(RuntimeError),
            ):
                await idem.execute(**args)
            assert await db.scalar(select(func.count()).select_from(InvStock)) == 0
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 0
            first = await idem.execute(**args)
            assert await idem.execute(**args) == first
            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 1
            stock = await db.scalar(select(InvStock).where(InvStock.tenant_id == 1))
            assert stock.qty == 2

    asyncio.run(scenario())


def test_business_type_direction_and_unit_cost_validation():
    directions = {
        "purchase_in": 1,
        "purchase_return": -1,
        "sale_out": -1,
        "sale_return": 1,
        "transfer_in": 1,
        "transfer_out": -1,
        "check_gain": 1,
        "check_loss": -1,
    }
    base = {
        "warehouse_id": 1,
        "goods_id": 1,
        "qty": 1,
        "source_no": "test",
        "biz_time": datetime(2026, 11, 1),
    }
    for biz_type, direction in directions.items():
        assert ChangeStockIn(**base, biz_type=biz_type, direction=direction).direction == direction
        with pytest.raises(ValidationError):
            ChangeStockIn(**base, biz_type=biz_type, direction=-direction)
    for invalid in ("other", "test_in"):
        with pytest.raises(ValidationError):
            ChangeStockIn(**base, biz_type=invalid, direction=1)
    for invalid_cost in ("NaN", "Infinity", "-0.0001", "0.00001", "100000000000000"):
        with pytest.raises(ValidationError):
            ChangeStockIn(
                **base,
                biz_type="purchase_in",
                direction=1,
                unit_cost=invalid_cost,
            )


def test_outbound_flow_uses_ledger_quantities_and_existing_average_cost():
    async def scenario():
        async with memory_db() as db:
            db.add(
                InvStock(
                    tenant_id=1,
                    warehouse_id=1,
                    goods_id=1,
                    qty=10,
                    locked_qty=2,
                    available_qty=8,
                    avg_cost=3,
                )
            )
            await db.commit()
            request = ChangeStockIn(
                warehouse_id=1,
                goods_id=1,
                direction=-1,
                qty=3,
                unit_cost=99,
                biz_type="sale_out",
                source_no="SALE-1",
                biz_time=datetime(2026, 11, 1),
            )
            result = await InventoryService(db, 1).change_stock(request)
            flow = await db.scalar(select(InvStockFlow))
            assert result.qty == 7
            assert result.locked_qty == 2
            assert result.available_qty == 5 == result.qty - result.locked_qty
            assert result.avg_cost == 3
            assert flow.before_qty == 10 and flow.after_qty == 7
            assert flow.unit_cost == 3 and flow.direction == -1

    asyncio.run(scenario())


def test_direction_zero_rejected_and_weighted_cost_uses_before_quantity():
    with pytest.raises(ValidationError):
        ChangeStockIn(
            warehouse_id=1,
            goods_id=1,
            direction=0,
            qty=1,
            biz_type="purchase_in",
            source_no="test",
            biz_time=datetime(2026, 11, 1),
        )

    async def scenario():
        async with memory_db() as db:
            service = InventoryService(db, 1)
            await service.change_stock(payload("change", qty="10"))
            request = payload("change", qty="10").model_copy(
                update={"unit_cost": Decimal("4")}
            )
            result = await service.change_stock(request)
            assert result.qty == 20 and result.avg_cost == Decimal("3")

    asyncio.run(scenario())


def test_inventory_http_requires_key_and_scopes_it_to_tenant_not_user():
    async def scenario():
        async with memory_db() as db:
            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(inventory_endpoint.router)
            current = {"user": SysUser(id=11, tenant_id=1, username="one", password_hash="test")}

            async def database():
                yield db

            app.dependency_overrides[get_db] = database
            app.dependency_overrides[get_current_tenant] = lambda: 1
            for route in inventory_endpoint.router.routes:
                if hasattr(route, "dependant"):
                    for dependency in route.dependant.dependencies:
                        if getattr(dependency.call, "__name__", "") == "_checker":
                            app.dependency_overrides[dependency.call] = lambda: current["user"]

            request = {
                "warehouseId": 1,
                "goodsId": 1,
                "direction": 1,
                "qty": 2,
                "unitCost": 4,
                "bizType": "purchase_in",
                "sourceNo": "PO-1",
                "bizTime": "20261101",
            }
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                missing = await client.post("/inventory/stocks/change", json=request)
                assert missing.status_code == 422
                first = await client.post(
                    "/inventory/stocks/change",
                    json=request,
                    headers={"Idempotency-Key": "tenant-command-1"},
                )
                assert first.status_code == 200, first.text
                current["user"] = SysUser(
                    id=12, tenant_id=1, username="two", password_hash="test"
                )
                replay = await client.post(
                    "/inventory/stocks/change",
                    json=request,
                    headers={"Idempotency-Key": "tenant-command-1"},
                )
                assert replay.json() == first.json()
                conflict = await client.post(
                    "/inventory/stocks/change",
                    json={**request, "qty": 3},
                    headers={"Idempotency-Key": "tenant-command-1"},
                )
                assert conflict.status_code == 409

            assert await db.scalar(select(func.count()).select_from(InvStockFlow)) == 1
            key = await db.scalar(select(IdempotencyKey))
            assert key.user_id == 0

    asyncio.run(scenario())
