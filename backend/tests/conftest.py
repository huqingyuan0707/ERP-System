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


# 必须在导入 app 之前设置：配置与引擎在导入时即读取环境变量
os.environ["TESTING"] = "1"

from app.core.config import settings  # noqa: E402  需在环境变量设置之后导入


_base_url = settings.DATABASE_URL
if "/erp_test" not in _base_url:
    # 直接改 settings 实例（引擎在导入时读取该值），避免测试数据污染开发库
    settings.DATABASE_URL = _base_url.rsplit("/", 1)[0] + "/erp_test"

import pytest  # noqa: E402

from app.db.session import engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_database() -> None:
    """会话级建表与种子数据（幂等，缺库/新库均可跑）。"""
    from app.db.init_db import init_db

    asyncio.run(init_db())
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _dispose_engine_after_test() -> None:
    """每个用例结束后释放连接池。"""
    yield
    asyncio.run(engine.dispose())
