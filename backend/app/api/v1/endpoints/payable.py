"""应付管理端点（F-02）：只读查询，不提供编辑/删除入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.payable import (
    AgingBucketOut,
    PayableDetailOut,
    PayableOut,
    PayableQuery,
    PayableSummary,
)
from app.services.payable import PayableService

router = APIRouter(prefix="/finance/payables", tags=["财务-应付管理"])

PayableReader = Annotated[SysUser, Depends(require_perm("fin:payable:list"))]


@router.get("", summary="应付列表")
async def list_payables(
    query: Annotated[PayableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: PayableReader,
) -> ApiResponse[PageResult[PayableOut]]:
    """分页查询应付；筛选与汇总/账龄同口径，数据范围由后端叠加。"""
    return success_response(await PayableService(db, tenant_id).list_payables(query, current_user))


@router.get("/summary", summary="应付汇总")
async def get_summary(
    query: Annotated[PayableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: PayableReader,
) -> ApiResponse[PayableSummary]:
    """汇总卡片：总额/已付/未付/应退预付/逾期未付。"""
    return success_response(await PayableService(db, tenant_id).get_summary(query, current_user))


@router.get("/aging", summary="应付账龄分桶")
async def get_aging(
    query: Annotated[PayableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: PayableReader,
) -> ApiResponse[list[AgingBucketOut]]:
    """按到期日分桶；负数余额单列「应退/预付」，不参与正数账龄。"""
    return success_response(await PayableService(db, tenant_id).get_aging(query, current_user))


@router.get("/{payable_id}", summary="应付详情")
async def get_payable(
    payable_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: PayableReader,
) -> ApiResponse[PayableDetailOut]:
    """详情含付款核销记录与冲减该应付的采购退货记录。"""
    return success_response(
        await PayableService(db, tenant_id).get_detail(payable_id, current_user)
    )
