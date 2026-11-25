"""销售退货接口（SA-04）：/sales/returns 列表、详情、原单可退明细、草稿 CRUD 与确认。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.sal_return import (
    SalReturnCreate,
    SalReturnOut,
    SalReturnQuery,
    SalReturnSourceOut,
    SalReturnUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.sal_return import SalReturnService

router = APIRouter(prefix="/sales", tags=["销售管理-销售退货"])

LIST_PERM = "sal:return:list"


@router.get("/returns", summary="销售退货单列表")
async def list_returns(
    query: Annotated[SalReturnQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[SalReturnOut]]:
    return success_response(await SalReturnService(db, tenant_id).list_returns(query, current_user))


@router.get("/returns/source/{source_no}", summary="按原出库单号取可退明细")
async def get_source_outbound(
    source_no: str,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[SalReturnSourceOut]:
    return success_response(
        await SalReturnService(db, tenant_id).source_outbound(source_no, current_user)
    )


@router.get("/returns/{return_id}", summary="销售退货单详情")
async def get_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[SalReturnOut]:
    return success_response(
        await SalReturnService(db, tenant_id).get_return(return_id, current_user)
    )


@router.post("/returns", summary="新增销售退货单（草稿）")
async def create_return(
    payload: SalReturnCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:return:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalReturnOut]:
    service = SalReturnService(db, tenant_id)

    async def operation() -> SalReturnOut:
        return await service.create_return(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_sal_return",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(SalReturnOut.model_validate(result))


@router.put("/returns/{return_id}", summary="修改销售退货单草稿")
async def update_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:return:edit"))],
    payload: SalReturnUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalReturnOut]:
    service = SalReturnService(db, tenant_id)

    async def operation() -> SalReturnOut:
        return await service.update_return(return_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_sal_return",
        idempotency_key=idempotency_key,
        request_body={
            "id": return_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(SalReturnOut.model_validate(result))


@router.delete("/returns/{return_id}", summary="删除销售退货单草稿")
async def delete_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:return:delete"))],
) -> ApiResponse[None]:
    await SalReturnService(db, tenant_id).delete_return(return_id, current_user)
    return success_response(None)


@router.post("/returns/{return_id}/confirm", summary="确认销售退货（回补库存+冲减应收）")
async def confirm_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:return:confirm"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalReturnOut]:
    service = SalReturnService(db, tenant_id)

    async def operation() -> SalReturnOut:
        return await service.confirm_return(return_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="confirm_sal_return",
        idempotency_key=idempotency_key,
        request_body={"id": return_id},
        operation=operation,
    )
    return success_response(SalReturnOut.model_validate(result))
