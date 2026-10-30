"""测试公共夹具。

说明：test_health/test_auth 均用 asyncio.run 为每个用例创建独立事件循环，
SQLAlchemy 异步引擎的连接池绑定事件循环，因此每个用例结束后释放连接，
避免下个循环复用旧连接报错。
"""

import asyncio

import pytest

from app.db.session import engine


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
