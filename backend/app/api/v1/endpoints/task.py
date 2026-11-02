"""任务列表、轮询和下载：owner 不依赖后台任务管理权限。"""

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.core.deps import CurrentUserDep, DbDep, TenantDep
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.task import TaskOut, TaskQuery
from app.services.auth import AuthService
from app.services.excel_export import EXPORT_DIR
from app.services.task import TaskService


router = APIRouter(prefix="/sys", tags=["异步任务"])


@router.get("/tasks", summary="异步任务列表")
async def list_tasks(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    task_type: str | None = Query(default=None, description="任务类型"),
    status: int | None = Query(default=None, ge=1, le=4),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[PageResult[TaskOut]]:
    query = TaskQuery(task_type=task_type, status=status, page=page, page_size=page_size)
    codes = await AuthService(db).get_permission_codes(current_user.id)
    result = await TaskService(db, tenant_id).list_tasks(
        query, user_id=current_user.id, codes=codes
    )
    return success_response(result)


@router.get("/tasks/{task_id}", summary="任务详情")
async def get_task(
    task_id: str,
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
) -> ApiResponse[TaskOut]:
    codes = await AuthService(db).get_permission_codes(current_user.id)
    result = await TaskService(db, tenant_id).get_task_by_id(
        task_id,
        user_id=current_user.id,
        codes=codes,
    )
    return success_response(result)


@router.get("/tasks/{task_id}/download", summary="下载任务文件")
async def download_task(
    task_id: str,
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
) -> FileResponse:
    codes = await AuthService(db).get_permission_codes(current_user.id)
    path = await TaskService(db, tenant_id).download_path(
        user_id=current_user.id,
        codes=codes,
        export_dir=EXPORT_DIR,
        task_id=task_id,
    )
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
