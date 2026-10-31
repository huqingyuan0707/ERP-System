"""异步任务端点（C-07）。

路由前缀 /sys，完整路径如：
  GET  /api/v1/sys/tasks        任务列表
  GET  /api/v1/sys/tasks/{id}   任务详情（前端轮询用）
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUserDep, DbDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.task import TaskOut, TaskQuery
from app.services.task import TaskService


router = APIRouter(prefix="/sys", tags=["异步任务"])

TASK_VIEW_PERM = "sys:task:view"


@router.get("/tasks", summary="异步任务列表")
async def list_tasks(
    db: DbDep,
    current_user: CurrentUserDep,
    task_type: str | None = Query(default=None, description="任务类型"),
    status: int | None = Query(default=None, description="状态 1=待执行 2=执行中 3=成功 4=失败"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: Annotated[SysUser, Depends(require_perm(TASK_VIEW_PERM))] = None,
) -> ApiResponse[PageResult[TaskOut]]:
    """异步任务分页查询。"""
    query = TaskQuery(task_type=task_type, status=status, page=page, page_size=page_size)
    result = await TaskService(db).list_tasks(query)
    return success_response(result)


@router.get("/tasks/{task_id}", summary="任务详情")
async def get_task(
    task_id: str,
    db: DbDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(TASK_VIEW_PERM))] = None,
) -> ApiResponse[TaskOut]:
    """根据 taskId 查询单条任务，前端轮询进度与下载地址。"""
    result = await TaskService(db).get_task_by_id(task_id)
    return success_response(result)
