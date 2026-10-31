"""Excel 导入导出端点（C-07）。

路由前缀 /api/v1（不带 /sys），完整路径如：
  POST /api/v1/base/goods/import    商品导入
  GET  /api/v1/base/goods/export    商品导出
  GET  /api/v1/inventory/stocks/export  库存导出
  POST /api/v1/reports/export       通用报表导出（异步任务）
  GET  /api/v1/finance/statements/{id}/export  对账单导出
"""

from pathlib import Path as _Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.core.deps import CurrentUserDep, DbDep, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.repositories.task import TaskRepository
from app.schemas.common import ApiResponse, success_response
from app.schemas.goods import GoodsImportResult, GoodsQuery
from app.schemas.task import TaskOut
from app.services.auth import AuthService
from app.services.excel_export import ExcelExportService
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
    file: UploadFile = File(..., description="Excel 文件"),
    _: Annotated[SysUser, Depends(require_perm(IMPORT_GOODS_PERM))] = None,
) -> ApiResponse[GoodsImportResult]:
    """上传商品 Excel，逐行校验，全部成功才入库，失败返回逐行错误报告。"""
    content = await file.read()
    if len(content) > IMPORT_MAX_SIZE:
        raise AppException(ErrorCode.BAD_REQUEST, "文件大小不能超过 5MB")
    result = await ExcelExportService(db).import_goods(content, current_user.id)
    return success_response(result)


@router.get("/base/goods/export", summary="商品导出")
async def export_goods(
    db: DbDep,
    current_user: CurrentUserDep,
    query: Annotated[GoodsQuery, Query()],
    _: Annotated[SysUser, Depends(require_perm(EXPORT_GOODS_PERM))] = None,
) -> ApiResponse[TaskOut]:
    """商品导出：按当前筛选条件提交异步任务，返回 taskId，前端轮询 /sys/tasks/{taskId}。"""
    task = await ExcelExportService(db).export_goods(
        created_by=current_user.id,
        query=query,
    )
    return success_response(task)


@router.get("/inventory/stocks/export", summary="库存导出")
async def export_stocks(
    db: DbDep,
    current_user: CurrentUserDep,
    warehouse_id: int | None = Query(default=None),
    keyword: str | None = Query(default=None),
    _: Annotated[SysUser, Depends(require_perm(EXPORT_STOCK_PERM))] = None,
) -> ApiResponse[TaskOut]:
    """库存导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db).export_stocks(
        created_by=current_user.id,
        warehouse_id=warehouse_id,
        keyword=keyword,
    )
    return success_response(task)


@router.post("/reports/export", summary="报表导出（异步）")
async def export_report(
    db: DbDep,
    current_user: CurrentUserDep,
    report_type: str = Query(..., description="报表类型 purchase/sales/inventory/finance"),
    _: Annotated[SysUser, Depends(require_perm(EXPORT_REPORT_PERM))] = None,
) -> ApiResponse[TaskOut]:
    """通用报表导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db).export_report(
        created_by=current_user.id,
        report_type=report_type,
    )
    return success_response(task)


@router.get(
    "/finance/statements/{statement_id}/export", summary="对账单导出"
)
async def export_statement(
    statement_id: int,
    db: DbDep,
    current_user: CurrentUserDep,
    _: Annotated[SysUser, Depends(require_perm(EXPORT_REPORT_PERM))] = None,
) -> ApiResponse[TaskOut]:
    """对账单导出：提交异步任务，返回 taskId。"""
    task = await ExcelExportService(db).export_statement(
        created_by=current_user.id,
        statement_id=statement_id,
    )
    return success_response(task)


@router.get("/exports/download", summary="下载导出文件")
async def download_export(
    db: DbDep,
    current_user: CurrentUserDep,
    fileName: str = Query(..., description="导出文件名"),
):
    """下载已生成的导出 Excel 文件。

    不再统一要求 report:export：商品/库存等各模块的导出人应能下载自己的文件；
    通过任务记录校验归属，只有具备 report:export 或 * 权限的用户可下载他人文件。
    """
    # 防止目录遍历攻击：只允许纯文件名，不允许路径分隔符
    if "/" in fileName or "\\" in fileName or ".." in fileName:
        raise AppException(ErrorCode.BAD_REQUEST, "非法文件名")
    task = await TaskRepository(db).get_by_file_name(fileName)
    if task is None:
        raise AppException(ErrorCode.NOT_FOUND, "文件不存在或已过期")
    codes = await AuthService(db).get_permission_codes(current_user.id)
    if (
        "*" not in codes
        and EXPORT_REPORT_PERM not in codes
        and task.created_by != current_user.id
    ):
        raise AppException(ErrorCode.FORBIDDEN, "无权下载该导出文件")
    file_path = _Path("exports") / fileName
    if not file_path.exists():
        raise AppException(ErrorCode.NOT_FOUND, "文件不存在或已过期")
    return FileResponse(
        path=str(file_path),
        filename=fileName,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/base/goods/template", summary="下载商品导入模板")
@router.get(
    "/base/goods/import-template",
    include_in_schema=False,
    summary="下载商品导入模板（兼容别名）",
)
async def download_goods_template(
    _: Annotated[SysUser, Depends(require_perm(IMPORT_GOODS_PERM))] = None,
):
    """下载商品导入Excel模板文件。"""
    content = build_import_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="商品导入模板.xlsx"',
        },
    )
