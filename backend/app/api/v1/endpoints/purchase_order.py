"""采购订单接口（P-02）：/purchase/orders 的列表、详情、草稿 CRUD 与转单预填。

约定：
  - 权限：list 用 pur:order:list（菜单已存在），写接口用 pur:order:add/edit；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 转单预填只读：GET /purchase/orders/from-apply/{apply_id}，必须在 /{order_id} 之前声明。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.purchase_order import (
    PurOrderCreate,
    PurOrderFromApplyOut,
    PurOrderOut,
    PurOrderQuery,
    PurOrderUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.purchase_order import PurchaseOrderService


router = APIRouter(prefix="/purchase", tags=["采购管理-采购订单"])

LIST_PERM = "pur:order:list"


@router.get("/orders", summary="采购订单列表")
async def list_orders(
    query: Annotated[PurOrderQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[PurOrderOut]]:
    """按状态、供应商、下单日期区间、来源申请单与关键字分页查询采购订单。"""
    return success_response(
        await PurchaseOrderService(db, tenant_id).list_orders(query, current_user)
    )


@router.post("/orders", summary="新增采购订单（草稿）")
async def create_order(
    payload: PurOrderCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurOrderOut]:
    """新增即草稿；单号自动生成，金额由后端按明细重算。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> PurOrderOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_order(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_pur_order",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(PurOrderOut.model_validate(result))


@router.put("/orders/{order_id}", summary="修改采购订单草稿")
async def update_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:edit"))],
    payload: PurOrderUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurOrderOut]:
    """仅草稿可改；其余状态返回 409。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> PurOrderOut:
        return await service.update_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_pur_order",
        idempotency_key=idempotency_key,
        request_body={
            "id": order_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(PurOrderOut.model_validate(result))


@router.delete("/orders/{order_id}", summary="删除采购订单草稿")
async def delete_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史流水保留；非草稿返回 409。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_order(order_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_pur_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id},
        operation=operation,
    )
    return success_response(None)


@router.get("/orders/from-apply/{apply_id}", summary="按已审采购申请单生成订单预填数据")
async def get_order_from_apply(
    apply_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurOrderFromApplyOut]:
    """只读：校验申请单已审 + 数据范围，返回订单预填数据（单价取商品成本价），不生成订单。"""
    return success_response(
        await PurchaseOrderService(db, tenant_id).build_from_apply(apply_id, current_user)
    )


@router.get("/orders/{order_id}", summary="采购订单详情")
async def get_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurOrderOut]:
    """详情含明细（商品/单位/金额/累计入库），不存在返回 404。"""
    return success_response(await PurchaseOrderService(db, tenant_id).get_order(order_id))
