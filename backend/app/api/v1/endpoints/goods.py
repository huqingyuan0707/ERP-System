"""基础资料端点（商品 / 商品分类 / 计量单位 CRUD + 导入导出）。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from app.core.deps import DbDep, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.goods import (
    GoodsCategoryCreate,
    GoodsCategoryOut,
    GoodsCategoryQuery,
    GoodsCategoryUpdate,
    GoodsCreate,
    GoodsImportResult,
    GoodsOut,
    GoodsQuery,
    GoodsUpdate,
    UnitCreate,
    UnitOut,
    UnitQuery,
    UnitUpdate,
)
from app.services.goods import GoodsCategoryService, GoodsService, UnitService
from app.utils.excel import build_workbook


router = APIRouter(prefix="/base", tags=["基础资料-商品"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
IMPORT_MAX_SIZE = 5 * 1024 * 1024  # 5MB


# ---------- 商品 ----------


@router.get("/goods/export", summary="导出商品")
async def export_goods(
    query: Annotated[GoodsQuery, Query()],
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:export"))],
) -> Response:
    headers, rows = await GoodsService(db).export_goods(query)
    content = build_workbook(headers, rows, sheet_title="商品列表")
    filename = f"goods_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/goods/import-template", summary="下载商品导入模板")
async def download_import_template(
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:import"))],
) -> Response:
    content = GoodsService(db).build_import_template()
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="goods_import_template.xlsx"'},
    )


@router.post("/goods/import", summary="导入商品")
async def import_goods(
    db: DbDep,
    file: Annotated[UploadFile, File()],
    _: Annotated[SysUser, Depends(require_perm("base:goods:import"))],
) -> ApiResponse[GoodsImportResult]:
    content = await file.read()
    if len(content) > IMPORT_MAX_SIZE:
        raise AppException(ErrorCode.BAD_REQUEST, "文件大小不能超过 5MB")
    result = await GoodsService(db).import_goods(content)
    return success_response(result)


@router.get("/goods", summary="商品列表")
async def list_goods(
    query: Annotated[GoodsQuery, Query()],
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:list"))],
) -> ApiResponse[PageResult[GoodsOut]]:
    result = await GoodsService(db).list_goods(query)
    return success_response(result)


@router.post("/goods", summary="新增商品")
async def create_goods(
    payload: GoodsCreate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:add"))],
) -> ApiResponse[GoodsOut]:
    result = await GoodsService(db).create_goods(payload)
    return success_response(result)


@router.get("/goods/{goods_id}", summary="商品详情")
async def get_goods(
    goods_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:list"))],
) -> ApiResponse[GoodsOut]:
    result = await GoodsService(db).get_goods(goods_id)
    return success_response(result)


@router.put("/goods/{goods_id}", summary="修改商品")
async def update_goods(
    goods_id: int,
    payload: GoodsUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:edit"))],
) -> ApiResponse[GoodsOut]:
    result = await GoodsService(db).update_goods(goods_id, payload)
    return success_response(result)


@router.delete("/goods/{goods_id}", summary="删除商品")
async def delete_goods(
    goods_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:delete"))],
) -> ApiResponse[None]:
    await GoodsService(db).delete_goods(goods_id)
    return success_response(None)


# ---------- 商品分类 ----------


@router.get("/categories", summary="商品分类列表")
async def list_categories(
    query: Annotated[GoodsCategoryQuery, Query()],
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:list"))],
) -> ApiResponse[list[GoodsCategoryOut]]:
    result = await GoodsCategoryService(db).list_categories(query)
    return success_response(result)


@router.post("/categories", summary="新增商品分类")
async def create_category(
    payload: GoodsCategoryCreate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:add"))],
) -> ApiResponse[GoodsCategoryOut]:
    result = await GoodsCategoryService(db).create_category(payload)
    return success_response(result)


@router.get("/categories/{category_id}", summary="商品分类详情")
async def get_category(
    category_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:list"))],
) -> ApiResponse[GoodsCategoryOut]:
    result = await GoodsCategoryService(db).get_category(category_id)
    return success_response(result)


@router.put("/categories/{category_id}", summary="修改商品分类")
async def update_category(
    category_id: int,
    payload: GoodsCategoryUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:edit"))],
) -> ApiResponse[GoodsCategoryOut]:
    result = await GoodsCategoryService(db).update_category(category_id, payload)
    return success_response(result)


@router.delete("/categories/{category_id}", summary="删除商品分类")
async def delete_category(
    category_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:delete"))],
) -> ApiResponse[None]:
    await GoodsCategoryService(db).delete_category(category_id)
    return success_response(None)


# ---------- 计量单位 ----------


@router.get("/units", summary="计量单位列表")
async def list_units(
    query: Annotated[UnitQuery, Query()],
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:list"))],
) -> ApiResponse[list[UnitOut]]:
    result = await UnitService(db).list_units(query)
    return success_response(result)


@router.post("/units", summary="新增计量单位")
async def create_unit(
    payload: UnitCreate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:add"))],
) -> ApiResponse[UnitOut]:
    result = await UnitService(db).create_unit(payload)
    return success_response(result)


@router.get("/units/{unit_id}", summary="计量单位详情")
async def get_unit(
    unit_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:list"))],
) -> ApiResponse[UnitOut]:
    result = await UnitService(db).get_unit(unit_id)
    return success_response(result)


@router.put("/units/{unit_id}", summary="修改计量单位")
async def update_unit(
    unit_id: int,
    payload: UnitUpdate,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:edit"))],
) -> ApiResponse[UnitOut]:
    result = await UnitService(db).update_unit(unit_id, payload)
    return success_response(result)


@router.delete("/units/{unit_id}", summary="删除计量单位")
async def delete_unit(
    unit_id: int,
    db: DbDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:delete"))],
) -> ApiResponse[None]:
    await UnitService(db).delete_unit(unit_id)
    return success_response(None)
