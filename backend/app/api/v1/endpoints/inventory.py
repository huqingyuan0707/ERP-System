"""库存端点（INV-05/INV-06）。

职责：
  1. 定义库存变更、锁定、释放接口的 URL、方法与参数校验；
  2. 做登录鉴权（JWT）和权限校验（perm_code）；
  3. 幂等保护：单据提交接口增加幂等键，防止重复提交导致重复入库；
  4. 把业务请求转交给 Service 层；
  5. 把 Service 返回结果包装成统一响应 {code, message, data}。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.core.deps import CurrentUserDep, DbDep, TenantDep, get_redis, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.idempotency import IdempotencyKey
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


async def _idem_check(
    idem_service: IdempotencyService,
    *,
    tenant_id: int,
    user_id: int,
    biz_type: str,
    idempotency_key: str | None,
    request_body: Any | None = None,
) -> tuple[IdempotencyKey | None, bool]:
    """执行幂等检查。

    返回 (entity, is_duplicate)：
      - entity 为 IdempotencyKey，is_duplicate=False：本次应执行业务；
      - entity 为 IdempotencyKey，is_duplicate=True：重复提交，返回缓存结果；
      - 未提供幂等键时返回 (None, False)。

    注意：返回值是二元组，不是 IdempotencyService；
    原标注写成 `IdempotencyService | None` 与实际返回值不符，
    会让调用方的元组解包在静态检查下无法通过（pytype 报 attribute-error）。
    """
    if not idempotency_key:
        return None, False
    entity = await idem_service._repo.get_by_key(
        IdempotencyService.build_key(tenant_id, user_id, biz_type, idempotency_key)
    )
    if entity is not None and entity.status == 1:
        return entity, True  # 已成功：重复提交
    if entity is not None and entity.status == 3:
        await idem_service._repo.delete_by_key(entity.key)  # 失败记录允许重试
    request_body_hash = (
        IdempotencyService.hash_body(request_body) if request_body is not None else None
    )
    entity = await idem_service._repo.create(
        key=IdempotencyService.build_key(tenant_id, user_id, biz_type, idempotency_key),
        tenant_id=tenant_id,
        user_id=user_id,
        biz_type=biz_type,
        biz_no=idempotency_key,
        request_body_hash=request_body_hash,
    )
    return entity, False  # 新记录：执行业务


# ---------- 库存变更（入库/出库） ----------


@router.post("/stocks/change", summary="库存变更（入库/出库）")
async def change_stock(
    payload: ChangeStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(INV_CHANGE_STOCK_PERM))],
    redis=Depends(get_redis),
) -> ApiResponse[ChangeStockOut]:
    """库存变更统一接口（入库/出库/退货/调拨/盘点）。

    在单个数据库事务内完成库存扣减与流水写入，任一步骤失败则整体回滚。
    支持幂等键：同一 idempotency_key 在 24 小时内只允许成功处理一次。
    """
    idem_service = IdempotencyService(db)
    entity, is_duplicate = await _idem_check(
        idem_service,
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="change_stock",
        idempotency_key=payload.idempotency_key,
        request_body=payload,
    )
    if is_duplicate and entity is not None:
        cached = await idem_service.get_cached_response(entity)
        if cached is not None:
            return success_response(cached)
        raise AppException(ErrorCode.CONFLICT, "重复提交，请勿重复入库")

    try:
        result = await InventoryService(db, tenant_id=tenant_id).change_stock(payload)
        await idem_service.confirm_success(entity, result.model_dump())
        return success_response(result)
    except Exception:
        if entity is not None:
            await idem_service.mark_failed(entity)
        raise


# ---------- 库存锁定 ----------


@router.post("/stocks/lock", summary="库存锁定")
async def lock_stock(
    payload: LockStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(INV_LOCK_STOCK_PERM))],
    redis=Depends(get_redis),
) -> ApiResponse[StockOut]:
    """库存锁定：available_qty -= qty, locked_qty += qty。

    可用量不足时拒绝，避免超卖。支持幂等键防止重复锁定。
    """
    idem_service = IdempotencyService(db)
    entity, is_duplicate = await _idem_check(
        idem_service,
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="lock_stock",
        idempotency_key=payload.idempotency_key,
        request_body=payload,
    )
    if is_duplicate and entity is not None:
        cached = await idem_service.get_cached_response(entity)
        if cached is not None:
            return success_response(cached)
        raise AppException(ErrorCode.CONFLICT, "重复提交，请勿重复锁定库存")

    try:
        result = await InventoryService(db, tenant_id=tenant_id).lock_stock(payload)
        await idem_service.confirm_success(entity, result.model_dump())
        return success_response(result)
    except Exception:
        if entity is not None:
            await idem_service.mark_failed(entity)
        raise


# ---------- 库存释放 ----------


@router.post("/stocks/unlock", summary="库存释放")
async def unlock_stock(
    payload: UnlockStockIn,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(INV_UNLOCK_STOCK_PERM))],
    redis=Depends(get_redis),
) -> ApiResponse[StockOut]:
    """库存释放：locked_qty -= qty, available_qty += qty。

    支持幂等键防止重复释放。
    """
    idem_service = IdempotencyService(db)
    entity, is_duplicate = await _idem_check(
        idem_service,
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="unlock_stock",
        idempotency_key=payload.idempotency_key,
        request_body=payload,
    )
    if is_duplicate and entity is not None:
        cached = await idem_service.get_cached_response(entity)
        if cached is not None:
            return success_response(cached)
        raise AppException(ErrorCode.CONFLICT, "重复提交，请勿重复释放库存")

    try:
        result = await InventoryService(db, tenant_id=tenant_id).unlock_stock(payload)
        await idem_service.confirm_success(entity, result.model_dump())
        return success_response(result)
    except Exception:
        if entity is not None:
            await idem_service.mark_failed(entity)
        raise
