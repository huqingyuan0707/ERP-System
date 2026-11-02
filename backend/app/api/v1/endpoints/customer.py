"""Customer 基础资料接口：权限、租户、显式幂等键与业务同事务。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

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
from app.services.idempotency import IdempotencyService


router = APIRouter(prefix="/base", tags=["基础资料-客户"])


@router.get("/customers", summary="客户列表")
async def list_customers(
    query: Annotated[CustomerQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[PageResult[CustomerOut]]:
    return success_response(await CustomerService(db, tenant_id).list_customers(query))


@router.get("/customers/options", summary="可选客户列表")
async def list_selectable_customers(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[list[CustomerOut]]:
    return success_response(await CustomerService(db, tenant_id).list_selectable_customers())


@router.post("/customers")
async def create_customer(
    payload: CustomerCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[CustomerOut]:
    service = CustomerService(db, tenant_id)

    async def operation():
        return await service.create_customer(payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_customer",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(CustomerOut.model_validate(result))


@router.put("/customers/{customer_id}")
async def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[CustomerOut]:
    service = CustomerService(db, tenant_id)

    async def operation():
        return await service.update_customer(customer_id, payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_customer",
        idempotency_key=idempotency_key,
        request_body={
            "id": customer_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(CustomerOut.model_validate(result))


@router.delete("/customers/{customer_id}")
async def delete_customer(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:customer:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = CustomerService(db, tenant_id)

    async def operation():
        return await service.delete_customer(customer_id, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_customer",
        idempotency_key=idempotency_key,
        request_body={"id": customer_id},
        operation=operation,
    )
    return success_response(None)


@router.get("/customers/{customer_id}", summary="客户详情")
async def get_customer(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[CustomerOut]:
    return success_response(await CustomerService(db, tenant_id).get_customer(customer_id))


@router.get("/customers/{customer_id}/credit", summary="客户信用概况")
async def get_customer_credit(
    customer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:customer:list"))],
) -> ApiResponse[CustomerCreditOut]:
    return success_response(await CustomerService(db, tenant_id).get_credit_summary(customer_id))
