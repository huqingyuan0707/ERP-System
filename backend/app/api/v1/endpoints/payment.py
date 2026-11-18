"""付款管理接口（F-04）：/finance/payments 的列表、汇总、详情、CRUD 与核销。

约定（与 P-04 采购退货 / F-02 应付管理保持一致）：
  - 权限：查看复用菜单权限 fin:payment:list，写接口分别用 fin:payment:add/edit/writeoff
    （删除复用 edit，与 P-01/P-04 的做法一致）；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径 /summary 必须在 /{payment_id} 之前声明，否则会被当成动态 ID 解析成 422。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.payment import (
    PaymentCreate,
    PaymentOut,
    PaymentQuery,
    PaymentSummary,
    PaymentUpdate,
    WriteoffCreate,
)
from app.services.idempotency import IdempotencyService
from app.services.payment import PaymentService


router = APIRouter(prefix="/finance/payments", tags=["财务-付款管理"])

# 查看权限复用菜单权限码：与 init_db.py 播种的菜单/按钮权限保持一致
LIST_PERM = "fin:payment:list"


@router.get("", summary="付款单列表")
async def list_payments(
    query: Annotated[PaymentQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[PaymentOut]]:
    """按供应商、核销进度、付款日期区间与关键字分页查询；自动叠加数据范围。"""
    return success_response(await PaymentService(db, tenant_id).list_payments(query, current_user))


@router.get("/summary", summary="付款汇总")
async def get_summary(
    query: Annotated[PaymentQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PaymentSummary]:
    """汇总卡片：付款总额/已核销/未分配/笔数，与列表同筛选、同数据范围。"""
    return success_response(await PaymentService(db, tenant_id).get_summary(query, current_user))


@router.post("", summary="新增付款单（待核销）")
async def create_payment(
    payload: PaymentCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:payment:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PaymentOut]:
    """新增即待核销；单号自动生成，不直接改变应付。"""
    service = PaymentService(db, tenant_id)

    async def operation() -> PaymentOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_payment(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_payment",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(PaymentOut.model_validate(result))


@router.put("/{payment_id}", summary="修改付款单（未核销）")
async def update_payment(
    payment_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:payment:edit"))],
    payload: PaymentUpdate = Body(...),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PaymentOut]:
    """仅未核销可改；已核销或版本号过期返回 409。"""
    service = PaymentService(db, tenant_id)

    async def operation() -> PaymentOut:
        return await service.update_payment(payment_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_payment",
        idempotency_key=idempotency_key,
        request_body={
            "id": payment_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(PaymentOut.model_validate(result))


@router.delete("/{payment_id}", summary="删除付款单（未核销）")
async def delete_payment(
    payment_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:payment:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删未核销付款单；已核销返回 409，核销明细保留。"""
    service = PaymentService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_payment(payment_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_payment",
        idempotency_key=idempotency_key,
        request_body={"id": payment_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/{payment_id}/writeoff", summary="付款核销（冲减应付）")
async def writeoff_payment(
    payment_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("fin:payment:writeoff"))],
    payload: WriteoffCreate,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PaymentOut]:
    """核销事务：校验供应商一致/单条≤未付/总额≤未分配 → 写核销明细 → 冲减应付。

    超未付、超未分配余额 422；跨供应商、已核销完成重复核销 409；失败整体回滚。
    """
    service = PaymentService(db, tenant_id)

    async def operation() -> PaymentOut:
        return await service.writeoff_payment(payment_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="writeoff_payment",
        idempotency_key=idempotency_key,
        request_body={
            "id": payment_id,
            "payload": payload.model_dump(mode="json"),
        },
        operation=operation,
    )
    return success_response(PaymentOut.model_validate(result))


@router.get("/{payment_id}", summary="付款单详情")
async def get_payment(
    payment_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PaymentOut]:
    """详情含核销明细（应付单号/核销金额/核销时间）；越权 403，不存在 404。"""
    return success_response(
        await PaymentService(db, tenant_id).get_detail(payment_id, current_user)
    )
