"""采购申请接口（P-01）：/purchase/applies 的草稿 CRUD、提交与审核。

约定：
  - 权限：list 用 pur:apply:list（菜单已存在），写接口分别用 add/edit/submit/audit；
  - 返回体统一 ApiResponse + success_response，分页 PageResult（对外 camelCase）；
  - 事务边界在 Service，写接口通过 IdempotencyService 包装并支持 Idempotency-Key；
  - 静态路径（无动态段）只有列表与新增，其余均为 /{apply_id} 动态路径，无路由冲突。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.purchase_apply import (
    PurApplyAudit,
    PurApplyCreate,
    PurApplyOut,
    PurApplyQuery,
    PurApplyUpdate,
)
from app.services.idempotency import IdempotencyService
from app.services.purchase_apply import PurchaseApplyService


# 采购申请路由：先声明 Router，后续接口装饰器全部挂在它上面。
# 这里保留两个空行：与仓库 isort(lines-after-imports=2) 和 black 的「导入块后接定义」规则一致。
router = APIRouter(prefix="/purchase", tags=["采购管理-采购申请"])


@router.get("/applies", summary="采购申请列表")
async def list_applies(
    query: Annotated[PurApplyQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:list"))],
) -> ApiResponse[PageResult[PurApplyOut]]:
    """按状态、部门、需求日期区间与关键字分页查询；自动叠加当前用户数据范围。"""
    return success_response(
        await PurchaseApplyService(db, tenant_id).list_applies(query, current_user)
    )


@router.post("/applies", summary="新增采购申请（草稿）")
async def create_apply(
    payload: PurApplyCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:add"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurApplyOut]:
    """新增即草稿；单号自动生成，部门/申请人默认当前用户。"""
    service = PurchaseApplyService(db, tenant_id)

    async def operation() -> PurApplyOut:
        # commit=False：由幂等包装在成功后统一提交，业务与幂等结果同事务
        return await service.create_apply(payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="create_pur_apply",
        idempotency_key=idempotency_key,
        request_body=payload,
        operation=operation,
    )
    return success_response(PurApplyOut.model_validate(result))


@router.put("/applies/{apply_id}", summary="修改采购申请草稿")
async def update_apply(
    apply_id: int,
    payload: PurApplyUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:edit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurApplyOut]:
    """仅草稿可改；待审/已审返回 409，需先由审核人驳回。"""
    service = PurchaseApplyService(db, tenant_id)

    async def operation() -> PurApplyOut:
        return await service.update_apply(apply_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="update_pur_apply",
        idempotency_key=idempotency_key,
        request_body={
            "id": apply_id,
            "payload": payload.model_dump(mode="json", exclude_unset=True),
        },
        operation=operation,
    )
    return success_response(PurApplyOut.model_validate(result))


@router.delete("/applies/{apply_id}", summary="删除采购申请草稿")
async def delete_apply(
    apply_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:delete"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[None]:
    """软删草稿（主表 + 明细），历史流水保留；待审/已审返回 409。

    删除使用独立权限码 pur:apply:delete，与编辑权限分离，
    避免「能改草稿」的角色默认具备删除能力。
    """
    service = PurchaseApplyService(db, tenant_id)

    async def operation() -> None:
        return await service.delete_apply(apply_id, current_user, commit=False)

    await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="delete_pur_apply",
        idempotency_key=idempotency_key,
        request_body={"id": apply_id},
        operation=operation,
    )
    return success_response(None)


@router.post("/applies/{apply_id}/submit", summary="提交采购申请（草稿→待审）")
async def submit_apply(
    apply_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:submit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurApplyOut]:
    """提交前复核商品启用与数量；非草稿状态返回 409。"""
    service = PurchaseApplyService(db, tenant_id)

    async def operation() -> PurApplyOut:
        return await service.submit_apply(apply_id, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="submit_pur_apply",
        idempotency_key=idempotency_key,
        request_body={"id": apply_id},
        operation=operation,
    )
    return success_response(PurApplyOut.model_validate(result))


@router.post("/applies/{apply_id}/audit", summary="审核采购申请（通过/驳回）")
async def audit_apply(
    apply_id: int,
    payload: PurApplyAudit,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:audit"))],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ] = None,
) -> ApiResponse[PurApplyOut]:
    """通过：待审→已审；驳回：待审→草稿并写入驳回原因；非待审状态返回 409。"""
    service = PurchaseApplyService(db, tenant_id)

    async def operation() -> PurApplyOut:
        return await service.audit_apply(apply_id, payload, current_user, commit=False)

    result = await IdempotencyService(db).execute(
        tenant_id=tenant_id,
        user_id=current_user.id,
        biz_type="audit_pur_apply",
        idempotency_key=idempotency_key,
        request_body={"id": apply_id, **payload.model_dump(mode="json")},
        operation=operation,
    )
    return success_response(PurApplyOut.model_validate(result))


@router.get("/applies/{apply_id}", summary="采购申请详情")
async def get_apply(
    apply_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("pur:apply:list"))],
) -> ApiResponse[PurApplyOut]:
    """详情含明细（商品编码/名称/规格/单位），供详情弹窗与打印使用。"""
    return success_response(
        await PurchaseApplyService(db, tenant_id).get_apply(apply_id, current_user)
    )
