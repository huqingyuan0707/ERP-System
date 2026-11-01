"""系统管理端点（用户 CRUD、角色管理、权限树、角色权限/数据范围分配）。

本文件是 FastAPI 的“路由层”（也叫 Controller/端点层）。
职责：
  1. 定义 HTTP 接口的 URL、方法（GET/POST/PUT/DELETE）、参数校验；
  2. 调用依赖注入做登录鉴权（JWT）和权限校验（perm_code）；
  3. 把业务请求转交给 Service 层处理；
  4. 把业务返回的结果包装成统一响应格式 {code, message, data} 返回给前端。
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, get_redis, require_perm
from app.core.exceptions import AppException, ErrorCode
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


# 创建一个路由器对象。
# prefix="/sys" 表示下面所有接口的 URL 都以 /sys 开头。
# 完整的访问路径会是：/api/v1/sys/users 等（前缀 /api/v1 在 main.py 中统一加）。
# tags=["系统管理"] 只是给自动生成的接口文档（/docs）做分类显示。
router = APIRouter(prefix="/sys", tags=["系统管理"])


# ---------------------------------------------------------------------------
# 用户管理相关接口（SYS-01 ~ SYS-06）
# ---------------------------------------------------------------------------


@router.get("/users", summary="用户列表")
async def list_users(
    query: Annotated[UserQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[PageResult[UserOut]]:
    result = await UserService(db, tenant_id=tenant_id).list_users(query)
    return success_response(result)


@router.post("/users", summary="新增用户")
async def create_user(
    payload: UserCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:add"))],
    redis=Depends(get_redis),
) -> ApiResponse[UserOut]:
    if redis is not None:
        idem_key = f"idempotency:create_user:{tenant_id}:{current_user.id}:{payload.username}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增用户")
    result = await UserService(db, tenant_id=tenant_id).create_user(payload, current_user.id)
    return success_response(result)


@router.get("/users/{user_id}", summary="用户详情")
async def get_user(
    user_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db, tenant_id=tenant_id).get_user(user_id)
    return success_response(result)


@router.put("/users/{user_id}", summary="修改用户")
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:edit"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db, tenant_id=tenant_id).update_user(user_id, payload, current_user.id)
    return success_response(result)


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:delete"))],
) -> ApiResponse[None]:
    await UserService(db, tenant_id=tenant_id).delete_user(user_id, current_user.id)
    return success_response(None)


@router.post("/users/{user_id}/reset-password", summary="重置密码")
async def reset_password(
    user_id: int,
    payload: PasswordResetIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:reset"))],
) -> ApiResponse[None]:
    await UserService(db, tenant_id=tenant_id).reset_password(user_id, payload.password, current_user.id)
    return success_response(None)


# ---------------------------------------------------------------------------
# 权限与角色相关接口
# ---------------------------------------------------------------------------


@router.get("/permissions/tree", summary="权限树")
async def list_permission_tree(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[PermissionNodeOut]]:
    result = await PermissionService(db, tenant_id=tenant_id).get_tree()
    return success_response(result)


@router.put("/roles/{role_id}/permissions", summary="分配角色权限")
async def assign_role_permissions(
    role_id: int,
    payload: RolePermissionAssign,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await PermissionService(db, tenant_id=tenant_id).assign_role_permissions(role_id, payload.permission_ids)
    return success_response(None)


@router.get("/depts", summary="部门列表")
async def list_depts(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[DeptOut]]:
    result = await DeptService(db, tenant_id=tenant_id).list_all()
    return success_response([DeptOut.model_validate(d) for d in result])


@router.get("/roles", summary="角色列表")
async def list_roles(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[RoleOut]]:
    result = await RoleService(db, tenant_id=tenant_id).list_roles()
    return success_response([RoleOut.model_validate(r) for r in result])


@router.post("/roles", summary="新增角色")
async def create_role(
    payload: RoleCreate,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:add"))],
    redis=Depends(get_redis),
) -> ApiResponse[RoleOut]:
    if redis is not None:
        idem_key = f"idempotency:create_role:{tenant_id}:{payload.code}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增角色")
    result = await RoleService(db, tenant_id=tenant_id).create_role(payload)
    return success_response(RoleOut.model_validate(result))


@router.put("/roles/{role_id}", summary="修改角色")
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:edit"))],
) -> ApiResponse[RoleOut]:
    result = await RoleService(db, tenant_id=tenant_id).update_role(role_id, payload)
    dept_ids = await RoleRepository(db).list_dept_ids(role_id)
    out = RoleOut.model_validate(result)
    return success_response(out.model_copy(update={"dept_ids": dept_ids}))


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:delete"))],
) -> ApiResponse[None]:
    await RoleService(db, tenant_id=tenant_id).delete_role(role_id)
    return success_response(None)


@router.put("/roles/{role_id}/data-scope", summary="分配角色数据范围")
async def assign_role_data_scope(
    role_id: int,
    payload: DataScopeAssign,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await RoleService(db, tenant_id=tenant_id).assign_data_scope(role_id, payload)
    return success_response(None)
