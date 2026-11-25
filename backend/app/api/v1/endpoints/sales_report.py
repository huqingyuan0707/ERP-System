"""销售报表端点（R-03）：/reports/sales，只读查询与异步导出。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.sales_report import SalesReportOut, SalesReportQuery
from app.schemas.task import TaskOut
from app.services.excel_export import ExcelExportService
from app.services.sales_report import SalesReportService

router = APIRouter(prefix="/reports/sales", tags=["报表-销售报表"])

REPORT_READER = Annotated[SysUser, Depends(require_perm("rpt:sales:list"))]
REPORT_EXPORTER = Annotated[SysUser, Depends(require_perm("report:export"))]


@router.get("", summary="销售报表")
async def get_sales_report(
    query: Annotated[SalesReportQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: REPORT_READER,
) -> ApiResponse[Any]:
    """明细/汇总/客户排行/商品排行四视图；退货以负数行，合计 = 出库 - 退货。"""
    return success_response(await SalesReportService(db, tenant_id).get_report(current_user, query))


@router.post("/export", summary="销售报表导出")
async def export_sales_report(
    query: Annotated[SalesReportQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    current_user: REPORT_EXPORTER,
) -> ApiResponse[TaskOut]:
    task = await ExcelExportService(db, tenant_id).export_sales_report(
        created_by=current_user.id,
        query=query,
        user=current_user,
    )
    return success_response(task)
