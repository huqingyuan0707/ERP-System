"""Warehouse 基础资料接口：权限、租户、显式幂等键与业务同事务。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.warehouse import (
    WarehouseCreate,
    WarehouseManagerOption,
    WarehouseOut,
    WarehouseQuery,
    WarehouseUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.warehouse import WarehouseService


router = APIRouter(prefix="/base", tags=["基础资料-仓库"])


@router.get("/warehouses", summary="仓库列表")
async def list_warehouses(
    query: Annotated[WarehouseQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
) -> ApiResponse[PageResult[WarehouseOut]]:
    return success_response(await WarehouseService(db, tenant_id).list_warehouses(query))


@router.get("/warehouses/manager-options", summary="仓库负责人选项（暂用用户）")
async def list_manager_options(
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
    keyword: Annotated[str | None, Query(max_length=64)] = None,
) -> ApiResponse[list[WarehouseManagerOption]]:
    """只返回本租户启用用户的 ID/显示名，不依赖 sys:user:list；员工待 B06。"""
    return success_response(await WarehouseService(db, tenant_id).list_manager_options(keyword))


@router.post("/warehouses")
async def create_warehouse(
    payload: WarehouseCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[WarehouseOut]:
    service = WarehouseService(db, tenant_id)

    async def operation():
        return await service.create_warehouse(payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_warehouse",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(WarehouseOut.model_validate(result))


@router.put("/warehouses/{warehouse_id}")
async def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[WarehouseOut]:
    service = WarehouseService(db, tenant_id)

    async def operation():
        return await service.update_warehouse(warehouse_id, payload, current_user.id, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_warehouse",
        idempotency_key=idempotency_key,
        request_body={
            "id": warehouse_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(WarehouseOut.model_validate(result))


@router.delete("/warehouses/{warehouse_id}")
async def delete_warehouse(
    warehouse_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:warehouse:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = WarehouseService(db, tenant_id)

    async def operation():
        return await service.delete_warehouse(warehouse_id, current_user.id, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_warehouse",
        idempotency_key=idempotency_key,
        request_body={"id": warehouse_id},
        operation=operation,
    )
    return success_response(None)


@router.get("/warehouses/{warehouse_id}", summary="仓库详情")
async def get_warehouse(
    warehouse_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:warehouse:list"))],
) -> ApiResponse[WarehouseOut]:
    return success_response(await WarehouseService(db, tenant_id).get_warehouse(warehouse_id))
