"""异步任务数据访问（C-07）。"""

from app.models.sys import SysTask
from app.repositories.base import BaseRepository


class TaskRepository(BaseRepository[SysTask]):
    """异步任务仓储。"""

    model = SysTask

    async def get_by_task_id(self, task_id: str) -> SysTask | None:
        """根据 task_id 查询任务。"""
        from sqlalchemy import select

        stmt = select(SysTask).where(SysTask.task_id == task_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_with_filter(
        self,
        task_type: str | None,
        status: int | None,
        page: int,
        page_size: int,
    ) -> tuple[list[SysTask], int]:
        """按类型/状态筛选并分页查询任务。"""
        from sqlalchemy import select

        stmt = select(SysTask).order_by(SysTask.created_at.desc(), SysTask.id.desc())
        if task_type:
            stmt = stmt.where(SysTask.task_type == task_type)
        if status is not None:
            stmt = stmt.where(SysTask.status == status)
        return await self.paginate(stmt, page, page_size)
