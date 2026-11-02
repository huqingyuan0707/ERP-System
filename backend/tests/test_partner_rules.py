"""B03-B05 独立内存规则测试，不连接开发库。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import customer, supplier, warehouse
from app.core.deps import get_current_user, get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.db.base import Base
from app.models.base_data import BdWarehouse
from app.models.finance import ArApRecord
from app.models.sys import SysOperLog, SysUser
from app.schemas.config import ConfigUpdateItem
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate
from app.services.config import ConfigService
from app.services.customer import CustomerService
from app.services.idempotency import IdempotencyService
from app.services.supplier import SupplierService
from app.services.warehouse import WarehouseService


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
                SysUser(
                    id=1,
                    tenant_id=1,
                    username="operator",
                    password_hash="test",
                    real_name="本租户用户",
                ),
                SysUser(
                    id=2,
                    tenant_id=2,
                    username="other",
                    password_hash="test",
                    real_name="其他租户用户",
                ),
                SysUser(
                    id=3, tenant_id=1, username="deleted", password_hash="test", is_deleted=True
                ),
            ]
        )
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.parametrize(
    "schema,field",
    [(CustomerUpdate, field) for field in ("code", "name", "creditLimit", "creditDays", "status")]
    + [(SupplierUpdate, field) for field in ("code", "name", "creditDays", "status")],
)
def test_partner_update_rejects_explicit_null(schema, field):
    with pytest.raises(ValidationError):
        schema.model_validate({field: None})
    assert schema().model_dump(exclude_unset=True) == {}
    assert schema.model_validate({"address": None, "remark": None}).model_dump(
        exclude_unset=True
    ) == {
        "address": None,
        "remark": None,
    }


@pytest.mark.parametrize(
    "schema,field",
    [(CustomerCreate, field) for field in ("name", "creditLimit", "creditDays", "status")]
    + [(SupplierCreate, field) for field in ("name", "creditDays", "status")],
)
def test_partner_create_rejects_explicit_null(schema, field):
    with pytest.raises(ValidationError):
        schema.model_validate({"name": "资料", field: None})


def test_partner_addresses_tenant_and_due_date():
    async def scenario():
        async with memory_db() as db:
            for service_type, create_type, update_type, noun in [
                (CustomerService, CustomerCreate, CustomerUpdate, "customer"),
                (SupplierService, SupplierCreate, SupplierUpdate, "supplier"),
            ]:
                service = service_type(db, 1)
                entity = await getattr(service, f"create_{noun}")(
                    create_type(name="独立字段", address="地址", remark="备注", credit_days=5), 1
                )
                assert entity.address == "地址" and entity.remark == "备注"
                entity = await getattr(service, f"update_{noun}")(
                    entity.id, update_type(name="改名"), 1
                )
                assert entity.address == "地址" and entity.remark == "备注"
                entity = await getattr(service, f"update_{noun}")(
                    entity.id, update_type(address=None), 1
                )
                assert entity.address is None and entity.remark == "备注"
                assert await service.calculate_due_date(entity.id, date(2026, 11, 1)) == date(
                    2026, 11, 6
                )
                with pytest.raises(AppException) as error:
                    await getattr(service_type(db, 2), f"get_{noun}")(entity.id)
                assert error.value.code == 404
            logs = (await db.scalars(select(SysOperLog))).all()
            assert len(logs) == 6 and all(log.tenant_id == 1 for log in logs)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["block", "warn", "off"])
def test_credit_zero_unlimited_and_mode_results(mode):
    async def scenario():
        async with memory_db() as db:
            service = CustomerService(db, 1)
            customer_out = await service.create_customer(CustomerCreate(name="额度"), 1)
            customer_id = customer_out.id
            db.add(
                ArApRecord(
                    tenant_id=1,
                    partner_type=2,
                    partner_id=customer_id,
                    record_type=2,
                    source_type="test",
                    source_no="AR-1",
                    amount=Decimal("80"),
                    biz_date="20261101",
                )
            )
            db.add(
                ArApRecord(
                    tenant_id=2,
                    partner_type=2,
                    partner_id=customer_id,
                    record_type=2,
                    source_type="test",
                    source_no="AR-2",
                    amount=Decimal("9000"),
                    biz_date="20261101",
                )
            )
            await db.commit()
            with patch.object(
                ConfigService, "get_value", new=AsyncMock(return_value=mode)
            ) as config:
                summary = await service.get_credit_summary(customer_id)
                config.assert_awaited_with("sales.credit.mode", "block")
                assert (
                    summary.unlimited
                    and summary.available_credit is None
                    and not summary.over_limit
                )
                assert summary.receivable_amount == Decimal("80")
                result = await service.assert_within_credit(customer_id, Decimal("999999"))
                assert result.allowed and not result.over_limit and result.warning is None
                await service.update_customer(customer_id, CustomerUpdate(credit_limit=100), 1)
                summary = await service.get_credit_summary(customer_id)
                assert not summary.unlimited and summary.available_credit == Decimal("20")
                assert (
                    await service.assert_within_credit(customer_id, Decimal("20"))
                ).warning is None
                if mode == "block":
                    with pytest.raises(AppException) as error:
                        await service.assert_within_credit(customer_id, Decimal("21"))
                    assert error.value.code == 422
                else:
                    result = await service.assert_within_credit(customer_id, Decimal("21"))
                    assert result.allowed and result.over_limit
                    assert bool(result.warning) == (mode == "warn")

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["", "BLOCK", "invalid"])
def test_config_rejects_invalid_credit_mode_before_writes(mode):
    async def scenario():
        db = AsyncMock()
        service = ConfigService(db)
        with pytest.raises(AppException) as error:
            await service.batch_update([ConfigUpdateItem(key="sales.credit.mode", value=mode)])
        assert error.value.code == 422
        db.commit.assert_not_awaited()
        db.execute.assert_not_awaited()

    asyncio.run(scenario())


def test_warehouse_manager_isolation_and_default_audit():
    async def scenario():
        async with memory_db() as db:
            service = WarehouseService(db, 1)
            for manager_id in (2, 3, 999):
                with pytest.raises(AppException) as error:
                    await service.create_warehouse(
                        WarehouseCreate(name="非法负责人", manager_id=manager_id), 1
                    )
                assert error.value.code == 422
            options = await service.list_manager_options()
            assert [option.value for option in options] == [1]
            first = await service.create_warehouse(
                WarehouseCreate(name="第一仓", manager_id=1, is_default=True, remark="保留备注"), 1
            )
            assert first.manager_name == "本租户用户" and first.remark == "保留备注"
            second = await service.create_warehouse(
                WarehouseCreate(name="第二仓", is_default=True), 1
            )
            assert not (await service.get_warehouse(first.id)).is_default
            assert (
                await service.update_warehouse(first.id, WarehouseUpdate(name="改名"), 1)
            ).remark == "保留备注"
            log = await db.scalar(
                select(SysOperLog)
                .where(SysOperLog.action == "切换默认仓")
                .order_by(SysOperLog.id.desc())
            )
            assert json.loads(log.params) == {
                "warehouseId": second.id,
                "previousDefaultWarehouseIds": [first.id],
                "defaultWarehouseId": second.id,
            }
            bad = BdWarehouse(tenant_id=1, code="LEGACY", name="历史错误关联", manager_id=2)
            db.add(bad)
            await db.commit()
            assert (await service.get_warehouse(bad.id)).manager_name is None

    asyncio.run(scenario())


def test_warehouse_idempotency_retry_and_atomic_rollback():
    async def scenario():
        async with memory_db() as db:
            service = WarehouseService(db, 1)
            idem = IdempotencyService(db)
            payload = WarehouseCreate(name="幂等仓", remark="remark")

            async def operation():
                return await service.create_warehouse(payload, 1, commit=False)

            kwargs = {
                "tenant_id": 1,
                "user_id": 1,
                "biz_type": "create_warehouse",
                "idempotency_key": "one",
                "request_body": payload,
                "operation": operation,
            }
            first = await idem.execute(**kwargs)
            assert await idem.execute(**kwargs) == first
            assert await db.scalar(select(func.count()).select_from(BdWarehouse)) == 1
            assert await db.scalar(select(func.count()).select_from(SysOperLog)) == 1
            with pytest.raises(AppException) as error:
                await idem.execute(**{**kwargs, "request_body": {"name": "changed"}})
            assert error.value.code == 409
            await idem.execute(**{**kwargs, "idempotency_key": None})
            assert await db.scalar(select(func.count()).select_from(BdWarehouse)) == 2
            with (
                patch(
                    "app.services.warehouse.write_oper_log",
                    new=AsyncMock(side_effect=RuntimeError("audit failed")),
                ),
                pytest.raises(RuntimeError),
            ):
                await idem.execute(**{**kwargs, "idempotency_key": "retry"})
            assert await db.scalar(select(func.count()).select_from(BdWarehouse)) == 2
            await idem.execute(**{**kwargs, "idempotency_key": "retry"})
            assert await db.scalar(select(func.count()).select_from(BdWarehouse)) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "noun,router",
    [
        ("warehouse", warehouse.router),
        ("customer", customer.router),
        ("supplier", supplier.router),
    ],
)
def test_partner_http_crud_and_explicit_idempotency(noun, router):
    async def scenario():
        async with memory_db() as db:
            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(router)

            async def current_user():
                return await db.get(SysUser, 1)

            async def database():
                yield db

            app.dependency_overrides[get_current_user] = current_user
            app.dependency_overrides[get_db] = database
            with patch(
                "app.core.deps.AuthService.get_permission_codes", new=AsyncMock(return_value=["*"])
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    path = f"/base/{noun}s"
                    payload = {"name": "API资料", "address": "地址", "remark": "备注"}
                    first = await client.post(
                        path, json=payload, headers={"Idempotency-Key": "create"}
                    )
                    assert first.status_code == 200, first.text
                    repeated = await client.post(
                        path, json=payload, headers={"Idempotency-Key": "create"}
                    )
                    assert repeated.json() == first.json()
                    other = await client.post(path, json=payload)
                    assert (
                        other.status_code == 200
                        and other.json()["data"]["id"] != first.json()["data"]["id"]
                    )
                    entity_path = f"{path}/{first.json()['data']['id']}"
                    changed = await client.put(
                        entity_path, json={"name": "改名"}, headers={"Idempotency-Key": "edit"}
                    )
                    assert changed.status_code == 200, changed.text
                    assert changed.json()["data"]["address"] == "地址"
                    assert changed.json()["data"]["remark"] == "备注"
                    cleared = await client.put(entity_path, json={"address": None})
                    assert cleared.status_code == 200 and cleared.json()["data"]["address"] is None
                    assert cleared.json()["data"]["remark"] == "备注"
                    removed = await client.delete(
                        entity_path, headers={"Idempotency-Key": "delete"}
                    )
                    assert removed.status_code == 200
                    repeated = await client.delete(
                        entity_path, headers={"Idempotency-Key": "delete"}
                    )
                    assert repeated.status_code == 200 and repeated.json()["data"] is None
                    assert (await client.get(entity_path)).status_code == 404

    asyncio.run(scenario())


def test_manager_options_warehouse_permission_and_partner_http_422():
    async def scenario():
        async with memory_db() as db:
            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(warehouse.router)
            app.include_router(customer.router)
            app.include_router(supplier.router)
            user = await db.get(SysUser, 1)

            async def current_user():
                return user

            async def database():
                yield db

            app.dependency_overrides[get_current_user] = current_user
            app.dependency_overrides[get_db] = database
            permissions = ["base:warehouse:list", "base:customer:edit", "base:supplier:edit"]
            with patch(
                "app.core.deps.AuthService.get_permission_codes",
                new=AsyncMock(return_value=permissions),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get("/base/warehouses/manager-options")
                    assert response.status_code == 200 and response.json()["data"] == [
                        {"value": 1, "label": "本租户用户"}
                    ]
                    for noun in ("customers", "suppliers"):
                        response = await client.put(f"/base/{noun}/1", json={"status": None})
                        assert response.status_code == 422
                    permissions.clear()
                    response = await client.get("/base/warehouses/manager-options")
                    assert response.status_code == 403

    asyncio.run(scenario())
