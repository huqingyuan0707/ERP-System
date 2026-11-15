"""采购订单接口（P-02）：/purchase/orders 的列表、详情、草稿 CRUD、状态流转与导出。

约定：
  - 权限：查看复用菜单权限 pur:order:list（等价于需求文档的 pur:order:view），
    写接口分别用 pur:order:add/edit/submit/audit/close，导出用 report:export；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径 /orders/export 与 /orders/from-apply/{apply_id} 必须在 /{order_id} 之前声明，
    否则会被当成动态 order_id 解析成 422。
"""

from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Header, Query, Response

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.purchase_order import (
    PurOrderAudit,
    PurOrderClose,
    PurOrderCreate,
    PurOrderFromApplyOut,
    PurOrderOut,
    PurOrderQuery,
    PurOrderUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.purchase_order import PurchaseOrderService


router = APIRouter(prefix="/purchase", tags=["采购管理-采购订单"])

# 查看权限复用菜单权限码：与 init_db.py 播种的菜单/按钮权限保持一致
LIST_PERM = "pur:order:list"
# 导出权限：需求文档规定采购订单导出受 report:export 控制
EXPORT_PERM = "report:export"


@router.get("/orders", summary="采购订单列表")
async def list_orders(
    query: Annotated[PurOrderQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PageResult[PurOrderOut]]:
    """按状态、供应商、下单日期区间、来源申请单与关键字分页查询；自动叠加数据范围。"""
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


@router.get("/orders/export", summary="导出采购订单 Excel")
async def export_orders(
    query: Annotated[PurOrderQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm(EXPORT_PERM))],
) -> Response:
    """按当前筛选条件导出：与列表共用数据范围，导出动作写操作日志。"""
    content = await PurchaseOrderService(db, tenant_id).export_orders(query, current_user)
    filename = f"采购订单_{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


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
    """仅草稿可改；其余状态或版本号过期返回 409。"""
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


@router.post("/orders/{order_id}/submit", summary="提交采购订单（草稿→待审）")
async def submit_order(
    order_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:submit"))],
    version: Annotated[int | None, Query(ge=1, description="乐观锁版本号")] = None,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurOrderOut]:
    """提交前复核供应商/商品启用与明细；非草稿或重复提交返回 409。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> PurOrderOut:
        return await service.submit_order(
            order_id, current_user, version=version, commit=False
        )

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="submit_pur_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id, "version": version},
        operation=operation,
    )
    return success_response(PurOrderOut.model_validate(result))


@router.post("/orders/{order_id}/audit", summary="审核采购订单（通过/驳回）")
async def audit_order(
    order_id: int,
    payload: PurOrderAudit,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:audit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurOrderOut]:
    """通过：待审→已审；驳回：待审→草稿并写入驳回原因；非待审返回 409。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> PurOrderOut:
        return await service.audit_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="audit_pur_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id, **payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(PurOrderOut.model_validate(result))


@router.post("/orders/{order_id}/close", summary="关闭采购订单（剩余数量作废）")
async def close_order(
    order_id: int,
    payload: PurOrderClose,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:order:close"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurOrderOut]:
    """关闭后状态=关闭且不可再入库；已完成订单不可关闭，重复关闭返回 409。"""
    service = PurchaseOrderService(db, tenant_id)

    async def operation() -> PurOrderOut:
        return await service.close_order(order_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="close_pur_order",
        idempotency_key=idempotency_key,
        request_body={"id": order_id, **payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(PurOrderOut.model_validate(result))


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
    current_user: Annotated[SysUser, Depends(require_perm(LIST_PERM))],
) -> ApiResponse[PurOrderOut]:
    """详情含明细、累计入库数量与进度；越权 403，不存在 404。"""
    return success_response(
        await PurchaseOrderService(db, tenant_id).get_order(order_id, current_user)
    )
