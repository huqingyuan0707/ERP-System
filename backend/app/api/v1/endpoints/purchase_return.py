"""采购退货接口（P-04）：/purchase/returns 的列表、详情、原单可退查询、草稿 CRUD 与确认。

约定：
  - 权限：查看复用菜单权限 pur:return:list（等价于需求文档的 pur:return:view），
    写接口分别用 pur:return:add/edit/confirm（删除复用 edit，与 P-01/P-02 的做法一致）；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径 /returns/source 必须在 /{return_id} 之前声明，
    否则会被当成动态 return_id 解析成 422。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.purchase_return import (
    PurReturnCreate,
    PurReturnOut,
    PurReturnQuery,
    PurReturnUpdate,
    SourceInboundOut,
)
from app.services.idempotency import IdempotencyService
from app.services.purchase_return import PurchaseReturnService


router = APIRouter(prefix="/purchase", tags=["采购管理-采购退货"])

# 查看权限复用菜单权限码：与 init_db.py 播种的菜单/按钮权限保持一致
LIST_PERM = "pur:return:list"


@router.get("/returns", summary="采购退货单列表")
async def list_returns(
    query: Annotated[PurReturnQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[PurReturnOut]]:
    """按状态、供应商、仓库、原入库单号与退货日期区间分页查询；自动叠加数据范围。"""
    return success_response(
        await PurchaseReturnService(db, tenant_id).list_returns(query, current_user)
    )


@router.get("/returns/source", summary="按原入库单号查询可退数据")
async def get_return_source(
    source_no: Annotated[str, Query(min_length=1, max_length=64)],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[SourceInboundOut]:
    """只读：返回原入库/已退/可退数量（按商品聚合）与原应付快照，供新建退货单预填与展示。"""
    return success_response(
        await PurchaseReturnService(db, tenant_id).get_source_inbound(source_no, current_user)
    )


@router.post("/returns", summary="新增采购退货单（草稿）")
async def create_return(
    payload: PurReturnCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:return:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurReturnOut]:
    """新增即草稿；单号自动生成，金额由后端按明细重算。"""
    service = PurchaseReturnService(db, tenant_id)

    async def operation() -> PurReturnOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_return(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_pur_return",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(PurReturnOut.model_validate(result))


@router.put("/returns/{return_id}", summary="修改采购退货单草稿")
async def update_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:return:edit"))],
    payload: PurReturnUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurReturnOut]:
    """仅草稿可改；其余状态或版本号过期返回 409。"""
    service = PurchaseReturnService(db, tenant_id)

    async def operation() -> PurReturnOut:
        return await service.update_return(return_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_pur_return",
        idempotency_key=idempotency_key,
        request_body={
            "id": return_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(PurReturnOut.model_validate(result))


@router.delete("/returns/{return_id}", summary="删除采购退货单草稿")
async def delete_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:return:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史流水保留；非草稿返回 409。"""
    service = PurchaseReturnService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_return(return_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_pur_return",
        idempotency_key=idempotency_key,
        request_body={"id": return_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/returns/{return_id}/confirm", summary="确认采购退货（减库存+冲减应付）")
async def confirm_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:return:confirm"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurReturnOut]:
    """确认事务：校验可退数量/可用量/退货金额 → 减库存并写流水 → 按 source_no 冲减原应付。

    超可退量、超可用量、退货金额超过原应付余额、重复确认均返回 409，且库存/应付保持不变。
    """
    service = PurchaseReturnService(db, tenant_id)

    async def operation() -> PurReturnOut:
        return await service.confirm_return(return_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="confirm_pur_return",
        idempotency_key=idempotency_key,
        request_body={"id": return_id},
        operation=operation,
    )
    return success_response(PurReturnOut.model_validate(result))


@router.get("/returns/{return_id}", summary="采购退货单详情")
async def get_return(
    return_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurReturnOut]:
    """详情含明细与原入库/已退/可退数量；越权 403，不存在 404。"""
    return success_response(
        await PurchaseReturnService(db, tenant_id).get_return(return_id, current_user)
    )
