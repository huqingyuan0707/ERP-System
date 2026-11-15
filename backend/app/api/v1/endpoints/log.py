"""操作日志与登录日志端点（C-06）。

路由前缀 /sys，完整路径如：
  GET /api/v1/sys/logs/oper   操作日志列表
  GET /api/v1/sys/logs/login  登录日志列表
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.requests import Request

from app.core.deps import CurrentUserDep, DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.login_log import LoginLogOut, LoginLogQuery
from app.schemas.oper_log import OperLogOut, OperLogQuery
from app.services.login_log import LoginLogService
from app.services.oper_log import OperLogService


router = APIRouter(prefix="/sys", tags=["系统日志"])

# 日志查询需要的权限码
LOG_VIEW_PERM = "sys:log:view"


def _parse_date(value: str | None) -> datetime | None:
    """将 YYYY-MM-DD 字符串解析为 datetime，失败返回 None。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/logs/oper", summary="操作日志列表")
async def list_oper_logs(
    request: Request,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(LOG_VIEW_PERM))],
    query: Annotated[OperLogQuery, Query()],
) -> ApiResponse[PageResult[OperLogOut]]:
    """操作日志分页查询（只读）。

    用 Query 模型注入：对外参数名走 camelCase（pageSize/userId/startDate…），
    populate_by_name 同时兼容旧 snake_case 调用方，筛选条件不会再被静默忽略。
    """
    result = await OperLogService(db, tenant_id=tenant_id).list_oper_logs(query)
    return success_response(result)


@router.get("/logs/login", summary="登录日志列表")
async def list_login_logs(
    request: Request,
    db: DbDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(LOG_VIEW_PERM))],
    query: Annotated[LoginLogQuery, Query()],
) -> ApiResponse[PageResult[LoginLogOut]]:
    """登录日志分页查询（只读）；camelCase query，兼容 snake_case。"""
    result = await LoginLogService(db).list_login_logs(query)
    return success_response(result)
