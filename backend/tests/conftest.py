"""测试公共夹具：默认只使用本次运行新建的临时 SQLite 数据库。"""

import asyncio
import importlib.util
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

# 在导入应用前覆盖连接配置，绝不从开发 .env 继承测试连接。
_test_directory = tempfile.TemporaryDirectory(prefix="erp-tests-")
os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = (
    "sqlite+aiosqlite:///" + (Path(_test_directory.name) / "test.sqlite3").as_posix()
)
os.environ["REDIS_URL"] = ""
os.environ["SECRET_KEY"] = "isolated-test-secret-not-for-deployment"

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import event, text  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.sys import SysConfig, SysDictItem, SysDictType  # noqa: E402


def pytest_configure(config) -> None:
    """默认临时目录属于本次测试，避免复用其他会话不可访问的 pytest 根目录。"""
    if config.option.basetemp is None:
        config.option.basetemp = str(Path(_test_directory.name) / "pytest")


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_foreign_keys(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


async def _initialize_test_database() -> None:
    from app.db.init_db import _seed

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        await _seed(session)
        # 复用已发布迁移的数据定义；不在 SQLite 上模拟 PostgreSQL 历史迁移。
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "a5c9d3e7f1b2_dict_config_billno_and_role_dept.py"
        )
        spec = importlib.util.spec_from_file_location("test_seed_definitions", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # SA-01：报价单号规则在后续迁移 e2f4a6b8c0d2 里，同样按数据定义复用
        quotation_migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "e2f4a6b8c0d2_sa01_sal_quotation_seeds.py"
        )
        quotation_spec = importlib.util.spec_from_file_location(
            "test_seed_definitions_sa01", quotation_migration_path
        )
        quotation_module = importlib.util.module_from_spec(quotation_spec)
        quotation_spec.loader.exec_module(quotation_module)
        # SAL-02/04~09：销售订单号规则在迁移 b7d2e9f4a6c1 里，同样按数据定义复用
        sal_order_migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "b7d2e9f4a6c1_sa02_sal_order_seeds.py"
        )
        sal_order_spec = importlib.util.spec_from_file_location(
            "test_seed_definitions_sal_order", sal_order_migration_path
        )
        sal_order_module = importlib.util.module_from_spec(sal_order_spec)
        sal_order_spec.loader.exec_module(sal_order_module)
        session.add_all(
            [SysDictType(code=code, name=name, status=1) for code, name in module._DICT_TYPES]
        )
        session.add_all(
            [
                SysDictItem(type_code=code, label=label, value=value, sort=sort, status=1)
                for code, label, value, sort in module._DICT_ITEMS
            ]
        )
        # 多个迁移可能重复补种同一 sys_config 键（如 a5c9d3e7f1b2 基线已含 billno.sal_order，
        # b7d2e9f4a6c1 再次补种）：按 key 去重、先到先得，与迁移内的 _config_exists 同口径
        merged_configs: dict[str, tuple[str, str, str]] = {}
        for key, value, name in [
            *module._CONFIGS,
            *quotation_module._CONFIGS,
            *sal_order_module._CONFIGS,
        ]:
            merged_configs.setdefault(key, (key, value, name))
        session.add_all(
            [
                SysConfig(key=key, value=value, name=name)
                for key, value, name in merged_configs.values()
            ]
        )
        await session.commit()
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _init_database():
    asyncio.run(_initialize_test_database())
    yield
    asyncio.run(engine.dispose())
    _test_directory.cleanup()


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    # 保留真实认证/租户依赖；不注入或覆盖待测 X-Tenant-Id。
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _clear_login_logs() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM sys_login_log"))
        await session.commit()


@pytest.fixture(autouse=True)
def _clear_login_logs_per_test():
    asyncio.run(_clear_login_logs())
    yield


@pytest.fixture(autouse=True)
def _dispose_engine_after_test():
    yield
    asyncio.run(engine.dispose())
