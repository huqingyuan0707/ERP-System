"""任务业务与统一访问策略：查看权限不隐式授予他人文件下载权限。"""

import uuid
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysTask
from app.repositories.task import TaskRepository
from app.schemas.common import PageResult
from app.schemas.task import TaskCreate, TaskOut, TaskQuery
from app.utils.pagination import paginate_result


TASK_VIEW_PERM = "sys:task:view"


class TaskService:
    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.tasks = TaskRepository(db, tenant_id)

    @staticmethod
    def can_view_all(codes: Collection[str]) -> bool:
        return "*" in codes or TASK_VIEW_PERM in codes

    def can_download(self, task: SysTask, user_id: int, codes: Collection[str]) -> bool:
        return task.tenant_id == self.tenant_id and (task.created_by == user_id or "*" in codes)

    def _task_out(self, task: SysTask, user_id: int, codes: Collection[str]) -> TaskOut:
        allowed = self.can_download(task, user_id, codes)
        return TaskOut.model_validate(task).model_copy(
            update={
                "can_download": allowed and task.status == 3 and bool(task.file_path),
                "file_path": task.file_path if allowed else None,
            }
        )

    async def create_task(self, payload: TaskCreate, created_by: int) -> TaskOut:
        task = SysTask(
            tenant_id=self.tenant_id,
            task_id=uuid.uuid4().hex,
            task_type=payload.task_type,
            status=1,
            params=payload.params,
            progress=0,
            created_by=created_by,
        )
        await self.tasks.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return self._task_out(task, created_by, ())

    async def list_tasks(
        self,
        query: TaskQuery,
        *,
        user_id: int,
        codes: Collection[str],
    ) -> PageResult[TaskOut]:
        rows, total = await self.tasks.list_with_filter(
            task_type=query.task_type,
            status=query.status,
            page=query.page,
            page_size=query.page_size,
            created_by=None if self.can_view_all(codes) else user_id,
        )
        items = [self._task_out(row, user_id, codes) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_task_by_id(
        self,
        task_id: str,
        *,
        user_id: int,
        codes: Collection[str],
    ) -> TaskOut:
        task = await self.tasks.get_by_task_id(task_id)
        if task is None or (task.created_by != user_id and not self.can_view_all(codes)):
            raise AppException(ErrorCode.NOT_FOUND, "任务不存在")
        return self._task_out(task, user_id, codes)

    async def download_path(
        self,
        *,
        user_id: int,
        codes: Collection[str],
        export_dir: Path,
        task_id: str | None = None,
        file_name: str | None = None,
    ) -> Path:
        """taskId 为首选入口；兼容旧文件名接口，但匹配与授权规则完全一致。"""
        if task_id is not None:
            task = await self.tasks.get_by_task_id(task_id)
        else:
            if not file_name or any(token in file_name for token in ("/", "\\", "..", "\x00")):
                raise AppException(ErrorCode.BAD_REQUEST, "非法文件名")
            task = await self.tasks.get_by_file_name(file_name, export_dir)
        if task is None:
            raise AppException(ErrorCode.NOT_FOUND, "文件不存在或已过期")
        if not self.can_download(task, user_id, codes):
            raise AppException(ErrorCode.FORBIDDEN, "无权下载该导出文件")
        if task.status != 3 or not task.file_path:
            raise AppException(ErrorCode.NOT_FOUND, "任务未成功生成文件")
        # 以任务记录的完整路径为准，并阻止历史脏路径/符号链接越出导出目录。
        path = Path(task.file_path.replace("\\", "/")).resolve()
        if path.parent != export_dir.resolve() or not path.is_file():
            raise AppException(ErrorCode.NOT_FOUND, "文件不存在或已过期")
        return path

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
        """本地任务执行器更新状态，同样限定租户。"""
        task = await self.tasks.get_by_task_id(task_id)
        if task is None:
            raise AppException(ErrorCode.NOT_FOUND, "任务不存在")
        task.status = status
        for name, value in {
            "file_path": file_path,
            "error_msg": error_msg,
            "progress": progress,
            "started_at": started_at,
            "finished_at": finished_at,
        }.items():
            if value is not None:
                setattr(task, name, value)
        await self.db.commit()
        await self.db.refresh(task)
        return task
