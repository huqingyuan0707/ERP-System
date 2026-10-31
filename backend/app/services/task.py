"""异步任务业务逻辑（C-07）。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysTask
from app.repositories.task import TaskRepository
from app.schemas.common import PageResult
from app.schemas.task import TaskCreate, TaskOut, TaskQuery
from app.utils.pagination import paginate_result


class TaskService:
    """异步任务业务：创建、查询、按 task_id 取单条。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tasks = TaskRepository(db)

    async def create_task(
        self, payload: TaskCreate, created_by: int | None = None
    ) -> TaskOut:
        """新建一条任务记录（初始状态为“待执行”）。"""
        import uuid

        task = SysTask(
            task_id=uuid.uuid4().hex,
            task_type=payload.task_type,
            status=1,  # 待执行
            params=payload.params,
            progress=0,
            created_by=created_by or payload.created_by,
        )
        await self.tasks.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return TaskOut.model_validate(task)

    async def list_tasks(self, query: TaskQuery) -> PageResult[TaskOut]:
        """按条件分页查询任务。"""
        rows, total = await self.tasks.list_with_filter(
            task_type=query.task_type,
            status=query.status,
            page=query.page,
            page_size=query.page_size,
        )
        items = [TaskOut.model_validate(row) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_task_by_id(self, task_id: str) -> TaskOut:
        """根据 task_id 查询单条任务。"""
        task = await self.tasks.get_by_task_id(task_id)
        if task is None:
            raise AppException(ErrorCode.NOT_FOUND, "任务不存在")
        return TaskOut.model_validate(task)

    async def update_status(
        self,
        task_id: str,
        status: int,
        *,
        file_path: str | None = None,
        error_msg: str | None = None,
        progress: int | None = None,
        started_at: Any = None,
        finished_at: Any = None,
    ) -> SysTask:
        """更新任务状态（由 Celery 工作进程调用）。"""
        task = await self.tasks.get_by_task_id(task_id)
        if task is None:
            raise AppException(ErrorCode.NOT_FOUND, "任务不存在")
        task.status = status
        if file_path is not None:
            task.file_path = file_path
        if error_msg is not None:
            task.error_msg = error_msg
        if progress is not None:
            task.progress = progress
        if started_at is not None:
            task.started_at = started_at
        if finished_at is not None:
            task.finished_at = finished_at
        await self.db.commit()
        await self.db.refresh(task)
        return task
