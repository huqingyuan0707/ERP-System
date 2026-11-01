"""操作日志与登录日志端点（C-06）。

路由前缀 /sys，完整路径如：
  GET /api/v1/sys/logs/oper   操作日志列表
  GET /api/v1/sys/logs/login  登录日志列表
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.requests import Request

from app.core.deps import CurrentUserDep, DbDep, require_perm
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
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(LOG_VIEW_PERM))],
    module: str | None = Query(default=None, description="模块名称模糊匹配"),
    action: str | None = Query(default=None, description="动作模糊匹配"),
    user_id: int | None = Query(default=None, description="操作人ID"),
    status: int | None = Query(default=None, description="状态 1=成功 0=失败"),
    start_date: str | None = Query(default=None, description="开始日期 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[PageResult[OperLogOut]]:
    """操作日志分页查询（只读）。"""
    query = OperLogQuery(
        module=module,
        action=action,
        user_id=user_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    result = await OperLogService(db).list_oper_logs(query)
    return success_response(result)


@router.get("/logs/login", summary="登录日志列表")
async def list_login_logs(
    request: Request,
    db: DbDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(LOG_VIEW_PERM))],
    username: str | None = Query(default=None, description="账号模糊匹配"),
    status: int | None = Query(default=None, description="状态 1=成功 0=失败"),
    start_date: str | None = Query(default=None, description="开始日期 YYYY-MM-DD"),
    end_date: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[PageResult[LoginLogOut]]:
    """登录日志分页查询（只读）。"""
    query = LoginLogQuery(
        username=username,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    result = await LoginLogService(db).list_login_logs(query)
    return success_response(result)
