"""测试公共夹具。

说明：test_health/test_auth 均用 asyncio.run 为每个用例创建独立事件循环，
SQLAlchemy 异步引擎的连接池绑定事件循环，因此每个用例结束后释放连接，
避免下个循环复用旧连接报错。

PostgreSQL 下连接池无法跨事件循环复用（asyncpg 会报
'NoneType' object has no attribute 'send'），因此测试环境强制使用 NullPool，
并默认把库名指向 erp_test，避免测试数据污染开发库。
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


# 必须在导入 app 之前设置：配置与引擎在导入时即读取环境变量
os.environ["TESTING"] = "1"

from app.core.config import settings  # noqa: E402  需在环境变量设置之后导入


_base_url = settings.DATABASE_URL
if "/erp_test" not in _base_url:
    # 直接改 settings 实例（引擎在导入时读取该值），避免测试数据污染开发库
    settings.DATABASE_URL = _base_url.rsplit("/", 1)[0] + "/erp_test"

import pytest  # noqa: E402
import httpx  # noqa: E402
from fastapi import Request  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import engine, AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.core.deps import get_current_tenant  # noqa: E402


async def _test_get_current_tenant(
    request: Request = None, payload: dict = None
) -> int:
    """测试环境下统一返回租户 ID 1，避免每个测试请求手动携带 X-Tenant-Id。"""
    return 1


app.dependency_overrides[get_current_tenant] = _test_get_current_tenant


_ALTER_TENANT_SQL = """
ALTER TABLE sys_user ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE sys_dept ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE sys_role ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_warehouse ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_goods ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_goods_category ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_unit ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_supplier ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE bd_customer ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE inv_stock ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
ALTER TABLE inv_stock_flow ADD COLUMN IF NOT EXISTS tenant_id BIGINT NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_username_tenant ON sys_user (username, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dept_name_tenant ON sys_dept (name, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_role_code_tenant ON sys_role (code, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_warehouse_code_tenant ON bd_warehouse (code, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_goods_code_tenant ON bd_goods (code, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_goods_category_name_tenant
    ON bd_goods_category (name, parent_id, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_unit_name_tenant ON bd_unit (name, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_code_tenant ON bd_supplier (code, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_code_tenant ON bd_customer (code, tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_inv_stock_warehouse_goods_tenant
    ON inv_stock (warehouse_id, goods_id, tenant_id);
"""
# 说明：tenant_id 列上的普通索引（ix_*_tenant_id）由模型 TenantMixin 的
# index=True 自动生成，create_all 时已建好，此处不再重复创建。
# 否则会多出一批与模型定义不一致的索引，导致 `alembic check` 报
# "Detected removed index" 而失败（CI 的 db-migration 流水线会红）。


async def _add_tenant_id_to_test_db() -> None:
    """为测试数据库补全 tenant_id 列与索引（幂等），并清空登录日志避免限流误触。"""
    async with AsyncSessionLocal() as session:
        for stmt in _ALTER_TENANT_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await session.execute(text(stmt))
        # 清空登录日志，避免跨测试用例的登录失败累积触发限流
        await session.execute(text("DELETE FROM sys_login_log"))
        await session.commit()


async def _inject_tenant_header(request: httpx.Request) -> None:
    """所有测试请求自动携带 X-Tenant-Id 头，模拟多租户上下文。"""
    request.headers["X-Tenant-Id"] = "1"


@pytest.fixture(scope="session", autouse=True)
def _init_database() -> None:
    """会话级建表与种子数据（幂等，缺库/新库均可跑）。"""
    from app.db.init_db import init_db

    # 必须先建表：init_db 内部走 create_all + stamp，
    # 若表尚不存在，后面的 ALTER TABLE 会因 "relation does not exist" 直接失败。
    asyncio.run(init_db())
    # 再为已有表补全 tenant_id 列（DEFAULT 1）与租户唯一索引：
    # init_db 只保证表结构存在，不负责建索引；这里补齐后 ORM 查询不会缺列。
    asyncio.run(_add_tenant_id_to_test_db())
    asyncio.run(engine.dispose())


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        event_hooks={"request": [_inject_tenant_header]},
    ) as client:
        yield client


async def _clear_login_logs() -> None:
    """清空登录日志，避免跨测试用例的登录失败累积触发限流。"""
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM sys_login_log"))
        await session.commit()


@pytest.fixture(autouse=True)
def _clear_login_logs_per_test() -> None:
    """每个测试用例前清空登录日志。"""
    asyncio.run(_clear_login_logs())
    yield


@pytest.fixture(autouse=True)
def _dispose_engine_after_test() -> None:
    """每个用例结束后释放连接池。"""
    yield
    asyncio.run(engine.dispose())
