"""基础资料端点（仓库 CRUD）。

职责：
  1. 定义 HTTP 接口的 URL、方法与参数校验；
  2. 做登录鉴权（JWT）和权限校验（perm_code）；
  3. 把业务请求转交给 Service 层；
  4. 把 Service 返回结果包装成统一响应 {code, message, data}。
"""

# 下面一组导入的含义（从上到下）：
#   Annotated        —— 给类型附加 FastAPI 的依赖、校验等元数据，让参数声明更简洁
#   APIRouter        —— 组织路由；Depends 注入依赖；Query 声明查询串参数
#   DbDep            —— 异步数据库会话依赖的类型别名
#   require_perm     —— 生成权限校验依赖，参数是权限码
#   SysUser          —— 系统用户模型，鉴权依赖会返回当前登录用户对象
#   ApiResponse      —— 统一响应泛型；PageResult 分页结果泛型；success_response 成功包装函数
#   WarehouseCreate  —— 新增仓库的请求体模型
#   WarehouseOut     —— 仓库对外输出模型（JSON 字段为 camelCase）
#   WarehouseQuery   —— 仓库列表的查询参数模型（分页、关键字等）
#   WarehouseUpdate  —— 修改仓库的请求体模型，字段全部可选，表示部分更新
#   WarehouseService —— 仓库业务逻辑层，Router 只负责调用，不写业务规则
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseOut,
    WarehouseQuery,
    WarehouseUpdate,
)
from app.services.warehouse import WarehouseService


# 本模块接口统一挂在 /base 前缀下，OpenAPI 文档分组为「基础资料-仓库」
router = APIRouter(prefix="/base", tags=["基础资料-仓库"])


# ---------- 仓库 ----------


# 注册 GET /base/warehouses 接口，summary 会显示在接口文档里
@router.get("/warehouses", summary="仓库列表")
async def list_warehouses(  # 分页查询仓库列表的处理函数
    # 从 URL 查询串解析分页与筛选条件，并做类型、格式校验
    query: Annotated[WarehouseQuery, Query()],
    db: DbDep,  # 注入本次请求的异步数据库会话，请求结束时由依赖自动关闭
    # 校验「仓库查询」权限；本函数用不到用户对象，故用下划线命名
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
    # 返回统一响应，data 为分页结构（total、page、page_size、items）
) -> ApiResponse[PageResult[WarehouseOut]]:
    """查询仓库分页列表。

    :param query: 分页与筛选参数，由 FastAPI 从 URL 查询串解析。
    :param db: 异步数据库会话。
    :return: 统一包装的分页数据。
    :raises AppException: 无 base:warehouse:list 权限时由依赖抛出。
    """
    # 调用业务层执行查询，Router 不直接碰数据库
    result = await WarehouseService(db).list_warehouses(query)
    # 包装成 {code: 0, message: "success", data: ...} 返回前端
    return success_response(result)


# 注册 POST /base/warehouses 接口，用于创建新仓库
@router.post("/warehouses", summary="新增仓库")
async def create_warehouse(  # 新增仓库的处理函数
    payload: WarehouseCreate,  # 请求体，按 WarehouseCreate 模型做必填项与格式校验
    db: DbDep,  # 注入本次请求的异步数据库会话
    # 校验「仓库新增」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:add"))],
    # 返回统一响应，data 为新建成功的仓库详情
) -> ApiResponse[WarehouseOut]:
    """新增一个仓库。

    :param payload: 仓库创建参数（编码、名称等）。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 created_by 审计字段。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限、编码重复等业务异常。
    """
    # 业务层负责查重、落库并回写审计字段
    result = await WarehouseService(db).create_warehouse(payload, current_user.id)
    return success_response(result)  # 按统一格式返回创建结果


# 注册 GET /base/warehouses/{warehouse_id}，路径参数是仓库主键
@router.get("/warehouses/{warehouse_id}", summary="仓库详情")
async def get_warehouse(  # 查询单个仓库详情的处理函数
    warehouse_id: int,  # 路径中的仓库主键，FastAPI 会自动转换成 int 并校验类型
    db: DbDep,  # 注入本次请求的异步数据库会话
    # 详情与列表同属查询场景，复用 list 权限码
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
    # 返回统一响应，data 为仓库详情
) -> ApiResponse[WarehouseOut]:
    """按主键查询仓库详情。

    :param warehouse_id: 仓库主键。
    :param db: 异步数据库会话。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限或记录不存在（已删除）时抛出。
    """
    # 业务层按主键查询，并过滤已软删除的数据
    result = await WarehouseService(db).get_warehouse(warehouse_id)
    return success_response(result)  # 按统一格式返回详情


# 注册 PUT /base/warehouses/{warehouse_id}，用于局部更新仓库
@router.put("/warehouses/{warehouse_id}", summary="修改仓库")
async def update_warehouse(  # 修改仓库的处理函数
    warehouse_id: int,  # 要修改的仓库主键
    payload: WarehouseUpdate,  # 请求体，字段均可选，只更新前端传过来的字段
    db: DbDep,  # 注入本次请求的异步数据库会话
    # 校验「仓库修改」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:edit"))],
    # 返回统一响应，data 为修改后的仓库详情
) -> ApiResponse[WarehouseOut]:
    """修改仓库信息（部分更新）。

    :param warehouse_id: 仓库主键。
    :param payload: 待更新的字段，未传的字段保持原值。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限、记录不存在、编码冲突等业务异常。
    """
    # 业务层负责校验与落库，并记录修改人
    result = await WarehouseService(db).update_warehouse(warehouse_id, payload, current_user.id)
    return success_response(result)  # 按统一格式返回修改后的详情


# 注册 DELETE /base/warehouses/{warehouse_id}，执行删除（通常是软删除）
@router.delete("/warehouses/{warehouse_id}", summary="删除仓库")
async def delete_warehouse(  # 删除仓库的处理函数
    warehouse_id: int,  # 要删除的仓库主键
    db: DbDep,  # 注入本次请求的异步数据库会话
    # 校验「仓库删除」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:delete"))],
    # 删除无业务数据返回，data 固定为 null
) -> ApiResponse[None]:
    """删除仓库。

    :param warehouse_id: 仓库主键。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一响应，data 为 None。
    :raises AppException: 无权限或记录不存在时抛出。
    """
    # 业务层执行删除并处理关联校验，无需返回数据
    await WarehouseService(db).delete_warehouse(warehouse_id, current_user.id)
    # 成功时 data 为 null，前端只需判断 code 是否为 0
    return success_response(None)
