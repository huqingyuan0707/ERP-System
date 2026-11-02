"""商品规则回归：独立内存数据库、局部依赖覆盖，不使用开发数据库。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints.goods import router
from app.core.deps import get_current_tenant, get_db
from app.core.exceptions import AppException, register_exception_handlers
from app.db.base import Base
from app.models.base_data import BdGoods, BdGoodsCategory, BdUnit, BdWarehouse
from app.models.idempotency import IdempotencyKey
from app.models.inventory import InvStock
from app.models.sys import SysOperLog, SysUser
from app.repositories.goods import GoodsCategoryRepository, GoodsRepository, UnitRepository
from app.schemas.goods import (
    GoodsCategoryCreate,
    GoodsCategoryQuery,
    GoodsCategoryUpdate,
    GoodsCreate,
    GoodsQuery,
    GoodsUpdate,
    UnitCreate,
    UnitQuery,
    UnitUpdate,
)
from app.services.goods import GoodsCategoryService, GoodsService, UnitService
from app.services.idempotency import IdempotencyService
from app.services.inventory import InventoryService
from app.utils.numbers import MAX_QUANTITY, validate_quantity


@asynccontextmanager
async def memory_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.add_all(
                [
                    SysUser(id=1, username="operator", password_hash="test", tenant_id=1),
                    SysUser(id=2, username="operator", password_hash="test", tenant_id=2),
                    BdWarehouse(id=1, code="W1", name="一仓", tenant_id=1),
                    BdWarehouse(id=2, code="W2", name="二仓", tenant_id=1),
                    BdWarehouse(id=3, code="W1", name="他租户仓", tenant_id=2),
                ]
            )
            await db.commit()
            yield db
    finally:
        await engine.dispose()


def assert_code(error, code):
    assert error.value.code == code


def capture_selects(db):
    statements = []

    def capture(state):
        if state.is_select:
            statements.append(state.statement)

    event.listen(db.sync_session, "do_orm_execute", capture)
    return statements


def locked_tables(statements):
    return [
        statement.get_final_froms()[0].name
        for statement in statements
        if "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
    ]


@pytest.mark.parametrize(
    "repository,model",
    [
        (GoodsRepository, BdGoods),
        (GoodsCategoryRepository, BdGoodsCategory),
        (UnitRepository, BdUnit),
    ],
)
def test_reference_locks_compile_with_tenant_and_refresh(repository, model):
    async def run():
        async with memory_db() as db:
            statements = capture_selects(db)
            assert await repository(db, 1).get_for_update(999) is None
            statement = statements[-1]
            sql = str(
                statement.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            assert "FOR UPDATE" in sql
            assert f"{model.__tablename__}.tenant_id = 1" in sql
            assert f"{model.__tablename__}.id = 999" in sql
            assert statement.get_execution_options()["populate_existing"] is True

    asyncio.run(run())


@pytest.mark.parametrize(
    "model,repository,values",
    [
        (BdGoods, GoodsRepository, {"code": "G", "name": "商品"}),
        (BdGoodsCategory, GoodsCategoryRepository, {"name": "分类"}),
        (BdUnit, UnitRepository, {"name": "单位"}),
    ],
)
def test_reference_locks_refresh_stale_identity_and_isolate_tenant(model, repository, values):
    async def run():
        async with memory_db() as db:
            entity = model(tenant_id=1, **values)
            db.add(entity)
            await db.commit()
            entity_id = entity.id
            await db.execute(
                update(model)
                .where(model.id == entity_id)
                .values(is_deleted=True)
                .execution_options(synchronize_session=False)
            )
            assert entity.is_deleted is False
            refreshed = await repository(db, 1).get_for_update(entity_id)
            assert refreshed.is_deleted is True
            assert await repository(db, 2).get_for_update(entity_id) is None

    asyncio.run(run())


def test_goods_update_delete_and_inventory_share_ordered_reference_locks():
    async def run():
        async with memory_db() as db:
            category = await GoodsCategoryService(db, 1).create_category(
                GoodsCategoryCreate(name="分类"),
            )
            unit = await UnitService(db, 1).create_unit(UnitCreate(name="单位", precision=4))
            goods = await GoodsService(db, 1).create_goods(
                GoodsCreate(
                    name="商品",
                    category_id=category.id,
                    unit_id=unit.id,
                )
            )
            statements = capture_selects(db)
            await InventoryService(db, 1)._validate_references(1, goods.id, Decimal("1"))
            assert locked_tables(statements) == ["bd_warehouse", "bd_goods", "bd_unit"]
            await db.rollback()
            statements.clear()
            await GoodsService(db, 1).update_goods(
                goods.id, GoodsUpdate(stock_upper="2"), commit=False
            )
            assert locked_tables(statements) == ["bd_goods", "bd_goods_category", "bd_unit"]
            await db.rollback()
            statements.clear()
            await GoodsService(db, 1).delete_goods(goods.id, commit=False)
            assert locked_tables(statements) == ["bd_goods"]
            stock_check = next(
                i
                for i, statement in enumerate(statements)
                if statement.get_final_froms()[0].name == "inv_stock"
            )
            assert "FOR UPDATE" in str(statements[0].compile(dialect=postgresql.dialect()))
            assert stock_check > 0

    asyncio.run(run())


def test_prepare_create_and_reference_deletes_share_locks():
    async def run():
        async with memory_db() as db:
            category = await GoodsCategoryService(db, 1).create_category(
                GoodsCategoryCreate(name="分类"),
            )
            unit = await UnitService(db, 1).create_unit(UnitCreate(name="单位"))
            statements = capture_selects(db)
            prepared = await GoodsService(db, 1).prepare_create(
                GoodsCreate(
                    name="商品",
                    category_id=category.id,
                    unit_id=unit.id,
                ),
                1,
            )
            assert inspect(prepared).transient
            assert locked_tables(statements) == ["bd_goods_category", "bd_unit"]
            await db.rollback()
            statements.clear()
            await GoodsCategoryService(db, 1).delete_category(category.id, commit=False)
            assert locked_tables(statements) == ["bd_goods_category"]
            await db.rollback()
            statements.clear()
            await UnitService(db, 1).delete_unit(unit.id, commit=False)
            assert locked_tables(statements) == ["bd_unit"]

    asyncio.run(run())


def test_precision_reduction_never_locks_goods_after_unit_and_refreshes_thresholds():
    async def run():
        async with memory_db() as db:
            unit = await UnitService(db, 1).create_unit(UnitCreate(name="单位", precision=4))
            goods = await GoodsService(db, 1).create_goods(
                GoodsCreate(name="商品", unit_id=unit.id, stock_upper="2"),
            )
            db.add(
                InvStock(
                    tenant_id=1,
                    warehouse_id=1,
                    goods_id=goods.id,
                    qty=2,
                    locked_qty=1,
                    available_qty=1,
                )
            )
            await db.commit()
            cached_goods = await db.get(BdGoods, goods.id)
            await db.execute(
                update(BdGoods)
                .where(BdGoods.id == goods.id)
                .values(stock_upper=Decimal("2.125"))
                .execution_options(synchronize_session=False)
            )
            statements = capture_selects(db)
            with pytest.raises(AppException) as exc:
                await UnitService(db, 1).update_unit(unit.id, UnitUpdate(precision=0), commit=False)
            assert_code(exc, 409)
            assert cached_goods.stock_upper == Decimal("2.125")
            assert locked_tables(statements) == ["bd_unit"]
            await db.rollback()
            statements.clear()
            await UnitService(db, 1).update_unit(unit.id, UnitUpdate(precision=0), commit=False)
            assert locked_tables(statements) == ["bd_unit"]
            assert any(
                statement.get_final_froms()[0].name == "inv_stock" for statement in statements
            )

    asyncio.run(run())


@pytest.mark.parametrize("schema", [GoodsCreate, GoodsCategoryCreate, UnitCreate])
def test_names_strip_before_validation(schema):
    with pytest.raises(ValidationError):
        schema(name=" \t ")
    assert schema(name=" 名称 ").name == "名称"


@pytest.mark.parametrize(
    "schema,field",
    [
        (GoodsUpdate, "name"),
        (GoodsUpdate, "cost_price"),
        (GoodsUpdate, "sale_price"),
        (GoodsUpdate, "stock_lower"),
        (GoodsUpdate, "stock_upper"),
        (GoodsUpdate, "status"),
        (GoodsCategoryUpdate, "name"),
        (GoodsCategoryUpdate, "sort"),
        (UnitUpdate, "name"),
        (UnitUpdate, "precision"),
    ],
)
def test_explicit_null_is_rejected_but_omission_allowed(schema, field):
    assert schema().model_dump(exclude_unset=True) == {}
    with pytest.raises(ValidationError):
        schema(**{field: None})


@pytest.mark.parametrize(
    "field,value",
    [
        ("cost_price", "NaN"),
        ("sale_price", "Infinity"),
        ("stock_lower", "-Infinity"),
        ("sale_price", "-1"),
        ("cost_price", "0.001"),
        ("cost_price", "10000000000000000"),
        ("stock_upper", "100000000000000"),
        ("stock_upper", "0.00001"),
        ("status", 2),
    ],
)
def test_decimal_and_status_schema_bounds(field, value):
    with pytest.raises(ValidationError):
        GoodsCreate(name="商品", **{field: value})


def test_decimal_capacity_and_trailing_zeros():
    value = GoodsCreate(
        name="商品", cost_price="9999999999999999.9900", stock_upper=str(MAX_QUANTITY)
    )
    assert value.cost_price == Decimal("9999999999999999.99")
    assert isinstance(value.cost_price, Decimal)
    assert GoodsCreate(name="商品", barcode=" \t").barcode is None
    assert GoodsCreate(name="商品", barcode=" ABC ").barcode == "ABC"
    validate_quantity(Decimal("1.0000"), 0)
    validate_quantity(Decimal("1.2300"), 2)
    validate_quantity(Decimal("0E-10000"), 0)
    validate_quantity(MAX_QUANTITY, 4)


@pytest.mark.parametrize(
    "value,precision",
    [
        ("NaN", 4),
        ("Infinity", 4),
        ("-1", 4),
        ("1.0001", 3),
        ("1E-10000", 4),
        ("100000000000000", 4),
        ("1", 5),
    ],
)
def test_shared_quantity_validator_rejects_invalid(value, precision):
    with pytest.raises(AppException) as exc:
        validate_quantity(Decimal(value), precision)
    assert_code(exc, 422)


def test_prepare_create_has_no_persistence_and_checks_unit_precision():
    async def run():
        async with memory_db() as db:
            unit = await UnitService(db, 1).create_unit(UnitCreate(name="个", precision=0))
            goods = await GoodsService(db, 1).prepare_create(
                GoodsCreate(name=" 商品 ", unit_id=unit.id, stock_upper="2.0000"),
                1,
            )
            assert inspect(goods).transient
            assert goods.created_by == goods.updated_by == 1
            assert goods.name == "商品"
            assert await db.scalar(select(func.count()).select_from(BdGoods)) == 0
            with pytest.raises(AppException) as exc:
                await GoodsService(db, 1).prepare_create(
                    GoodsCreate(name="商品", unit_id=unit.id, stock_upper="0.1"),
                    1,
                )
            assert_code(exc, 422)
            with pytest.raises(AppException) as exc:
                await GoodsService(db, 1).prepare_create(
                    GoodsCreate(name="商品", stock_lower="1", stock_upper="0"),
                    1,
                )
            assert_code(exc, 422)

    asyncio.run(run())


def test_repository_tenant_isolation_including_counts_and_joins():
    async def run():
        async with memory_db() as db:
            category1 = await GoodsCategoryService(db, 1).create_category(
                GoodsCategoryCreate(name="类别")
            )
            category2 = await GoodsCategoryService(db, 2).create_category(
                GoodsCategoryCreate(name="类别")
            )
            unit1 = await UnitService(db, 1).create_unit(UnitCreate(name="件"))
            unit2 = await UnitService(db, 2).create_unit(UnitCreate(name="件"))
            first = await GoodsService(db, 1).create_goods(
                GoodsCreate(
                    name="本租户",
                    code="GD0001",
                    barcode="B",
                    category_id=category1.id,
                    unit_id=unit1.id,
                )
            )
            await GoodsService(db, 2).create_goods(
                GoodsCreate(
                    name="其他租户",
                    code="GD9999",
                    barcode="B",
                    category_id=category2.id,
                    unit_id=unit2.id,
                )
            )
            # 模拟历史脏关联，名称不得通过 join 泄露，计数不得统计其他租户。
            db.add_all(
                [
                    BdGoods(
                        tenant_id=1,
                        code="legacy",
                        name="脏关联",
                        category_id=category2.id,
                        unit_id=unit2.id,
                    ),
                    BdGoods(
                        tenant_id=2,
                        code="foreign",
                        name="跨租户引用",
                        category_id=category1.id,
                        unit_id=unit1.id,
                    ),
                    BdGoodsCategory(tenant_id=2, name="跨租户子类", parent_id=category1.id),
                    InvStock(tenant_id=2, warehouse_id=3, goods_id=first.id, qty=1),
                ]
            )
            await db.commit()
            repo = GoodsRepository(db, 1)
            rows, total = await repo.list_goods(GoodsQuery())
            assert total == 2
            assert all(row[0].tenant_id == 1 for row in rows)
            dirty = next(row for row in rows if row[0].code == "legacy")
            assert dirty[1:] == (None, None)
            assert (await repo.get_by_barcode("B")).id == first.id
            assert await repo.get_by_code("GD9999") is None
            assert await repo.max_code("GD") == "GD0001"
            assert not await repo.has_nonzero_stock(first.id)
            assert await repo.total_stock_qty(first.id) == 0
            assert len((await repo.list_goods(GoodsQuery(), limit=10))[0]) == 2
            category_repo = GoodsCategoryRepository(db, 1)
            assert await category_repo.count_goods(category1.id) == 1
            assert await category_repo.count_children(category1.id) == 0
            assert await category_repo.children_count_map() == {}
            assert (await category_repo.goods_count_map())[category1.id] == 1
            unit_repo = UnitRepository(db, 1)
            assert await unit_repo.count_goods(unit1.id) == 1
            assert (await unit_repo.goods_count_map())[unit1.id] == 1
            assert len(await GoodsCategoryService(db, 1).list_categories(GoodsCategoryQuery())) == 1
            assert len(await UnitService(db, 1).list_units(UnitQuery())) == 1
            for data in [{"category_id": category2.id}, {"unit_id": unit2.id}]:
                with pytest.raises(AppException) as exc:
                    await GoodsService(db, 1).prepare_create(
                        GoodsCreate(name="非法关联", **data), 1
                    )
                assert_code(exc, 422)
            with pytest.raises(AppException) as exc:
                await GoodsService(db, 2).get_goods(first.id)
            assert_code(exc, 404)

    asyncio.run(run())


def test_soft_deleted_names_codes_reserved_barcodes_reusable():
    async def run():
        async with memory_db() as db:
            service = GoodsService(db, 1)
            goods = await service.create_goods(GoodsCreate(name="商品", code="OLD", barcode=" B "))
            with pytest.raises(AppException) as exc:
                await service.create_goods(GoodsCreate(name="重复条码", barcode="B"))
            assert_code(exc, 409)
            await service.delete_goods(goods.id)
            with pytest.raises(AppException) as exc:
                await service.create_goods(GoodsCreate(name="重复编码", code=" OLD "))
            assert_code(exc, 409)
            await service.create_goods(GoodsCreate(name="条码重用", barcode="B"))
            for name in ["无条码1", "无条码2"]:
                await service.create_goods(GoodsCreate(name=name, barcode="  "))
            categories = GoodsCategoryService(db, 1)
            category = await categories.create_category(GoodsCategoryCreate(name="类别"))
            await categories.delete_category(category.id)
            with pytest.raises(AppException) as exc:
                await categories.create_category(GoodsCategoryCreate(name=" 类别 "))
            assert_code(exc, 409)
            units = UnitService(db, 1)
            unit = await units.create_unit(UnitCreate(name="单位"))
            await units.delete_unit(unit.id)
            with pytest.raises(AppException) as exc:
                await units.create_unit(UnitCreate(name=" 单位 "))
            assert_code(exc, 409)

    asyncio.run(run())


def test_root_category_and_barcode_database_unique_guards():
    async def run():
        async with memory_db() as db:
            for entities in [
                [BdGoodsCategory(tenant_id=1, name="根") for _ in range(2)],
                [BdGoods(tenant_id=1, name="商品", code=f"C{i}", barcode="B") for i in range(2)],
            ]:
                db.add_all(entities)
                with pytest.raises(IntegrityError):
                    await db.flush()
                await db.rollback()

    asyncio.run(run())


def test_category_prevents_self_descendant_and_existing_cycle():
    async def run():
        async with memory_db() as db:
            service = GoodsCategoryService(db, 1)
            root = await service.create_category(GoodsCategoryCreate(name="根"))
            child = await service.create_category(GoodsCategoryCreate(name="子", parent_id=root.id))
            for parent in [root.id, child.id]:
                with pytest.raises(AppException) as exc:
                    await service.update_category(root.id, GoodsCategoryUpdate(parent_id=parent))
                assert_code(exc, 422)
            await service.update_category(
                child.id, GoodsCategoryUpdate(name=" 改名 ", parent_id=None)
            )
            assert (await service.get_category(child.id)).name == "改名"
            a = await db.get(BdGoodsCategory, root.id)
            b = await db.get(BdGoodsCategory, child.id)
            a.parent_id = b.id
            b.parent_id = a.id
            await db.commit()
            with pytest.raises(AppException) as exc:
                await service.create_category(GoodsCategoryCreate(name="挂入循环", parent_id=a.id))
            assert_code(exc, 422)

    asyncio.run(run())


@pytest.mark.parametrize("quantities", [[(1, 0), (-1, 0)], [(0, 1)], [(-1, 0)]])
def test_delete_checks_each_warehouse_qty_and_locked(quantities):
    async def run():
        async with memory_db() as db:
            service = GoodsService(db, 1)
            goods = await service.create_goods(GoodsCreate(name="商品"))
            for index, (qty, locked) in enumerate(quantities):
                db.add(
                    InvStock(
                        tenant_id=1,
                        warehouse_id=index + 1,
                        goods_id=goods.id,
                        qty=qty,
                        locked_qty=locked,
                        available_qty=qty - locked,
                    )
                )
            await db.commit()
            with pytest.raises(AppException) as exc:
                await service.delete_goods(goods.id)
            assert_code(exc, 409)

    asyncio.run(run())


def test_precision_reduction_and_unit_switch_validate_existing_stock():
    async def run():
        async with memory_db() as db:
            units = UnitService(db, 1)
            fine = await units.create_unit(UnitCreate(name="公斤", precision=4))
            whole = await units.create_unit(UnitCreate(name="件", precision=0))
            goods_service = GoodsService(db, 1)
            goods = await goods_service.create_goods(
                GoodsCreate(name="商品", unit_id=fine.id, stock_upper=10)
            )
            stock = InvStock(
                tenant_id=1,
                warehouse_id=1,
                goods_id=goods.id,
                qty=Decimal("1.25"),
                locked_qty=Decimal("0.25"),
                available_qty=1,
            )
            db.add(stock)
            await db.commit()
            for operation in [
                lambda: units.update_unit(fine.id, UnitUpdate(precision=0)),
                lambda: goods_service.update_goods(goods.id, GoodsUpdate(unit_id=whole.id)),
            ]:
                with pytest.raises(AppException) as exc:
                    await operation()
                assert_code(exc, 409)
            stock.qty = 2
            stock.locked_qty = 1
            await db.commit()
            await units.update_unit(fine.id, UnitUpdate(precision=0))
            await goods_service.update_goods(goods.id, GoodsUpdate(unit_id=whole.id))
            assert (await goods_service.get_goods(goods.id)).unit_id == whole.id
            await goods_service.update_goods(
                goods.id, GoodsUpdate(unit_id=None, stock_upper="10.1234")
            )
            assert (await goods_service.get_goods(goods.id)).stock_upper == Decimal("10.1234")

    asyncio.run(run())


def test_numeric_auto_code_ignores_custom_and_other_tenant_codes():
    async def run():
        async with memory_db() as db:
            db.add_all(
                [
                    BdGoods(tenant_id=1, code=code, name=code)
                    for code in ["GD9999", "GD10000", "GDcustom"]
                ]
                + [BdGoods(tenant_id=2, code="GD999999", name="其他租户")]
            )
            await db.commit()
            result = await GoodsService(db, 1).create_goods(GoodsCreate(name="自动编码"))
            assert result.code == "GD10001"

    asyncio.run(run())


def test_idempotency_atomic_replay_conflict_retry_expiry_and_audit():
    async def run():
        async with memory_db() as db:
            service = IdempotencyService(db)
            payload = GoodsCreate(name="商品", cost_price="1.20")

            async def operation():
                return await GoodsService(db, 1).create_goods(payload, 1, commit=False)

            options = {
                "tenant_id": 1,
                "user_id": 1,
                "biz_type": "create_goods",
                "request_body": payload,
                "operation": operation,
                "idempotency_key": "key",
            }
            first = await service.execute(**options)
            assert first == await service.execute(**options)
            assert first["costPrice"] == 1.2
            assert isinstance(first["costPrice"], float)
            assert await db.scalar(select(func.count()).select_from(BdGoods)) == 1
            assert await db.scalar(select(func.count()).select_from(SysOperLog)) == 1
            entity = await db.scalar(select(BdGoods))
            assert entity.created_by == entity.updated_by == 1
            log = await db.scalar(select(SysOperLog))
            assert log.tenant_id == 1 and log.user_id == 1
            with pytest.raises(AppException) as exc:
                await service.execute(**{**options, "request_body": GoodsCreate(name="不同参数")})
            assert_code(exc, 409)
            for _ in range(2):
                await service.execute(**{**options, "idempotency_key": None})
            assert await db.scalar(select(func.count()).select_from(BdGoods)) == 3

            async def failing():
                await GoodsService(db, 1).create_goods(GoodsCreate(name="应回滚"), 1, commit=False)
                raise RuntimeError("failure after business and audit flush")

            with pytest.raises(RuntimeError):
                await service.execute(
                    **{**options, "idempotency_key": "retry", "operation": failing}
                )
            assert await db.scalar(select(func.count()).select_from(BdGoods)) == 3
            assert await db.scalar(select(func.count()).select_from(SysOperLog)) == 3
            assert (
                await service._repo.get_by_key(service.build_key(1, 1, "create_goods", "retry"))
                is None
            )
            await service.execute(**{**options, "idempotency_key": "retry"})
            record = await service._repo.get_by_key(service.build_key(1, 1, "create_goods", "key"))
            record.status = 2
            await db.commit()
            with pytest.raises(AppException) as exc:
                await service.execute(**options)
            assert_code(exc, 409)
            record = await service._repo.get_by_key(service.build_key(1, 1, "create_goods", "key"))
            record.status = 3
            await db.commit()
            await service.execute(**options)
            record = await service._repo.get_by_key(service.build_key(1, 1, "create_goods", "key"))
            record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
            replay_after_expiry = await service.execute(**options)
            assert replay_after_expiry["id"] != first["id"]

    asyncio.run(run())


def test_idempotency_race_returns_409_without_running_business(monkeypatch):
    async def run():
        async with memory_db() as db:
            service = IdempotencyService(db)

            async def collision(**kwargs):
                raise IntegrityError("insert", {}, Exception("unique"))

            async def operation():
                pytest.fail("business must not execute when registration loses")

            monkeypatch.setattr(service._repo, "create", collision)
            with pytest.raises(AppException) as exc:
                await service.execute(
                    tenant_id=1,
                    user_id=1,
                    biz_type="test",
                    idempotency_key="race",
                    request_body={},
                    operation=operation,
                )
            assert_code(exc, 409)

    asyncio.run(run())


def test_http_endpoints_null_normalization_idempotency_and_audit():
    async def run():
        async with memory_db() as db:
            app = FastAPI()
            app.include_router(router)
            register_exception_handlers(app)
            user = SysUser(id=1, tenant_id=1, username="operator", password_hash="test")

            async def session_override():
                yield db

            app.dependency_overrides[get_db] = session_override
            app.dependency_overrides[get_current_tenant] = lambda: 1
            for route in router.routes:
                if hasattr(route, "dependant"):
                    for dependency in route.dependant.dependencies:
                        if getattr(dependency.call, "__name__", "") == "_checker":
                            app.dependency_overrides[dependency.call] = lambda: user
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                first = await client.post(
                    "/base/goods",
                    json={"name": " A ", "barcode": " B "},
                    headers={"Idempotency-Key": "first"},
                )
                assert first.status_code == 200, first.text
                replay = await client.post(
                    "/base/goods",
                    json={"name": " A ", "barcode": " B "},
                    headers={"Idempotency-Key": "first"},
                )
                assert replay.json() == first.json()
                assert first.json()["data"]["name"] == "A"
                goods_id = first.json()["data"]["id"]
                for data in [{"name": None}, {"costPrice": None}, {"name": "  "}, {"status": 8}]:
                    response = await client.put(f"/base/goods/{goods_id}", json=data)
                    assert response.status_code == 422, response.text
                for name in ["B", "C"]:
                    assert (
                        await client.post("/base/goods", json={"name": name})
                    ).status_code == 200
                assert (
                    await client.post("/base/goods", json={"name": "重复", "barcode": " B "})
                ).status_code == 409
                assert (
                    await client.put(
                        f"/base/goods/{goods_id}", json={"barcode": None, "categoryId": None}
                    )
                ).status_code == 200
                assert (await client.delete(f"/base/goods/{goods_id}")).status_code == 200
                assert await db.scalar(select(func.count()).select_from(SysOperLog)) == 5
                assert await db.scalar(select(func.count()).select_from(IdempotencyKey)) == 1

    asyncio.run(run())
