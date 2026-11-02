"""任务仓储：所有入口要求明确租户，历史归属未知任务不对外暴露。"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys import SysTask
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[SysTask]):
    model = SysTask

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
            raise ValueError("任务操作必须提供有效 tenant_id")
        super().__init__(db, tenant_id=tenant_id)

    async def get_by_task_id(self, task_id: str) -> SysTask | None:
        stmt = select(SysTask).where(
            SysTask.tenant_id == self.tenant_id,
            SysTask.task_id == task_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_file_name(self, file_name: str, export_dir: Path) -> SysTask | None:
        """仅匹配指定导出目录里的完整路径，不允许 LIKE 通配符或相似后缀匹配。"""
        paths = {str(export_dir / file_name), str(export_dir.resolve() / file_name)}
        paths |= {path.replace("\\", "/") for path in paths}
        paths |= {path.replace("/", "\\") for path in paths}
        stmt = select(SysTask).where(
            SysTask.tenant_id == self.tenant_id,
            SysTask.file_path.in_(paths),
        )
        rows = list((await self.db.execute(stmt.limit(2))).scalars().all())
        # 历史数据若有歧义也不猜测文件归属。
        return rows[0] if len(rows) == 1 else None

    async def list_with_filter(
        self,
        task_type: str | None,
        status: int | None,
        page: int,
        page_size: int,
        *,
        created_by: int | None = None,
    ) -> tuple[list[SysTask], int]:
        stmt = select(SysTask).where(SysTask.tenant_id == self.tenant_id)
        if created_by is not None:
            stmt = stmt.where(SysTask.created_by == created_by)
        if task_type:
            stmt = stmt.where(SysTask.task_type == task_type)
        if status is not None:
            stmt = stmt.where(SysTask.status == status)
        return await self.paginate(
            stmt.order_by(SysTask.created_at.desc(), SysTask.id.desc()),
            page,
            page_size,
        )
