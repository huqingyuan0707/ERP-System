"""部门业务逻辑（查询）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysDept


class DeptService:
    """部门查询服务。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def list_all(self) -> list[SysDept]:
        """全量有效部门，按 sort、id 排序（供树形选择）。"""
        stmt = (
            select(SysDept)
            .where(
                SysDept.is_deleted.is_(False),
                SysDept.tenant_id == self.tenant_id,  # 租户隔离：只查当前租户部门
            )
            .order_by(SysDept.sort, SysDept.id)
        )
        return list((await self.db.execute(stmt)).scalars().all())
