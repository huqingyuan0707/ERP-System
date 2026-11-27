"""库存调拨接口（I-03）：/inventory/transfers 的列表、详情、草稿 CRUD 与确认。

约定：
  - 权限：查看 inv:transfer:view，写接口分别用 inv:transfer:add/edit/confirm；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径必须在 /{transfer_id} 之前声明，否则会被当成动态 transfer_id 解析成 422。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.inventory_transfer import (
    TransferConfirm,
    TransferCreate,
    TransferOut,
    TransferQuery,
    TransferUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.inventory_transfer import InventoryTransferService

router = APIRouter(prefix="/inventory", tags=["库存管理-库存调拨"])

# 查看权限复用菜单权限码：与 init_db.py 播种的菜单/按钮权限保持一致
LIST_PERM = "inv:transfer:list"


@router.get("/transfers", summary="库存调拨单列表")
async def list_transfers(
    query: Annotated[TransferQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[TransferOut]]:
    """按状态、仓库、商品、日期区间分页查询；自动叠加数据范围。"""
    return success_response(
        await InventoryTransferService(db, tenant_id).list_transfers(query, current_user)
    )


@router.post("/transfers", summary="新增库存调拨单（草稿）")
async def create_transfer(
    payload: TransferCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("inv:transfer:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[TransferOut]:
    """新增即草稿；单号自动生成，调出/调入仓不能相同。"""
    service = InventoryTransferService(db, tenant_id)

    async def operation() -> TransferOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_transfer(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_inv_transfer",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(TransferOut.model_validate(result))


@router.put("/transfers/{transfer_id}", summary="修改库存调拨单草稿")
async def update_transfer(
    transfer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("inv:transfer:edit"))],
    payload: TransferUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[TransferOut]:
    """仅草稿可改；其余状态或版本号过期返回 409。"""
    service = InventoryTransferService(db, tenant_id)

    async def operation() -> TransferOut:
        return await service.update_transfer(transfer_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_inv_transfer",
        idempotency_key=idempotency_key,
        request_body={
            "id": transfer_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(TransferOut.model_validate(result))


@router.delete("/transfers/{transfer_id}", summary="删除库存调拨单草稿")
async def delete_transfer(
    transfer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("inv:transfer:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史流水保留；非草稿返回 409。"""
    service = InventoryTransferService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_transfer(transfer_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_inv_transfer",
        idempotency_key=idempotency_key,
        request_body={"id": transfer_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/transfers/{transfer_id}/confirm", summary="确认库存调拨（两仓库存变动+2条流水）")
async def confirm_transfer(
    transfer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("inv:transfer:confirm"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[TransferOut]:
    """确认事务：调出仓扣减 + 调入仓增加（按调出仓 avg_cost 重新加权）+ 写两条流水；
    调出仓可用量不足、重复确认返回 409；两仓库存在同一事务内保持一致。
    """
    service = InventoryTransferService(db, tenant_id)

    async def operation() -> TransferOut:
        return await service.confirm_transfer(transfer_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="confirm_inv_transfer",
        idempotency_key=idempotency_key,
        request_body={"id": transfer_id},
        operation=operation,
    )
    return success_response(TransferOut.model_validate(result))


@router.get("/transfers/{transfer_id}", summary="库存调拨单详情")
async def get_transfer(
    transfer_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[TransferOut]:
    """详情含明细与仓库名称；越权 403，不存在 404。"""
    return success_response(
        await InventoryTransferService(db, tenant_id).get_transfer(transfer_id, current_user)
    )
