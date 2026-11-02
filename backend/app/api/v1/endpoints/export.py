"""Excel 导入导出端点（C-07）。

路由前缀 /api/v1（不带 /sys），完整路径如：
  POST /api/v1/base/goods/import    商品导入
  GET  /api/v1/base/goods/export    商品导出
  GET  /api/v1/inventory/stocks/export  库存导出
  POST /api/v1/reports/export       通用报表导出（异步任务）
  GET  /api/v1/finance/statements/{id}/export  对账单导出
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.core.deps import CurrentUserDep, DbDep, TenantDep, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.goods import GoodsImportResult, GoodsQuery
from app.schemas.task import TaskOut
from app.services.auth import AuthService
from app.services.excel_export import EXPORT_DIR, ExcelExportService
from app.services.task import TaskService
from app.utils.importer import build_import_template


router = APIRouter(tags=["导入导出"])

# 导出/导入权限码
EXPORT_GOODS_PERM = "base:goods:export"
IMPORT_GOODS_PERM = "base:goods:import"
EXPORT_STOCK_PERM = "inv:stock:export"
EXPORT_REPORT_PERM = "report:export"

# 导入文件大小上限 5MB，与前端提示保持一致
IMPORT_MAX_SIZE = 5 * 1024 * 1024


@router.post("/base/goods/import", summary="商品导入")
async def import_goods(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(IMPORT_GOODS_PERM))],
    file: UploadFile = File(..., description="Excel 文件"),
) -> ApiResponse[GoodsImportResult]:
    """上传商品 Excel，逐行校验，全部成功才入库，失败返回逐行错误报告。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise AppException(
            ErrorCode.BAD_REQUEST, "仅支持 .xlsx 文件，请将 .xls 另存为 .xlsx 后上传"
        )
    content = await file.read(IMPORT_MAX_SIZE + 1)
    if len(content) > IMPORT_MAX_SIZE:
        raise AppException(ErrorCode.BAD_REQUEST, "文件大小不能超过 5MB")
    result = await ExcelExportService(db, tenant_id).import_goods(content, current_user.id)
    return success_response(result)


@router.get("/base/goods/export", summary="商品导出")
async def export_goods(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    query: Annotated[GoodsQuery, Query()],
    _: Annotated[SysUser, Depends(require_perm(EXPORT_GOODS_PERM))],
) -> ApiResponse[TaskOut]:
    """商品导出：按当前筛选条件提交异步任务，返回 taskId，前端轮询 /sys/tasks/{taskId}。"""
    task = await ExcelExportService(db, tenant_id).export_goods(
        created_by=current_user.id,
        query=query,
    )
    return success_response(task)


@router.get("/inventory/stocks/export", summary="库存导出")
async def export_stocks(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(EXPORT_STOCK_PERM))],
    warehouse_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> ApiResponse[TaskOut]:
    """库存导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db, tenant_id).export_stocks(
        created_by=current_user.id,
        warehouse_id=warehouse_id,
        keyword=keyword,
    )
    return success_response(task)


@router.post("/reports/export", summary="报表导出（异步）")
async def export_report(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(EXPORT_REPORT_PERM))],
    report_type: str = Query(..., description="报表类型 purchase/sales/inventory/finance"),
) -> ApiResponse[TaskOut]:
    """通用报表导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db, tenant_id).export_report(
        created_by=current_user.id,
        report_type=report_type,
    )
    return success_response(task)


@router.get("/finance/statements/{statement_id}/export", summary="对账单导出")
async def export_statement(
    statement_id: int,
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm(EXPORT_REPORT_PERM))],
) -> ApiResponse[TaskOut]:
    """对账单导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db, tenant_id).export_statement(
        created_by=current_user.id,
        statement_id=statement_id,
    )
    return success_response(task)


@router.get("/exports/download", summary="下载导出文件")
async def download_export(
    db: DbDep,
    current_user: CurrentUserDep,
    tenant_id: TenantDep,
    fileName: str = Query(..., description="导出文件名"),
):
    """兼容旧文件名入口；与 taskId 下载使用同一租户/owner 权限策略。"""
    codes = await AuthService(db).get_permission_codes(current_user.id)
    file_path = await TaskService(db, tenant_id).download_path(
        user_id=current_user.id,
        codes=codes,
        export_dir=EXPORT_DIR,
        file_name=fileName,
    )
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/base/goods/template", summary="下载商品导入模板")
@router.get(
    "/base/goods/import-template",
    include_in_schema=False,
    summary="下载商品导入模板（兼容别名）",
)
async def download_goods_template(
    _: Annotated[SysUser, Depends(require_perm(IMPORT_GOODS_PERM))],
):
    """下载商品导入Excel模板文件。"""
    content = build_import_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote("商品导入模板.xlsx"),
        },
    )
