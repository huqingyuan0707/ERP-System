"""收款管理接口（F-03）：/finance/receipts 的列表、汇总、详情、CRUD 与核销。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.receipt import (
    ReceiptCreate,
    ReceiptOut,
    ReceiptQuery,
    ReceiptSummary,
    ReceiptUpdate,
    WriteoffCreate,
)
from app.services.idempotency import IdempotencyService
from app.services.receipt import ReceiptService

router = APIRouter(prefix="/finance/receipts", tags=["财务-收款管理"])

LIST_PERM = "fin:receipt:list"


@router.get("", summary="收款单列表")
async def list_receipts(
    query: Annotated[ReceiptQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[ReceiptOut]]:
    return success_response(await ReceiptService(db, tenant_id).list_receipts(query, current_user))


@router.get("/summary", summary="收款汇总")
async def get_summary(
    query: Annotated[ReceiptQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[ReceiptSummary]:
    return success_response(await ReceiptService(db, tenant_id).get_summary(query, current_user))


@router.post("", summary="新增收款单（待核销）")
async def create_receipt(
    payload: ReceiptCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:receipt:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[ReceiptOut]:
    service = ReceiptService(db, tenant_id)

    async def operation() -> ReceiptOut:
        return await service.create_receipt(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_receipt",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(ReceiptOut.model_validate(result))


@router.put("/{receipt_id}", summary="修改收款单（未核销）")
async def update_receipt(
    receipt_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:receipt:edit"))],
    payload: ReceiptUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[ReceiptOut]:
    service = ReceiptService(db, tenant_id)

    async def operation() -> ReceiptOut:
        return await service.update_receipt(receipt_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_receipt",
        idempotency_key=idempotency_key,
        request_body={
            "id": receipt_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(ReceiptOut.model_validate(result))


@router.delete("/{receipt_id}", summary="删除收款单（未核销）")
async def delete_receipt(
    receipt_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:receipt:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    service = ReceiptService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_receipt(receipt_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_receipt",
        idempotency_key=idempotency_key,
        request_body={"id": receipt_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/{receipt_id}/writeoff", summary="收款核销（冲减应收）")
async def writeoff_receipt(
    receipt_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:receipt:writeoff"))],
    payload: WriteoffCreate,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[ReceiptOut]:
    """核销事务：客户一致/单条≤未收/总额≤未分配 → 写核销明细 → 冲减应收。"""
    service = ReceiptService(db, tenant_id)

    async def operation() -> ReceiptOut:
        return await service.writeoff_receipt(receipt_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="writeoff_receipt",
        idempotency_key=idempotency_key,
        request_body={"id": receipt_id, "payload": payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(ReceiptOut.model_validate(result))


@router.get("/{receipt_id}", summary="收款单详情")
async def get_receipt(
    receipt_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[ReceiptOut]:
    return success_response(
        await ReceiptService(db, tenant_id).get_detail(receipt_id, current_user)
    )
