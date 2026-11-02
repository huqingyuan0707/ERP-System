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
        session.add_all(
            [SysDictType(code=code, name=name, status=1) for code, name in module._DICT_TYPES]
        )
        session.add_all(
            [
                SysDictItem(type_code=code, label=label, value=value, sort=sort, status=1)
                for code, label, value, sort in module._DICT_ITEMS
            ]
        )
        session.add_all(
            [SysConfig(key=key, value=value, name=name) for key, value, name in module._CONFIGS]
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
