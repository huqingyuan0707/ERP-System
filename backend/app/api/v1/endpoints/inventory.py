"""库存接口：只读查询（库存/流水/汇总）与写操作（锁定/释放/变更）。

写接口要求显式 Idempotency-Key，库存与流水在同一事务内提交；
所有接口按 tenant_id 隔离，权限码沿用 init_db 中已播种的 inv:* 体系。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.inventory import (
    ChangeStockIn,
    ChangeStockOut,
    FlowListItem,
    FlowQuery,
    LockStockIn,
    StockListItem,
    StockOut,
    StockQuery,
    StockSummary,
    UnlockStockIn,
)
from app.services.idempotency import IdempotencyService
from app.services.inventory import InventoryService


router = APIRouter(prefix="/inventory", tags=["库存管理"])
INV_STOCK_LIST_PERM = "inv:stock:list"
INV_FLOW_LIST_PERM = "inv:flow:list"
INV_CHANGE_STOCK_PERM = "inv:stock:change"
INV_LOCK_STOCK_PERM = "inv:stock:lock"
INV_UNLOCK_STOCK_PERM = "inv:stock:unlock"


def _key(header: str | None, body: str | None) -> str:
    if header and body and header != body:
        raise AppException(ErrorCode.VALIDATION_ERROR, "请求头与请求体幂等键不一致")
    key = header or body
    if key is None:
        raise AppException(ErrorCode.VALIDATION_ERROR, "库存写操作必须提供 Idempotency-Key")
    return key


# ---------- 只读查询 ----------


@router.get("/stocks", summary="实时库存列表")
async def list_stocks(
    query: Annotated[StockQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(INV_STOCK_LIST_PERM))],
) -> ApiResponse[PageResult[StockListItem]]:
    """分页查询实时库存。

    支持按仓库、商品筛选；keyword 命中商品编码/名称/条码；
    onlyAvailable=true 只看可用量大于 0（拣货）；belowLower=true 只看低于库存下限（补货）。
    """
    return success_response(await InventoryService(db, tenant_id).list_stocks(query))


@router.get("/stocks/summary", summary="实时库存汇总")
async def stock_summary(
    query: Annotated[StockQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(INV_STOCK_LIST_PERM))],
) -> ApiResponse[StockSummary]:
    """按与列表一致的过滤条件返回数量与金额合计，供列表页底部展示。"""
    return success_response(await InventoryService(db, tenant_id).stock_summary(query))


@router.get("/flows", summary="出入库流水列表")
async def list_flows(
    query: Annotated[FlowQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(INV_FLOW_LIST_PERM))],
) -> ApiResponse[PageResult[FlowListItem]]:
    """分页查询库存流水（只增不删的留痕记录）。

    支持按仓库、商品、流水类型、方向、来源单号与业务时间区间筛选；
    区间跨度上限 366 天，防止大范围扫表。
    """
    return success_response(await InventoryService(db, tenant_id).list_flows(query))


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
        user_id=current_user.id,  # 幂等记录归属真实操作人，避免多人共用同一幂等键相互覆盖
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
        user_id=current_user.id,  # 幂等记录归属真实操作人
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
        user_id=current_user.id,  # 幂等记录归属真实操作人
        biz_type="unlock_stock",
        idempotency_key=_key(idempotency_key, payload.idempotency_key),
        request_body=payload.model_dump(mode="json", exclude={"idempotency_key"}),
        operation=operation,
    )
    return success_response(StockOut.model_validate(result))
