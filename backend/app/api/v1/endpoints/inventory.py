"""库存写接口：显式幂等键、库存与流水在同一事务提交。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.core.deps import DbDep, TenantDep, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.inventory import (
    ChangeStockIn,
    ChangeStockOut,
    LockStockIn,
    StockOut,
    UnlockStockIn,
)
from app.services.idempotency import IdempotencyService
from app.services.inventory import InventoryService


router = APIRouter(prefix="/inventory", tags=["库存管理"])
INV_CHANGE_STOCK_PERM = "inv:stock:change"
INV_LOCK_STOCK_PERM = "inv:stock:lock"
INV_UNLOCK_STOCK_PERM = "inv:stock:unlock"


def _key(header: str | None, body: str | None) -> str | None:
    if header and body and header != body:
        raise AppException(ErrorCode.VALIDATION_ERROR, "请求头与请求体幂等键不一致")
    return header or body


@router.post("/stocks/change")
async def change_stock(
    payload: ChangeStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(INV_CHANGE_STOCK_PERM))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[ChangeStockOut]:
    async def operation():
        return await InventoryService(db, tenant_id).change_stock(payload, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="change_stock",
        idempotency_key=_key(idempotency_key, payload.idempotency_key),
        request_body=payload.model_dump(mode="json", exclude={"idempotency_key"}),
        operation=operation,
    )
    return success_response(ChangeStockOut.model_validate(result))


@router.post("/stocks/lock")
async def lock_stock(
    payload: LockStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(INV_LOCK_STOCK_PERM))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[StockOut]:
    async def operation():
        return await InventoryService(db, tenant_id).lock_stock(payload, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="lock_stock",
        idempotency_key=_key(idempotency_key, payload.idempotency_key),
        request_body=payload.model_dump(mode="json", exclude={"idempotency_key"}),
        operation=operation,
    )
    return success_response(StockOut.model_validate(result))


@router.post("/stocks/unlock")
async def unlock_stock(
    payload: UnlockStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(INV_UNLOCK_STOCK_PERM))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[StockOut]:
    async def operation():
        return await InventoryService(db, tenant_id).unlock_stock(payload, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="unlock_stock",
        idempotency_key=_key(idempotency_key, payload.idempotency_key),
        request_body=payload.model_dump(mode="json", exclude={"idempotency_key"}),
        operation=operation,
    )
    return success_response(StockOut.model_validate(result))
