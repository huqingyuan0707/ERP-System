"""基础资料端点（仓库 CRUD）。

职责：
  1. 定义 HTTP 接口的 URL、方法与参数校验；
  2. 做登录鉴权（JWT）和权限校验（perm_code）；
  3. 把业务请求转交给 Service 层；
  4. 把 Service 返回结果包装成统一响应 {code, message, data}。
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, get_redis, require_perm
from app.core.exceptions import AppException, ErrorCode
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
    tenant_id: TenantDep,  # 从 JWT 载荷解析当前租户，用于数据隔离
    # 校验「仓库查询」权限；本函数用不到用户对象，故用下划线命名
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
    # 返回统一响应，data 为分页结构（total、page、page_size、items）
) -> ApiResponse[PageResult[WarehouseOut]]:
    """查询仓库分页列表。

    :param query: 分页与筛选参数，由 FastAPI 从 URL 查询串解析。
    :param db: 异步数据库会话。
    :param tenant_id: 当前租户 ID，用于多租户数据隔离。
    :return: 统一包装的分页数据。
    :raises AppException: 无 base:warehouse:list 权限时由依赖抛出。
    """
    # 调用业务层执行查询，Router 不直接碰数据库
    result = await WarehouseService(db, tenant_id=tenant_id).list_warehouses(query)
    # 包装成 {code: 0, message: "success", data: ...} 返回前端
    return success_response(result)


# 注册 POST /base/warehouses 接口，用于创建新仓库
@router.post("/warehouses", summary="新增仓库")
async def create_warehouse(  # 新增仓库的处理函数
    payload: WarehouseCreate,  # 请求体，按 WarehouseCreate 模型做必填项与格式校验
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 从 JWT 载荷解析当前租户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:add"))],
    redis=Depends(get_redis),
    # 返回统一响应，data 为新建成功的仓库详情
) -> ApiResponse[WarehouseOut]:
    """新增一个仓库。

    :param payload: 仓库创建参数（编码、名称等）。
    :param db: 异步数据库会话。
    :param tenant_id: 当前租户 ID。
    :param current_user: 当前登录用户，其 id 会写入 created_by 审计字段。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限、编码重复等业务异常。
    """
    # 幂等校验：同一租户、同一操作人、同一编码在 24 小时内只允许提交一次
    if redis is not None:
        idem_key = f"idempotency:create_warehouse:{tenant_id}:{current_user.id}:{payload.code or 'auto'}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增仓库")
    # 业务层负责查重、落库并回写审计字段
    result = await WarehouseService(db, tenant_id=tenant_id).create_warehouse(payload, current_user.id)
    return success_response(result)  # 按统一格式返回创建结果


# 注册 GET /base/warehouses/{warehouse_id}，路径参数是仓库主键
@router.get("/warehouses/{warehouse_id}", summary="仓库详情")
async def get_warehouse(  # 查询单个仓库详情的处理函数
    warehouse_id: int,  # 路径中的仓库主键，FastAPI 会自动转换成 int 并校验类型
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 从 JWT 载荷解析当前租户
    # 详情与列表同属查询场景，复用 list 权限码
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
    # 返回统一响应，data 为仓库详情
) -> ApiResponse[WarehouseOut]:
    """按主键查询仓库详情。

    :param warehouse_id: 仓库主键。
    :param db: 异步数据库会话。
    :param tenant_id: 当前租户 ID，用于多租户数据隔离。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限或记录不存在（已删除）时抛出。
    """
    # 业务层按主键查询，并过滤已软删除的数据
    result = await WarehouseService(db, tenant_id=tenant_id).get_warehouse(warehouse_id)
    return success_response(result)  # 按统一格式返回详情


# 注册 PUT /base/warehouses/{warehouse_id}，用于局部更新仓库
@router.put("/warehouses/{warehouse_id}", summary="修改仓库")
async def update_warehouse(  # 修改仓库的处理函数
    warehouse_id: int,  # 要修改的仓库主键
    payload: WarehouseUpdate,  # 请求体，字段均可选，只更新前端传过来的字段
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 从 JWT 载荷解析当前租户
    # 校验「仓库修改」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:edit"))],
    # 返回统一响应，data 为修改后的仓库详情
) -> ApiResponse[WarehouseOut]:
    """修改仓库信息（部分更新）。

    :param warehouse_id: 仓库主键。
    :param payload: 待更新的字段，未传的字段保持原值。
    :param db: 异步数据库会话。
    :param tenant_id: 当前租户 ID，用于多租户数据隔离。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一包装的仓库详情。
    :raises AppException: 无权限、记录不存在、编码冲突等业务异常。
    """
    # 业务层负责校验与落库，并记录修改人
    result = await WarehouseService(db, tenant_id=tenant_id).update_warehouse(warehouse_id, payload, current_user.id)
    return success_response(result)  # 按统一格式返回修改后的详情


# 注册 DELETE /base/warehouses/{warehouse_id}，执行删除（通常是软删除）
@router.delete("/warehouses/{warehouse_id}", summary="删除仓库")
async def delete_warehouse(  # 删除仓库的处理函数
    warehouse_id: int,  # 要删除的仓库主键
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 从 JWT 载荷解析当前租户
    # 校验「仓库删除」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:delete"))],
    # 删除无业务数据返回，data 固定为 null
) -> ApiResponse[None]:
    """删除仓库。

    :param warehouse_id: 仓库主键。
    :param db: 异步数据库会话。
    :param tenant_id: 当前租户 ID，用于多租户数据隔离。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一响应，data 为 None。
    :raises AppException: 无权限或记录不存在时抛出。
    """
    # 业务层执行删除并处理关联校验，无需返回数据
    await WarehouseService(db, tenant_id=tenant_id).delete_warehouse(warehouse_id, current_user.id)
    # 成功时 data 为 null，前端只需判断 code 是否为 0
    return success_response(None)
