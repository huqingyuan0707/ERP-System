"""部门业务逻辑（查询）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysDept


class DeptService:
    """部门查询服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_all(self) -> list[SysDept]:
        """全量有效部门，按 sort、id 排序（供树形选择）。"""
        stmt = (
            select(SysDept)
            .where(SysDept.is_deleted.is_(False))
            .order_by(SysDept.sort, SysDept.id)
        )
        return list((await self.db.execute(stmt)).scalars().all())
