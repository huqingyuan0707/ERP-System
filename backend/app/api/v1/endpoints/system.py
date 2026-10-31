"""系统管理端点（用户 CRUD、角色管理、权限树、角色权限/数据范围分配）。

本文件是 FastAPI 的“路由层”（也叫 Controller/端点层）。
职责：
  1. 定义 HTTP 接口的 URL、方法（GET/POST/PUT/DELETE）、参数校验；
  2. 调用依赖注入做登录鉴权（JWT）和权限校验（perm_code）；
  3. 把业务请求转交给 Service 层处理；
  4. 把 Service 返回的结果包装成统一响应格式 {code, message, data} 返回给前端。

阅读建议：
  - 每个接口函数前的 @router.xxx 装饰器决定了“访问这个接口的 URL 和方法”；
  - 函数参数里带 Depends(...) 的，会在函数执行前先由 FastAPI 自动处理；
  - 函数里真正的业务逻辑都在 Service 层（services/user.py 等），这里只负责“调度”。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

# 项目内部模块
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
    # query 参数会从 URL 查询字符串读取，如 /sys/users?page=1&page_size=20&keyword=张三
    # Annotated[UserQuery, Query()] 表示：用 Pydantic 模型 UserQuery 来校验查询参数，
    # 包含 page、page_size、keyword、status、dept_id 等字段。
    query: Annotated[UserQuery, Query()],
    # DbDep 是数据库会话依赖，FastAPI 会自动创建 AsyncSession 并在请求结束后关闭。
    db: DbDep,
    # 权限依赖：只有拥有 "sys:user:list" 权限码的用户才能调用此接口。
    # _ 表示我们不需要使用这个依赖返回的当前用户对象，只需要它做权限拦截。
    # 如果当前用户没有该权限，FastAPI 会直接返回 403，不会进入函数体。
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[PageResult[UserOut]]:
    # 把查询参数和数据库会话交给 Service 层，Service 返回分页结果。
    result = await UserService(db).list_users(query)
    # success_response 把结果包装成 {code: 0, message: "success", data: result}。
    return success_response(result)


@router.post("/users", summary="新增用户")
async def create_user(
    # payload 从请求体（JSON）中读取，并由 Pydantic 模型 UserCreate 自动校验。
    # 如果前端发送的字段不符合规则（如 username 为空），FastAPI 会直接返回 422。
    payload: UserCreate,
    db: DbDep,
    # 当前登录用户对象，同时做权限校验：必须有 "sys:user:add" 权限。
    # current_user.id 会被传给 Service，用于写操作日志（“谁创建的用户”）。
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:add"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).create_user(payload, current_user.id)
    return success_response(result)


@router.get("/users/{user_id}", summary="用户详情")
async def get_user(
    # 路径参数，URL 中的 {user_id} 会被自动解析为 int 类型。
    # 例如 GET /sys/users/5 会把 user_id=5 传入。
    user_id: int,
    db: DbDep,
    # 权限校验：需要 "sys:user:list" 权限（查看用户详情属于列表权限范畴）。
    _: Annotated[SysUser, Depends(require_perm("sys:user:list"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).get_user(user_id)
    return success_response(result)


@router.put("/users/{user_id}", summary="修改用户")
async def update_user(
    user_id: int,
    # 请求体中的字段都是可选的（UserUpdate 里所有字段 default=None），
    # 表示前端只需要传要修改的字段即可，未传的字段不做修改。
    payload: UserUpdate,
    db: DbDep,
    # 权限校验：需要 "sys:user:edit" 权限。
    # current_user.id 用于操作日志，记录是谁修改的。
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:edit"))],
) -> ApiResponse[UserOut]:
    result = await UserService(db).update_user(user_id, payload, current_user.id)
    return success_response(result)


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    db: DbDep,
    # 权限校验：需要 "sys:user:delete" 权限。
    # 注意：这里是逻辑删除（is_deleted=True），不是把数据库行真正删掉。
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:delete"))],
) -> ApiResponse[None]:
    await UserService(db).delete_user(user_id, current_user.id)
    # 删除操作不需要返回数据，所以 data 传 None。
    return success_response(None)


@router.post("/users/{user_id}/reset-password", summary="重置密码")
async def reset_password(
    user_id: int,
    # 请求体中包含新密码（可选，不传则使用系统默认初始密码）。
    payload: PasswordResetIn,
    db: DbDep,
    # 权限校验：需要 "sys:user:reset" 权限。
    current_user: Annotated[SysUser, Depends(require_perm("sys:user:reset"))],
) -> ApiResponse[None]:
    await UserService(db).reset_password(user_id, payload.password, current_user.id)
    return success_response(None)


# ---------------------------------------------------------------------------
# 权限与角色相关接口
# ---------------------------------------------------------------------------


@router.get("/permissions/tree", summary="权限树")
async def list_permission_tree(
    db: DbDep,
    # 权限校验：需要 "sys:role:list" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[PermissionNodeOut]]:
    # 获取权限菜单树（目录/菜单/按钮三级结构），用于前端渲染侧边栏和权限分配页面。
    result = await PermissionService(db).get_tree()
    return success_response(result)


@router.put("/roles/{role_id}/permissions", summary="分配角色权限")
async def assign_role_permissions(
    role_id: int,
    # 请求体包含要分配给该角色的权限 ID 列表。
    payload: RolePermissionAssign,
    db: DbDep,
    # 权限校验：需要 "sys:role:assign" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await PermissionService(db).assign_role_permissions(role_id, payload.permission_ids)
    return success_response(None)


@router.get("/depts", summary="部门列表")
async def list_depts(
    db: DbDep,
    # 权限校验：需要 "sys:role:list" 权限（部门数据通常和角色管理放在一起）。
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[DeptOut]]:
    # 查询所有部门，并把 ORM 模型对象转换为 Pydantic 输出模型 DeptOut。
    result = await DeptService(db).list_all()
    return success_response([DeptOut.model_validate(d) for d in result])


@router.get("/roles", summary="角色列表")
async def list_roles(
    db: DbDep,
    # 权限校验：需要 "sys:role:list" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:list"))],
) -> ApiResponse[list[RoleOut]]:
    result = await RoleService(db).list_roles()
    return success_response([RoleOut.model_validate(r) for r in result])


@router.post("/roles", summary="新增角色")
async def create_role(
    payload: RoleCreate,
    db: DbDep,
    # 权限校验：需要 "sys:role:add" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:add"))],
) -> ApiResponse[RoleOut]:
    result = await RoleService(db).create_role(payload)
    return success_response(RoleOut.model_validate(result))


@router.put("/roles/{role_id}", summary="修改角色")
async def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: DbDep,
    # 权限校验：需要 "sys:role:edit" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:edit"))],
) -> ApiResponse[RoleOut]:
    result = await RoleService(db).update_role(role_id, payload)
    # 修改角色后，需要把该角色关联的部门 ID 也一起返回给前端。
    dept_ids = await RoleRepository(db).list_dept_ids(role_id)
    out = RoleOut.model_validate(result)
    # model_copy(update=...) 用于在返回前临时给出参数 dept_ids。
    return success_response(out.model_copy(update={"dept_ids": dept_ids}))


@router.delete("/roles/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    db: DbDep,
    # 权限校验：需要 "sys:role:delete" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:delete"))],
) -> ApiResponse[None]:
    await RoleService(db).delete_role(role_id)
    return success_response(None)


@router.put("/roles/{role_id}/data-scope", summary="分配角色数据范围")
async def assign_role_data_scope(
    role_id: int,
    # 请求体包含数据范围配置（1=本人、2=本部门、3=全部，以及自定义部门列表）。
    payload: DataScopeAssign,
    db: DbDep,
    # 权限校验：需要 "sys:role:assign" 权限。
    _: Annotated[SysUser, Depends(require_perm("sys:role:assign"))],
) -> ApiResponse[None]:
    await RoleService(db).assign_data_scope(role_id, payload)
    return success_response(None)
