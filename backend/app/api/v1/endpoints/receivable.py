"""应收管理端点（F-01）：只读查询，不提供编辑/删除入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.receivable import (
    AgingBucketOut,
    ReceivableDetailOut,
    ReceivableOut,
    ReceivableQuery,
    ReceivableSummary,
)
from app.services.receivable import ReceivableService

router = APIRouter(prefix="/finance/receivables", tags=["财务-应收管理"])

ReceivableReader = Annotated[SysUser, Depends(require_perm("fin:receivable:list"))]


@router.get("", summary="应收列表")
async def list_receivables(
    query: Annotated[ReceivableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: ReceivableReader,
) -> ApiResponse[PageResult[ReceivableOut]]:
    """分页查询应收；筛选与汇总/账龄同口径，数据范围由后端叠加。"""
    return success_response(
        await ReceivableService(db, tenant_id).list_receivables(query, current_user)
    )


@router.get("/summary", summary="应收汇总")
async def get_summary(
    query: Annotated[ReceivableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: ReceivableReader,
) -> ApiResponse[ReceivableSummary]:
    """汇总卡片：总额/已收/未收/应退预收/逾期未收。"""
    return success_response(await ReceivableService(db, tenant_id).get_summary(query, current_user))


@router.get("/aging", summary="应收账龄分桶")
async def get_aging(
    query: Annotated[ReceivableQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: ReceivableReader,
) -> ApiResponse[list[AgingBucketOut]]:
    """按到期日分桶；负数余额单列「应退/预收」，不参与正数账龄。"""
    return success_response(await ReceivableService(db, tenant_id).get_aging(query, current_user))


@router.get("/{receivable_id}", summary="应收详情")
async def get_receivable(
    receivable_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: ReceivableReader,
) -> ApiResponse[ReceivableDetailOut]:
    """详情含收款核销记录与冲减该应收的销售退货记录。"""
    return success_response(
        await ReceivableService(db, tenant_id).get_detail(receivable_id, current_user)
    )
