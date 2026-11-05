"""异步引擎与会话工厂。"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings


# 判断是否为SQLite数据库（使用URL解析避免子串误判）
try:
    from sqlalchemy.engine.url import make_url
    db_url = make_url(settings.DATABASE_URL)
    is_sqlite = db_url.drivername == "sqlite"
except Exception:
    # 解析失败时保守判断
    is_sqlite = settings.DATABASE_URL.startswith("sqlite")

# 测试环境使用NullPool，生产环境使用默认连接池
# SQLite不使用连接池参数，PostgreSQL在生产环境使用默认连接池
if settings.TESTING or is_sqlite:
    # 测试环境或SQLite：使用NullPool，不传递连接池参数
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        poolclass=NullPool,
    )
else:
    # 生产环境PostgreSQL：使用默认连接池，配置连接池参数
    # asyncio引擎不支持QueuePool，使用默认的AsyncAdaptedQueuePool
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,  # 连接池大小
        max_overflow=10,  # 最大溢出连接数
        pool_recycle=3600,  # 连接回收时间（秒）
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
