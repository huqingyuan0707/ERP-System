"""系统管理端点（用户 CRUD）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.user import PasswordResetIn, UserCreate, UserOut, UserQuery, UserUpdate
from app.services.user import UserService


router = APIRouter(prefix="/sys", tags=["系统管理"])


@router.get("/users", summary="用户列表")
async def list_users(
    query: Annotated[UserQuery, Query()],
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[PageResult[UserOut]]:
    result = await UserService(db).list_users(query)
    return success_response(result)


@router.post("/users", summary="新增用户")
async def create_user(
    payload: UserCreate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:add"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).create_user(payload)
    return success_response(result)


@router.get("/users/{user_id}", summary="用户详情")
async def get_user(
    user_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).get_user(user_id)
    return success_response(result)


@router.put("/users/{user_id}", summary="修改用户")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:edit"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).update_user(user_id, payload)
    return success_response(result)


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:delete"))],
) -> ApiResponse[None]:
    await UserService(db).delete_user(user_id)
    return success_response(None)


@router.post("/users/{user_id}/reset-password", summary="重置密码")
async def reset_password(
    user_id: int,
    payload: PasswordResetIn,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:reset"))],
) -> ApiResponse[None]:
    await UserService(db).reset_password(user_id, payload.password)
    return success_response(None)
