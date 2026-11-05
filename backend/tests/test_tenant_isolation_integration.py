"""多租户隔离集成测试：验证跨租户数据隔离的完整性。"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import Awaitable, Callable
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

from app.core.exceptions import AppException, ErrorCode  # noqa: E402
from app.models.base_data import BdCustomer, BdSupplier, BdGoods, BdWarehouse  # noqa: E402
from app.models.inventory import InvStock  # noqa: E402
from app.models.sys import SysUser  # noqa: E402
from app.repositories.customer import CustomerRepository  # noqa: E402
from app.repositories.supplier import SupplierRepository  # noqa: E402
from app.repositories.inventory import InventoryRepository  # noqa: E402

SessionFactory = async_sessionmaker[AsyncSession]


def _run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(scenario())


def _factory() -> tuple[AsyncEngine, SessionFactory]:
    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _create_tenant_data(
    sessions: SessionFactory,
    tenant_id: int,
    prefix: str,
) -> dict[str, int]:
    """为指定租户创建测试数据。"""
    async with sessions() as db:
        customer = BdCustomer(
            tenant_id=tenant_id,
            code=f"CUS-{prefix}",
            name=f"customer-{prefix}",
            status=1,
        )
        supplier = BdSupplier(
            tenant_id=tenant_id,
            code=f"SUP-{prefix}",
            name=f"supplier-{prefix}",
            status=1,
        )
        warehouse = BdWarehouse(
            tenant_id=tenant_id,
            code=f"WH-{prefix}",
            name=f"warehouse-{prefix}",
            status=1,
        )
        goods = BdGoods(
            tenant_id=tenant_id,
            code=f"GOODS-{prefix}",
            name=f"goods-{prefix}",
            stock_upper=Decimal("999999"),
            status=1,
        )
        db.add_all([customer, supplier, warehouse, goods])
        await db.flush()
        
        stock = InvStock(
            tenant_id=tenant_id,
            warehouse_id=warehouse.id,
            goods_id=goods.id,
            qty=Decimal("100"),
            locked_qty=Decimal("0"),
            available_qty=Decimal("100"),
            avg_cost=Decimal("10"),
        )
        db.add(stock)
        await db.commit()
        
        return {
            "tenant_id": tenant_id,
            "customer_id": customer.id,
            "supplier_id": supplier.id,
            "warehouse_id": warehouse.id,
            "goods_id": goods.id,
            "stock_id": stock.id,
        }


async def _cleanup_tenant(sessions: SessionFactory, tenant_id: int) -> None:
    """清理指定租户的数据。"""
    async with sessions() as db:
        await db.execute(delete(InvStock).where(InvStock.tenant_id == tenant_id))
        await db.execute(delete(BdGoods).where(BdGoods.tenant_id == tenant_id))
        await db.execute(delete(BdWarehouse).where(BdWarehouse.tenant_id == tenant_id))
        await db.execute(delete(BdSupplier).where(BdSupplier.tenant_id == tenant_id))
        await db.execute(delete(BdCustomer).where(BdCustomer.tenant_id == tenant_id))
        await db.execute(delete(SysUser).where(SysUser.tenant_id == tenant_id))
        await db.commit()


def test_tenant_isolation_customer_repository() -> None:
    """测试客户仓储的租户隔离。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        tenant1_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        tenant2_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        try:
            # 为两个租户创建数据
            data1 = await _create_tenant_data(sessions, tenant1_id, "T1")
            data2 = await _create_tenant_data(sessions, tenant2_id, "T2")
            
            # 租户1的仓储只能访问租户1的数据
            async with sessions() as db:
                repo1 = CustomerRepository(db, tenant_id=tenant1_id)
                customer1 = await repo1.get(data1["customer_id"])
                customer2 = await repo1.get(data2["customer_id"])
                
                assert customer1 is not None
                assert customer1.tenant_id == tenant1_id
                assert customer2 is None  # 租户1无法访问租户2的客户
            
            # 租户2的仓储只能访问租户2的数据
            async with sessions() as db:
                repo2 = CustomerRepository(db, tenant_id=tenant2_id)
                customer1 = await repo2.get(data1["customer_id"])
                customer2 = await repo2.get(data2["customer_id"])
                
                assert customer2 is not None
                assert customer2.tenant_id == tenant2_id
                assert customer1 is None  # 租户2无法访问租户1的客户
            
            # 列表查询也隔离
            async with sessions() as db:
                repo1 = CustomerRepository(db, tenant_id=tenant1_id)
                from app.schemas.customer import CustomerQuery
                customers1, total1 = await repo1.paginate(
                    repo1.build_query(CustomerQuery()),
                    page=1,
                    page_size=10,
                )
                assert total1 == 1
                assert len(customers1) == 1
                assert customers1[0].tenant_id == tenant1_id
        finally:
            await _cleanup_tenant(sessions, tenant1_id)
            await _cleanup_tenant(sessions, tenant2_id)
            await engine.dispose()

    _run(scenario)


