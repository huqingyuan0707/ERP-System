"""基础资料端点（客户 CRUD）。

职责：
  1. 定义 HTTP 接口的 URL、方法与参数校验；
  2. 做登录鉴权（JWT）和权限校验（perm_code）；
  3. 把业务请求转交给 Service 层；
  4. 把 Service 返回结果包装成统一响应 {code, message, data}。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.customer import (
    CustomerCreate,
    CustomerCreditOut,
    CustomerOut,
    CustomerQuery,
    CustomerUpdate,
)
from app.services.customer import CustomerService


router = APIRouter(prefix="/base", tags=["基础资料-客户"])


# ---------- 客户 ----------


@router.get("/customers", summary="客户列表")
async def list_customers(
    query: Annotated[CustomerQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[PageResult[CustomerOut]]:
    """查询客户分页列表。"""
    result = await CustomerService(db, tenant_id=tenant_id).list_customers(query)
    return success_response(result)


@router.get("/customers/options", summary="可选客户列表")
async def list_selectable_customers(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[list[CustomerOut]]:
    """查询可用于新单据的客户列表，只返回启用状态。"""
    result = await CustomerService(db, tenant_id=tenant_id).list_selectable_customers()
    return success_response(result)


@router.post("/customers", summary="新增客户")
async def create_customer(
    payload: CustomerCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:add"))],
) -> ApiResponse[CustomerOut]:
    """新增一个客户。"""
    result = await CustomerService(db, tenant_id=tenant_id).create_customer(
        payload, current_user.id
    )
    return success_response(result)


@router.get("/customers/{customer_id}", summary="客户详情")
async def get_customer(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[CustomerOut]:
    """按主键查询客户详情。"""
    result = await CustomerService(db, tenant_id=tenant_id).get_customer(customer_id)
    return success_response(result)


@router.put("/customers/{customer_id}", summary="修改客户")
async def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:edit"))],
) -> ApiResponse[CustomerOut]:
    """修改客户信息（部分更新）。"""
    result = await CustomerService(db, tenant_id=tenant_id).update_customer(
        customer_id, payload, current_user.id
    )
    return success_response(result)


@router.delete("/customers/{customer_id}", summary="删除客户")
async def delete_customer(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:delete"))],
) -> ApiResponse[None]:
    """删除客户（软删除，存在单据引用时返回 409）。"""
    await CustomerService(db, tenant_id=tenant_id).delete_customer(customer_id, current_user.id)
    return success_response(None)


@router.get("/customers/{customer_id}/credit", summary="客户信用概况")
async def get_customer_credit(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[CustomerCreditOut]:
    """返回客户信用额度、应收余额、可用额度与是否超限，供前端超限提示使用。"""
    result = await CustomerService(db, tenant_id=tenant_id).get_credit_summary(customer_id)
    return success_response(result)
