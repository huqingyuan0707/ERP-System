"""销售订单接口（SAL-02/04~09/15）：/sales/orders 的草稿 CRUD、状态机、转单与信用概览。

约定：
  - 权限：list/credit 用 sal:order:list，写接口分别用 add/edit/delete/submit/audit/close；
    报价转单属于「创建订单」动作，归入 sal:order:add；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 路由顺序：/orders/credit-info 与 /orders/from-quotation/{quotation_id} 是静态段，
    必须先于 /orders/{order_id} 注册，否则动态段会吞掉静态路径（422）；
  - 报价转单 POST /sales/quotations/{quotation_id}/convert 与报价模块的
    /sales/quotations/{quotation_id}（GET 详情）路径段数不同，无冲突。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.sal_order import (
    CreditSummaryOut,
    SalOrderAudit,
    SalOrderClose,
    SalOrderConvert,
    SalOrderCreate,
    SalOrderFromQuotationOut,
    SalOrderOut,
    SalOrderQuery,
    SalOrderUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.sal_order import SalOrderService

# 销售订单路由：先声明 Router，后续接口装饰器全部挂在它上面。
router = APIRouter(prefix="/sales", tags=["销售管理-销售订单"])


@router.get("/orders", summary="销售订单列表")
async def list_orders(
    query: Annotated[SalOrderQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:list"))],
) -> ApiResponse[PageResult[SalOrderOut]]:
    """按状态、客户、下单日期区间与关键字（订单号/客户名称）分页查询；含出库进度汇总。"""
    return success_response(await SalOrderService(db, tenant_id).list_orders(query, current_user))


@router.get("/orders/credit-info", summary="客户信用额度概览（SAL-15）")
async def credit_info(
    # 对外 camelCase（与全局查询参数约定一致），内部仍叫 customer_id
    customer_id: Annotated[int, Query(alias="customerId", gt=0)],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:list"))],
) -> ApiResponse[CreditSummaryOut]:
    """返回客户额度、已占用（未结清应收 + 已审未出库订单）与可用余额，供订单页展示。"""
    summary = await SalOrderService(db, tenant_id).credit_info(customer_id, current_user)
    return success_response(CreditSummaryOut.model_validate(summary))


@router.get("/orders/from-quotation/{quotation_id}", summary="报价转订单预填（只读）")
async def build_from_quotation(
    quotation_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:add"))],
) -> ApiResponse[SalOrderFromQuotationOut]:
    """按已发送报价单组装订单预填数据（客户、明细、价格、信用概览），不落库不改状态。"""
    return success_response(
        await SalOrderService(db, tenant_id).build_from_quotation(quotation_id, current_user)
    )


@router.post("/orders", summary="新增销售订单（草稿）")
async def create_order(
    payload: SalOrderCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """新增即草稿；订单号自动生成（SO+yyyyMMdd+4 位），金额由后端按明细重算并做信用预检。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_order(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_sal_order",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.post("/quotations/{quotation_id}/convert", summary="报价转销售订单（SAL-02）")
async def convert_quotation(
    quotation_id: int,
    payload: SalOrderConvert,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """明细与价格从报价单复制生成订单草稿；报价状态置「已转订单」，重复转单返回 409。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        # 报价与订单两张表在同一事务提交，由幂等包装统一 commit
        return await service.convert_quotation(quotation_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="convert_sal_quotation",
        idempotency_key=idempotency_key,
        request_body={"quotationId": quotation_id, "payload": payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.put("/orders/{order_id}", summary="修改销售订单草稿")
async def update_order(
    order_id: int,
    payload: SalOrderUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """仅草稿可改；已审/部分出库/完成/关闭返回 409。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        return await service.update_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_sal_order",
        idempotency_key=idempotency_key,
        request_body={
            "id": order_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.delete("/orders/{order_id}", summary="删除销售订单草稿")
async def delete_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    # 删除复用 edit 权限码（与采购订单 pur:order:delete 缺省的约定一致）
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史保留；非草稿状态返回 409。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_order(order_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_sal_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/orders/{order_id}/submit", summary="提交销售订单（草稿→待审）")
async def submit_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:submit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """提交前复核明细与客户启用；空明细不可提交，重复提交返回 409。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        return await service.submit_order(order_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="submit_sal_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id},
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.post("/orders/{order_id}/audit", summary="审核销售订单（通过/驳回）")
async def audit_order(
    order_id: int,
    payload: SalOrderAudit,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:audit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """通过时做 SAL-15 信用校验（超额 409）；驳回回草稿且原因必填。

    锁库存按表结构设计发生在出库草稿（见 SalOrderService 模块说明），审核只推进状态。
    """
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        return await service.audit_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="audit_sal_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id, "payload": payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.post("/orders/{order_id}/close", summary="关闭销售订单（剩余数量作废）")
async def close_order(
    order_id: int,
    payload: SalOrderClose,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:close"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalOrderOut]:
    """已审/部分出库可关闭并记录原因；完成与重复关闭返回 409。"""
    service = SalOrderService(db, tenant_id)

    async def operation() -> SalOrderOut:
        return await service.close_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="close_sal_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id, "payload": payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(SalOrderOut.model_validate(result))


@router.get("/orders/{order_id}", summary="销售订单详情（含出库进度）")
async def get_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:order:list"))],
) -> ApiResponse[SalOrderOut]:
    """详情含明细（商品/单位/已出库/剩余数量）与订单级出库进度，供详情弹窗与出库参照。"""
    return success_response(await SalOrderService(db, tenant_id).get_order(order_id, current_user))