def test_tenant_isolation_supplier_repository() -> None:
    """测试供应商仓储的租户隔离。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        tenant1_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        tenant2_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        try:
            data1 = await _create_tenant_data(sessions, tenant1_id, "T1")
            data2 = await _create_tenant_data(sessions, tenant2_id, "T2")
            
            # 租户1的仓储只能访问租户1的数据
            async with sessions() as db:
                repo1 = SupplierRepository(db, tenant_id=tenant1_id)
                supplier1 = await repo1.get(data1["supplier_id"])
                supplier2 = await repo1.get(data2["supplier_id"])
                
                assert supplier1 is not None
                assert supplier1.tenant_id == tenant1_id
                assert supplier2 is None
            
            # 租户2的仓储只能访问租户2的数据
            async with sessions() as db:
                repo2 = SupplierRepository(db, tenant_id=tenant2_id)
                supplier1 = await repo2.get(data1["supplier_id"])
                supplier2 = await repo2.get(data2["supplier_id"])
                
                assert supplier2 is not None
                assert supplier2.tenant_id == tenant2_id
                assert supplier1 is None
        finally:
            await _cleanup_tenant(sessions, tenant1_id)
            await _cleanup_tenant(sessions, tenant2_id)
            await engine.dispose()

    _run(scenario)


def test_tenant_isolation_inventory_repository() -> None:
    """测试库存仓储的租户隔离。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        tenant1_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        tenant2_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        try:
            data1 = await _create_tenant_data(sessions, tenant1_id, "T1")
            data2 = await _create_tenant_data(sessions, tenant2_id, "T2")
            
            # 租户1的仓储只能访问租户1的库存
            async with sessions() as db:
                repo1 = InventoryRepository(db, tenant_id=tenant1_id)
                stock1 = await repo1.get_stock_for_update(
                    data1["warehouse_id"], data1["goods_id"]
                )
                stock2 = await repo1.get_stock_for_update(
                    data2["warehouse_id"], data2["goods_id"]
                )
                
                assert stock1 is not None
                assert stock1.tenant_id == tenant1_id
                assert stock2 is None
            
            # 租户2的仓储只能访问租户2的库存
            async with sessions() as db:
                repo2 = InventoryRepository(db, tenant_id=tenant2_id)
                stock1 = await repo2.get_stock_for_update(
                    data1["warehouse_id"], data1["goods_id"]
                )
                stock2 = await repo2.get_stock_for_update(
                    data2["warehouse_id"], data2["goods_id"]
                )
                
                assert stock2 is not None
                assert stock2.tenant_id == tenant2_id
                assert stock1 is None
        finally:
            await _cleanup_tenant(sessions, tenant1_id)
            await _cleanup_tenant(sessions, tenant2_id)
            await engine.dispose()

    _run(scenario)


def test_tenant_isolation_with_soft_delete() -> None:
    """测试软删除不会影响租户隔离。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        tenant1_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        tenant2_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        try:
            data1 = await _create_tenant_data(sessions, tenant1_id, "T1")
            data2 = await _create_tenant_data(sessions, tenant2_id, "T2")
            
            # 软删除租户1的客户
            async with sessions() as db:
                customer = await db.get(BdCustomer, data1["customer_id"])
                if customer:
                    customer.is_deleted = True
                await db.commit()
            
            # 租户1的查询应该看不到已删除的客户
            async with sessions() as db:
                repo1 = CustomerRepository(db, tenant_id=tenant1_id)
                from app.schemas.customer import CustomerQuery
                customers1, total1 = await repo1.paginate(
                    repo1.build_query(CustomerQuery()),
                    page=1,
                    page_size=10,
                )
                assert total1 == 0  # 租户1已删除的客户不在列表中
            
            # 租户2的查询不受影响
            async with sessions() as db:
                repo2 = CustomerRepository(db, tenant_id=tenant2_id)
                customers2, total2 = await repo2.paginate(
                    repo2.build_query(CustomerQuery()),
                    page=1,
                    page_size=10,
                )
                assert total2 == 1  # 租户2的客户仍在列表中
        finally:
            await _cleanup_tenant(sessions, tenant1_id)
            await _cleanup_tenant(sessions, tenant2_id)
            await engine.dispose()

    _run(scenario)


def test_cross_tenant_reference_prevention() -> None:
    """测试跨租户引用被阻止。"""
    async def scenario() -> None:
        engine, sessions = _factory()
        tenant1_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        tenant2_id = uuid.uuid4().int % 8_000_000_000 + 1_000_000_000
        try:
            data1 = await _create_tenant_data(sessions, tenant1_id, "T1")
            data2 = await _create_tenant_data(sessions, tenant2_id, "T2")
            
            # 尝试用租户1的库存仓储访问租户2的仓库和商品
            async with sessions() as db:
                repo1 = InventoryRepository(db, tenant_id=tenant1_id)
                # 尝试获取租户2的仓库
                warehouse2 = await repo1.get_warehouse_for_update(data2["warehouse_id"])
                assert warehouse2 is None  # 应该返回None，因为租户不匹配
                
                # 尝试获取租户2的商品
                goods2 = await repo1.get_goods_for_update(data2["goods_id"])
                assert goods2 is None  # 应该返回None，因为租户不匹配
        finally:
            await _cleanup_tenant(sessions, tenant1_id)
            await _cleanup_tenant(sessions, tenant2_id)
            await engine.dispose()

    _run(scenario)
