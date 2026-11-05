"""异步引擎与会话工厂。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, QueuePool

from app.core.config import settings


# 测试环境每个用例独立事件循环，必须禁用连接池，否则会跨循环复用连接
# 生产环境使用QueuePool，配置合理的连接池大小
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    poolclass=NullPool if settings.TESTING else QueuePool,
    pool_size=5 if not settings.TESTING else None,  # 连接池大小
    max_overflow=10 if not settings.TESTING else None,  # 最大溢出连接数
    pool_recycle=3600 if not settings.TESTING else None,  # 连接回收时间（秒）
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供一个请求级异步会话。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_pool_status() -> dict:
    """获取连接池状态，用于监控。"""
    pool = engine.pool
    if pool is None:
        return {"status": "disabled (testing)"}
    
    return {
        "status": "active",
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "max_overflow": pool._max_overflow,
    }
