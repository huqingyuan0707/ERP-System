"""销售报价接口（SA-01）：/sales/quotations 的草稿 CRUD、发送、作废（含 G2 补齐的详情/编辑/删除）。

约定：
  - 权限：list 用 sal:quotation:list（菜单已存在），写接口分别用 add/edit/delete；
    发送与作废属于单据状态变更，归入 edit（需求条目只定义 view/add/edit/delete 四个码）；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径（无动态段）只有列表与新增，其余均为 /{quotation_id} 动态路径，无路由冲突。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.sal_quotation import (
    SalQuotationCreate,
    SalQuotationOut,
    SalQuotationQuery,
    SalQuotationUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.sal_quotation import SalQuotationService


# 销售报价路由：先声明 Router，后续接口装饰器全部挂在它上面。
# 这里保留两个空行：与仓库 isort(lines-after-imports=2) 和 black 的「导入块后接定义」规则一致。
router = APIRouter(prefix="/sales", tags=["销售管理-销售报价"])


@router.get("/quotations", summary="销售报价列表")
async def list_quotations(
    query: Annotated[SalQuotationQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:list"))],
) -> ApiResponse[PageResult[SalQuotationOut]]:
    """按状态、报价日期区间与关键字（报价号/客户名称）分页查询；自动叠加当前用户数据范围。"""
    return success_response(
        await SalQuotationService(db, tenant_id).list_quotations(query, current_user)
    )


@router.post("/quotations", summary="新增销售报价（草稿）")
async def create_quotation(
    payload: SalQuotationCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalQuotationOut]:
    """新增即草稿；报价号自动生成（QT+yyyyMMdd+4 位），金额由后端按明细重算。"""
    service = SalQuotationService(db, tenant_id)

    async def operation() -> SalQuotationOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_quotation(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_sal_quotation",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(SalQuotationOut.model_validate(result))


@router.put("/quotations/{quotation_id}", summary="修改销售报价草稿（G2 补齐）")
async def update_quotation(
    quotation_id: int,
    payload: SalQuotationUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalQuotationOut]:
    """仅草稿可改；已发送/已失效返回 409。"""
    service = SalQuotationService(db, tenant_id)

    async def operation() -> SalQuotationOut:
        return await service.update_quotation(quotation_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_sal_quotation",
        idempotency_key=idempotency_key,
        request_body={
            "id": quotation_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(SalQuotationOut.model_validate(result))


@router.delete("/quotations/{quotation_id}", summary="删除销售报价草稿（G2 补齐）")
async def delete_quotation(
    quotation_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史保留；已发送/已失效返回 409。"""
    service = SalQuotationService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_quotation(quotation_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_sal_quotation",
        idempotency_key=idempotency_key,
        request_body={"id": quotation_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/quotations/{quotation_id}/send", summary="发送销售报价（草稿→已发送）")
async def send_quotation(
    quotation_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalQuotationOut]:
    """发送前复核明细与商品启用；已过有效期（valid_until < 今天）返回 409 并提示。"""
    service = SalQuotationService(db, tenant_id)

    async def operation() -> SalQuotationOut:
        return await service.send_quotation(quotation_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="send_sal_quotation",
        idempotency_key=idempotency_key,
        request_body={"id": quotation_id},
        operation=operation,
    )
    return success_response(SalQuotationOut.model_validate(result))


@router.post("/quotations/{quotation_id}/void", summary="作废销售报价（→已失效）")
async def void_quotation(
    quotation_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[SalQuotationOut]:
    """草稿/已发送可作废为已失效（终态）；已失效重复作废返回 409。"""
    service = SalQuotationService(db, tenant_id)

    async def operation() -> SalQuotationOut:
        return await service.void_quotation(quotation_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="void_sal_quotation",
        idempotency_key=idempotency_key,
        request_body={"id": quotation_id},
        operation=operation,
    )
    return success_response(SalQuotationOut.model_validate(result))


@router.get("/quotations/{quotation_id}", summary="销售报价详情（G2 补齐）")
async def get_quotation(
    quotation_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("sal:quotation:list"))],
) -> ApiResponse[SalQuotationOut]:
    """详情含明细（商品编码/名称/规格/单位），供详情弹窗、打印与复制预填订单使用。"""
    return success_response(
        await SalQuotationService(db, tenant_id).get_quotation(quotation_id, current_user)
    )
