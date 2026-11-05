"""事务管理工具：提供事务上下文管理器和装饰器。"""

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def transaction(db: AsyncSession):
    """事务上下文管理器：自动提交或回滚。

    使用方式：
        async with transaction(db):
            # 执行数据库操作
            ...

    如果发生异常，自动回滚；否则自动提交。
    """
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise


def with_transaction(commit: bool = True):
    """事务装饰器：为service方法提供事务管理。

    使用方式：
        @with_transaction(commit=True)
        async def my_service_method(self, ...):
            # self._db 已作为参数传入
            # 如果commit=True，方法结束后自动提交
            # 如果commit=False，由调用方控制提交
            ...

    注意：被装饰的方法必须接收db作为第一个参数（或self._db）。
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 提取db参数
            db = None
            if args and hasattr(args[0], '_db'):
                db = args[0]._db
            elif 'db' in kwargs:
                db = kwargs['db']

            if db is None:
                # 如果没有db参数，直接调用原函数
                return await func(*args, **kwargs)

            if not commit:
                # commit=False时，不管理事务
                return await func(*args, **kwargs)

            try:
                result = await func(*args, **kwargs)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

        return wrapper
    return decorator


class TransactionManager:
    """事务管理器：提供更细粒度的事务控制。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._committed = False
        self._rolled_back = False

    async def __aenter__(self) -> "TransactionManager":
        """进入事务上下文。"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出事务上下文：根据异常情况提交或回滚。"""
        if exc_type is not None:
            if not self._rolled_back:
                await self.db.rollback()
                self._rolled_back = True
        elif not self._committed:
            await self.db.commit()
            self._committed = True

    async def commit(self) -> None:
        """手动提交事务。"""
        await self.db.commit()
        self._committed = True

    async def rollback(self) -> None:
        """手动回滚事务。"""
        await self.db.rollback()
        self._rolled_back = True

    @property
    def is_active(self) -> bool:
        """检查事务是否仍然活跃（未提交也未回滚）。"""
        return not self._committed and not self._rolled_back
