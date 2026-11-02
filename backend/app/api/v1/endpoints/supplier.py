"""Supplier 基础资料接口：权限、租户、显式幂等键与业务同事务。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.supplier import (
    SupplierCreate,
    SupplierOut,
    SupplierQuery,
    SupplierUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.supplier import SupplierService


router = APIRouter(prefix="/base", tags=["基础资料-供应商"])


@router.get("/suppliers", summary="供应商列表")
async def list_suppliers(
    query: Annotated[SupplierQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
) -> ApiResponse[PageResult[SupplierOut]]:
    return success_response(await SupplierService(db, tenant_id).list_suppliers(query))


@router.get("/suppliers/options", summary="可选供应商列表")
async def list_selectable_suppliers(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
) -> ApiResponse[list[SupplierOut]]:
    return success_response(await SupplierService(db, tenant_id).list_selectable_suppliers())


@router.post("/suppliers")
async def create_supplier(
    payload: SupplierCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SupplierOut]:
    service = SupplierService(db, tenant_id)

    async def operation():
        return await service.create_supplier(payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_supplier",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(SupplierOut.model_validate(result))


@router.put("/suppliers/{supplier_id}")
async def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SupplierOut]:
    service = SupplierService(db, tenant_id)

    async def operation():
        return await service.update_supplier(supplier_id, payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_supplier",
        idempotency_key=idempotency_key,
        request_body={
            "id": supplier_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(SupplierOut.model_validate(result))


@router.delete("/suppliers/{supplier_id}")
async def delete_supplier(
    supplier_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:supplier:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = SupplierService(db, tenant_id)

    async def operation():
        return await service.delete_supplier(supplier_id, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_supplier",
        idempotency_key=idempotency_key,
        request_body={"id": supplier_id},
        operation=operation,
    )
    return success_response(None)


@router.get("/suppliers/{supplier_id}", summary="供应商详情")
async def get_supplier(
    supplier_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:supplier:list"))],
) -> ApiResponse[SupplierOut]:
    return success_response(await SupplierService(db, tenant_id).get_supplier(supplier_id))
