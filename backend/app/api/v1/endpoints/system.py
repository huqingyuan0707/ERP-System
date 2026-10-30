"""系统管理端点（用户 CRUD、角色管理、权限树、角色权限/数据范围分配）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, require_perm
from app.models.sys import SysUser
from app.repositories.role import RoleRepository
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.dept import DeptOut
from app.schemas.permission import PermissionNodeOut, RolePermissionAssign
from app.schemas.role import DataScopeAssign, RoleCreate, RoleOut, RoleUpdate
from app.schemas.user import PasswordResetIn, UserCreate, UserOut, UserQuery, UserUpdate
from app.services.dept import DeptService
from app.services.permission import PermissionService
from app.services.role import RoleService
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
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:add"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).create_user(payload, current_user.id)
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
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:edit"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).update_user(user_id, payload, current_user.id)
    return success_response(result)


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: DbDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:delete"))],
) -> ApiResponse[None]:
    await UserService(db).delete_user(user_id, current_user.id)
    return success_response(None)


@router.post("/users/{user_id}/reset-password", summary="重置密码")
async def reset_password(
    user_id: int,
    payload: PasswordResetIn,
    db: DbDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:reset"))],
) -> ApiResponse[None]:
    await UserService(db).reset_password(user_id, payload.password, current_user.id)
    return success_response(None)


@router.get("/permissions/tree", summary="权限树")
async def list_permission_tree(
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[PermissionNodeOut]]:
    result = await PermissionService(db).get_tree()
    return success_response(result)


@router.put("/roles/{role_id}/permissions", summary="分配角色权限")
async def assign_role_permissions(
    role_id: int,
    payload: RolePermissionAssign,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await PermissionService(db).assign_role_permissions(role_id, payload.permission_ids)
    return success_response(None)


@router.get("/depts", summary="部门列表")
async def list_depts(
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[DeptOut]]:
    result = await DeptService(db).list_all()
    return success_response([DeptOut.model_validate(d) for d in result])


@router.get("/roles", summary="角色列表")
async def list_roles(
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[RoleOut]]:
    result = await RoleService(db).list_roles()
    return success_response([RoleOut.model_validate(r) for r in result])


@router.post("/roles", summary="新增角色")
async def create_role(
    payload: RoleCreate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:add"))],
) -> ApiResponse[RoleOut]:
    result = await RoleService(db).create_role(payload)
    return success_response(RoleOut.model_validate(result))


@router.put("/roles/{role_id}", summary="修改角色")
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:edit"))],
) -> ApiResponse[RoleOut]:
    result = await RoleService(db).update_role(role_id, payload)
    dept_ids = await RoleRepository(db).list_dept_ids(role_id)
    out = RoleOut.model_validate(result)
    return success_response(out.model_copy(update={"dept_ids": dept_ids}))


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:delete"))],
) -> ApiResponse[None]:
    await RoleService(db).delete_role(role_id)
    return success_response(None)


@router.put("/roles/{role_id}/data-scope", summary="分配角色数据范围")
async def assign_role_data_scope(
    role_id: int,
    payload: DataScopeAssign,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await RoleService(db).assign_data_scope(role_id, payload)
    return success_response(None)
