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

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        self.db = db
        self.tenant_id = tenant_id

    def _apply_tenant_filter(self, stmt: Select) -> Select:
        """对模型包含 tenant_id 字段的查询自动追加租户过滤条件。

        当 tenant_id 为 None 时（如登录鉴权等系统级操作），不追加租户过滤，
        允许跨租户查询；业务 Service 层应始终传入具体 tenant_id 做数据隔离。
        """
        if self.tenant_id is not None and hasattr(self.model, "tenant_id"):
            stmt = stmt.where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]
        return stmt

    async def get(self, pk: int) -> ModelT | None:
        stmt = select(self.model).where(self.model.id == pk)  # type: ignore[attr-defined]
        stmt = self._apply_tenant_filter(stmt)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, obj: ModelT) -> ModelT:
        if (
            self.tenant_id is not None
            and hasattr(obj, "tenant_id")
            and obj.tenant_id is None
        ):
            obj.tenant_id = self.tenant_id  # type: ignore[attr-defined]
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.db.delete(obj)

    async def paginate(
        self, stmt: Select[tuple[ModelT]], page: int, page_size: int
    ) -> tuple[list[ModelT], int]:
        """执行分页查询，返回 (当前页数据, 总数)。自动过滤非当前租户数据。"""
        stmt = self._apply_tenant_filter(stmt)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows_stmt = stmt.limit(page_size).offset(max(page - 1, 0) * page_size)
        rows = list((await self.db.execute(rows_stmt)).scalars().all())
        return rows, total

    async def paginate_rows(self, stmt: Select, page: int, page_size: int) -> tuple[list[Row], int]:
        """多列查询分页，保留关联列并过滤当前租户。"""
        stmt = self._apply_tenant_filter(stmt)
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self.db.execute(count_stmt)).scalar_one())
        rows_stmt = stmt.limit(page_size).offset(max(page - 1, 0) * page_size)
        rows = list((await self.db.execute(rows_stmt)).all())
        return rows, total
