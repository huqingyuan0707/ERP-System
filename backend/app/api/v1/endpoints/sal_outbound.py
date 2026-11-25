"""销售出库接口（SA-03）：/sales/outbounds 列表、详情、按订单预填、草稿 CRUD 与确认。

静态路径 /outbounds/from-order/{order_id} 先于 /{outbound_id} 注册。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.sal_outbound import (
    SalOutboundCreate,
    SalOutboundFromOrderOut,
    SalOutboundOut,
    SalOutboundQuery,
    SalOutboundUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.sal_outbound import SalOutboundService

router = APIRouter(prefix="/sales", tags=["销售管理-销售出库"])

LIST_PERM = "sal:outbound:list"


@router.get("/outbounds", summary="销售出库单列表")
async def list_outbounds(
    query: Annotated[SalOutboundQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[SalOutboundOut]]:
    return success_response(
        await SalOutboundService(db, tenant_id).list_outbounds(query, current_user)
    )


@router.get("/outbounds/from-order/{order_id}", summary="按已审销售订单生成出库预填数据")
async def get_outbound_from_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[SalOutboundFromOrderOut]:
    return success_response(
        await SalOutboundService(db, tenant_id).build_from_order(order_id, current_user)
    )


@router.get("/outbounds/{outbound_id}", summary="销售出库单详情")
async def get_outbound(
    outbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[SalOutboundOut]:
    return success_response(
        await SalOutboundService(db, tenant_id).get_outbound(outbound_id, current_user)
    )


@router.post("/outbounds", summary="新增销售出库单（草稿并锁定库存）")
async def create_outbound(
    payload: SalOutboundCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:outbound:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOutboundOut]:
    service = SalOutboundService(db, tenant_id)

    async def operation() -> SalOutboundOut:
        return await service.create_outbound(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_sal_outbound",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(SalOutboundOut.model_validate(result))


@router.put("/outbounds/{outbound_id}", summary="修改销售出库单草稿（同步调整锁定）")
async def update_outbound(
    outbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:outbound:edit"))],
    payload: SalOutboundUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOutboundOut]:
    service = SalOutboundService(db, tenant_id)

    async def operation() -> SalOutboundOut:
        return await service.update_outbound(outbound_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_sal_outbound",
        idempotency_key=idempotency_key,
        request_body={
            "id": outbound_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(SalOutboundOut.model_validate(result))


@router.delete("/outbounds/{outbound_id}", summary="删除销售出库单草稿（释放锁定）")
async def delete_outbound(
    outbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:outbound:delete"))],
) -> ApiResponse[None]:
    await SalOutboundService(db, tenant_id).delete_outbound(outbound_id, current_user)
    return success_response(None)


@router.post("/outbounds/{outbound_id}/confirm", summary="确认销售出库（扣库存+回写+应收）")
async def confirm_outbound(
    outbound_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:outbound:confirm"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOutboundOut]:
    service = SalOutboundService(db, tenant_id)

    async def operation() -> SalOutboundOut:
        return await service.confirm_outbound(outbound_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="confirm_sal_outbound",
        idempotency_key=idempotency_key,
        request_body={"id": outbound_id},
        operation=operation,
    )
    return success_response(SalOutboundOut.model_validate(result))
