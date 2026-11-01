"""基础资料端点（供应商 CRUD）。

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
#   SupplierCreate   —— 新增供应商的请求体模型
#   SupplierOut      —— 供应商对外输出模型（JSON 字段为 camelCase）
#   SupplierQuery    —— 供应商列表的查询参数模型（分页、关键字等）
#   SupplierUpdate   —— 修改供应商的请求体模型，字段全部可选，表示部分更新
#   SupplierService  —— 供应商业务逻辑层，Router 只负责调用，不写业务规则
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.supplier import (
    SupplierCreate,
    SupplierOut,
    SupplierQuery,
    SupplierUpdate,
)
from app.services.supplier import SupplierService


# 本模块接口统一挂在 /base 前缀下，OpenAPI 文档分组为「基础资料-供应商」
router = APIRouter(prefix="/base", tags=["基础资料-供应商"])


# ---------- 供应商 ----------


# 注册 GET /base/suppliers 接口，summary 会显示在接口文档里
@router.get("/suppliers", summary="供应商列表")
async def list_suppliers(  # 分页查询供应商列表的处理函数
    # 从 URL 查询串解析分页与筛选条件，并做类型、格式校验
    query: Annotated[SupplierQuery, Query()],
    db: DbDep,  # 注入本次请求的异步数据库会话，请求结束时由依赖自动关闭
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 校验「供应商查询」权限；本函数用不到用户对象，故用下划线命名
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
    # 返回统一响应，data 为分页结构（total、page、page_size、items）
) -> ApiResponse[PageResult[SupplierOut]]:
    """查询供应商分页列表。

    :param query: 分页与筛选参数，由 FastAPI 从 URL 查询串解析。
    :param db: 异步数据库会话。
    :return: 统一包装的分页数据。
    :raises AppException: 无 base:supplier:list 权限时由依赖抛出。
    """
    # 调用业务层执行查询，Router 不直接碰数据库
    result = await SupplierService(db, tenant_id=tenant_id).list_suppliers(query)
    # 包装成 {code: 0, message: "success", data: ...} 返回前端
    return success_response(result)


# 注册 GET /base/suppliers/options 接口，供采购订单等新单据选择供应商
# 该静态路径必须先于 /suppliers/{supplier_id} 注册，否则会被当作动态参数匹配
@router.get("/suppliers/options", summary="可选供应商列表")
async def list_selectable_suppliers(  # 返回启用状态供应商的处理函数
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 复用供应商列表权限；停用供应商不出现在结果中
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
    # 返回统一响应，data 为供应商数组
) -> ApiResponse[list[SupplierOut]]:
    """查询可用于新单据的供应商列表。

    只返回启用状态且未删除的供应商；停用供应商不参与新单据选择，
    历史单据仍按 supplier_id 正常关联显示。

    :param db: 异步数据库会话。
    :return: 统一包装的供应商数组。
    :raises AppException: 无 base:supplier:list 权限时由依赖抛出。
    """
    # 业务层按状态过滤并补齐应付余额
    result = await SupplierService(db, tenant_id=tenant_id).list_selectable_suppliers()
    return success_response(result)  # 按统一格式返回可选项


# 注册 POST /base/suppliers 接口，用于创建新供应商
@router.post("/suppliers", summary="新增供应商")
async def create_supplier(  # 新增供应商的处理函数
    payload: SupplierCreate,  # 请求体，按 SupplierCreate 模型做必填项与格式校验
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 校验「供应商新增」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:add"))],
    # 返回统一响应，data 为新建成功的供应商详情
) -> ApiResponse[SupplierOut]:
    """新增一个供应商。

    :param payload: 供应商创建参数（编码、名称、账期等）。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 created_by 审计字段。
    :return: 统一包装的供应商详情。
    :raises AppException: 无权限、编码重复、账期为负等业务异常。
    """
    # 业务层负责查重、落库并回写审计字段
    result = await SupplierService(db, tenant_id=tenant_id).create_supplier(payload, current_user.id)
    return success_response(result)  # 按统一格式返回创建结果


# 注册 GET /base/suppliers/{supplier_id}，路径参数是供应商主键
@router.get("/suppliers/{supplier_id}", summary="供应商详情")
async def get_supplier(  # 查询单个供应商详情的处理函数
    supplier_id: int,  # 路径中的供应商主键，FastAPI 会自动转换成 int 并校验类型
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 详情与列表同属查询场景，复用 list 权限码
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
    # 返回统一响应，data 为供应商详情
) -> ApiResponse[SupplierOut]:
    """按主键查询供应商详情。

    :param supplier_id: 供应商主键。
    :param db: 异步数据库会话。
    :return: 统一包装的供应商详情。
    :raises AppException: 无权限或记录不存在（已删除）时抛出。
    """
    # 业务层按主键查询，并过滤已软删除的数据
    result = await SupplierService(db, tenant_id=tenant_id).get_supplier(supplier_id)
    return success_response(result)  # 按统一格式返回详情


# 注册 PUT /base/suppliers/{supplier_id}，用于局部更新供应商
@router.put("/suppliers/{supplier_id}", summary="修改供应商")
async def update_supplier(  # 修改供应商的处理函数
    supplier_id: int,  # 要修改的供应商主键
    payload: SupplierUpdate,  # 请求体，字段均可选，只更新前端传过来的字段
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 校验「供应商修改」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:edit"))],
    # 返回统一响应，data 为修改后的供应商详情
) -> ApiResponse[SupplierOut]:
    """修改供应商信息（部分更新）。

    :param supplier_id: 供应商主键。
    :param payload: 待更新的字段，未传的字段保持原值。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一包装的供应商详情。
    :raises AppException: 无权限、记录不存在、编码冲突、账期为负等业务异常。
    """
    # 业务层负责校验与落库，并记录修改人
    result = await SupplierService(db, tenant_id=tenant_id).update_supplier(supplier_id, payload, current_user.id)
    return success_response(result)  # 按统一格式返回修改后的详情


# 注册 DELETE /base/suppliers/{supplier_id}，执行删除（通常是软删除）
@router.delete("/suppliers/{supplier_id}", summary="删除供应商")
async def delete_supplier(  # 删除供应商的处理函数
    supplier_id: int,  # 要删除的供应商主键
    db: DbDep,  # 注入本次请求的异步数据库会话
    tenant_id: TenantDep,  # 当前租户 ID，用于多租户数据隔离
    # 校验「供应商删除」权限，并拿到当前登录用户
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:delete"))],
    # 删除无业务数据返回，data 固定为 null
) -> ApiResponse[None]:
    """删除供应商。

    :param supplier_id: 供应商主键。
    :param db: 异步数据库会话。
    :param current_user: 当前登录用户，其 id 会写入 updated_by 审计字段。
    :return: 统一响应，data 为 None。
    :raises AppException: 无权限、记录不存在或存在单据引用时抛出。
    """
    # 业务层执行删除并处理关联校验，无需返回数据
    await SupplierService(db, tenant_id=tenant_id).delete_supplier(supplier_id, current_user.id)
    # 成功时 data 为 null，前端只需判断 code 是否为 0
    return success_response(None)
