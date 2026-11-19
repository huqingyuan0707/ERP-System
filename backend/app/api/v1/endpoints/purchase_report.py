"""采购报表端点（R-02）：/reports/purchase，只读查询与异步导出。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.purchase_report import PurchaseReportOut, PurchaseReportQuery
from app.schemas.task import TaskOut
from app.services.excel_export import ExcelExportService
from app.services.purchase_report import PurchaseReportService

router = APIRouter(prefix="/reports/purchase", tags=["报表-采购报表"])

REPORT_READER = Annotated[SysUser, Depends(require_perm("rpt:purchase:list"))]
REPORT_EXPORTER = Annotated[SysUser, Depends(require_perm("report:export"))]


@router.get("", summary="采购报表")
async def get_purchase_report(
    query: Annotated[PurchaseReportQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: REPORT_READER,
) -> ApiResponse[Any]:
    """明细/汇总/排行三种视图，退货以负数行展示，合计 = 入库 - 退货。"""
    return success_response(
        await PurchaseReportService(db, tenant_id).get_report(current_user, query)
    )


@router.post("/export", summary="采购报表导出")
async def export_purchase_report(
    query: Annotated[PurchaseReportQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: REPORT_EXPORTER,
) -> ApiResponse[TaskOut]:
    """按当前筛选条件提交异步导出任务，返回 taskId 供前端轮询下载。"""
    task = await ExcelExportService(db, tenant_id).export_purchase_report(
        created_by=current_user.id,
        query=query,
        user=current_user,
    )
    return success_response(task)
