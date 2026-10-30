"""通用数据访问层：泛型 BaseRepository。"""

from typing import ClassVar, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base


ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """泛型仓储，仅提供 CRUD 与查询封装。"""

    model: ClassVar[type[ModelT]]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, pk: int) -> ModelT | None:
        return await self.db.get(self.model, pk)

    async def add(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.db.delete(obj)

    async def paginate(
        self, stmt: Select[tuple[ModelT]], page: int, page_size: int
    ) -> tuple[list[ModelT], int]:
        """执行分页查询，返回 (当前页数据, 总数)。"""
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows_stmt = stmt.limit(page_size).offset(max(page - 1, 0) * page_size)
        rows = list((await self.db.execute(rows_stmt)).scalars().all())
        return rows, total

    async def paginate_rows(
        self, stmt: Select, page: int, page_size: int
    ) -> tuple[list[Row], int]:
        """多列查询的分页，返回 (原始行列表, 总数)，不丢弃关联列。"""
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows_stmt = stmt.limit(page_size).offset(max(page - 1, 0) * page_size)
        rows = list((await self.db.execute(rows_stmt)).all())
        return rows, total
