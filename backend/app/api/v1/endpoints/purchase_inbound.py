"""采购入库接口（P-03）：/purchase/inbounds 的列表、详情、按订单预填、草稿 CRUD 与确认。

约定：
  - 权限：查看复用菜单权限 pur:inbound:list（等价于需求文档的 pur:inbound:view），
    写接口分别用 pur:inbound:add/edit/confirm（删除复用 edit，与 P-01/P-02 的做法一致）；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径 /inbounds/from-order/{order_id} 必须在 /{inbound_id} 之前声明，
    否则会被当成动态 inbound_id 解析成 422。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.purchase_inbound import (
    PurInboundCreate,
    PurInboundFromOrderOut,
    PurInboundOut,
    PurInboundQuery,
    PurInboundUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.purchase_inbound import PurchaseInboundService


router = APIRouter(prefix="/purchase", tags=["采购管理-采购入库"])

# 查看权限复用菜单权限码：与 init_db.py 播种的菜单/按钮权限保持一致
LIST_PERM = "pur:inbound:list"


@router.get("/inbounds", summary="采购入库单列表")
async def list_inbounds(
    query: Annotated[PurInboundQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[PurInboundOut]]:
    """按状态、来源订单、供应商、仓库与入库日期区间分页查询；自动叠加数据范围。"""
    return success_response(
        await PurchaseInboundService(db, tenant_id).list_inbounds(query, current_user)
    )


@router.get("/inbounds/from-order/{order_id}", summary="按已审采购订单生成入库单预填数据")
async def get_inbound_from_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurInboundFromOrderOut]:
    """只读：校验订单可入库 + 数据范围，返回剩余可入库明细预填数据，不生成入库单。"""
    return success_response(
        await PurchaseInboundService(db, tenant_id).build_from_order(order_id, current_user)
    )


@router.post("/inbounds", summary="新增采购入库单（草稿）")
async def create_inbound(
    payload: PurInboundCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:inbound:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurInboundOut]:
    """新增即草稿；单号自动生成，单价/金额按来源订单明细重算。"""
    service = PurchaseInboundService(db, tenant_id)

    async def operation() -> PurInboundOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_inbound(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_pur_inbound",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(PurInboundOut.model_validate(result))


@router.put("/inbounds/{inbound_id}", summary="修改采购入库单草稿")
async def update_inbound(
    inbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:inbound:edit"))],
    payload: PurInboundUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurInboundOut]:
    """仅草稿可改；其余状态或版本号过期返回 409。"""
    service = PurchaseInboundService(db, tenant_id)

    async def operation() -> PurInboundOut:
        return await service.update_inbound(inbound_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_pur_inbound",
        idempotency_key=idempotency_key,
        request_body={
            "id": inbound_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(PurInboundOut.model_validate(result))


@router.delete("/inbounds/{inbound_id}", summary="删除采购入库单草稿")
async def delete_inbound(
    inbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:inbound:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史流水保留；非草稿返回 409。"""
    service = PurchaseInboundService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_inbound(inbound_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_pur_inbound",
        idempotency_key=idempotency_key,
        request_body={"id": inbound_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/inbounds/{inbound_id}/confirm", summary="确认采购入库（库存+订单回写+应付）")
async def confirm_inbound(
    inbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:inbound:confirm"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurInboundOut]:
    """确认事务：增加库存并写流水、回写订单累计入库与状态、生成应付；任一步失败全回滚。

    超剩余数量、重复确认返回 409；库存/订单/应付三账在同一事务内保持一致。
    """
    service = PurchaseInboundService(db, tenant_id)

    async def operation() -> PurInboundOut:
        return await service.confirm_inbound(inbound_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="confirm_pur_inbound",
        idempotency_key=idempotency_key,
        request_body={"id": inbound_id},
        operation=operation,
    )
    return success_response(PurInboundOut.model_validate(result))


@router.get("/inbounds/{inbound_id}", summary="采购入库单详情")
async def get_inbound(
    inbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurInboundOut]:
    """详情含明细与来源订单号；越权 403，不存在 404。"""
    return success_response(
        await PurchaseInboundService(db, tenant_id).get_inbound(inbound_id, current_user)
    )
