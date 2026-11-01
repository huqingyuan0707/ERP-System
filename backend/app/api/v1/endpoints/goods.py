"""基础资料端点（商品 / 商品分类 / 计量单位 CRUD）。

导入导出统一由 export.py 按 C-07 异步任务实现，本模块只保留业务 CRUD，
避免同一路径重复注册导致路由被前者遮蔽。
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, get_redis, require_perm
from app.core.exceptions import AppException, ErrorCode
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, PageResult, success_response
from app.schemas.goods import (
    GoodsCategoryCreate,
    GoodsCategoryOut,
    GoodsCategoryQuery,
    GoodsCategoryUpdate,
    GoodsCreate,
    GoodsOut,
    GoodsQuery,
    GoodsUpdate,
    UnitCreate,
    UnitOut,
    UnitQuery,
    UnitUpdate,
)
from app.services.goods import GoodsCategoryService, GoodsService, UnitService


router = APIRouter(prefix="/base", tags=["基础资料-商品"])


# ---------- 商品 ----------


@router.get("/goods", summary="商品列表")
async def list_goods(
    query: Annotated[GoodsQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:list"))],
) -> ApiResponse[PageResult[GoodsOut]]:
    result = await GoodsService(db, tenant_id=tenant_id).list_goods(query)
    return success_response(result)


@router.post("/goods", summary="新增商品")
async def create_goods(
    payload: GoodsCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:goods:add"))],
    redis=Depends(get_redis),
) -> ApiResponse[GoodsOut]:
    # 幂等校验：同一租户、同一操作人、同一编码在 24 小时内只允许提交一次
    if redis is not None:
        idem_key = f"idempotency:create_goods:{tenant_id}:{current_user.id}:{payload.code or 'auto'}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增商品")
    result = await GoodsService(db, tenant_id=tenant_id).create_goods(payload)
    return success_response(result)


@router.get("/goods/{goods_id}", summary="商品详情")
async def get_goods(
    goods_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:goods:list"))],
) -> ApiResponse[GoodsOut]:
    result = await GoodsService(db, tenant_id=tenant_id).get_goods(goods_id)
    return success_response(result)


@router.put("/goods/{goods_id}", summary="修改商品")
async def update_goods(
    goods_id: int,
    payload: GoodsUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:goods:edit"))],
) -> ApiResponse[GoodsOut]:
    result = await GoodsService(db, tenant_id=tenant_id).update_goods(goods_id, payload)
    return success_response(result)


@router.delete("/goods/{goods_id}", summary="删除商品")
async def delete_goods(
    goods_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:goods:delete"))],
) -> ApiResponse[None]:
    await GoodsService(db, tenant_id=tenant_id).delete_goods(goods_id)
    return success_response(None)


# ---------- 商品分类 ----------


@router.get("/categories", summary="商品分类列表")
async def list_categories(
    query: Annotated[GoodsCategoryQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:list"))],
) -> ApiResponse[list[GoodsCategoryOut]]:
    result = await GoodsCategoryService(db, tenant_id=tenant_id).list_categories(query)
    return success_response(result)


@router.post("/categories", summary="新增商品分类")
async def create_category(
    payload: GoodsCategoryCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:category:add"))],
    redis=Depends(get_redis),
) -> ApiResponse[GoodsCategoryOut]:
    # 幂等校验：同一租户、同一操作人、同一分类名称在 24 小时内只允许提交一次
    if redis is not None:
        idem_key = f"idempotency:create_category:{tenant_id}:{current_user.id}:{payload.name}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增分类")
    result = await GoodsCategoryService(db, tenant_id=tenant_id).create_category(payload)
    return success_response(result)


@router.get("/categories/{category_id}", summary="商品分类详情")
async def get_category(
    category_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:category:list"))],
) -> ApiResponse[GoodsCategoryOut]:
    result = await GoodsCategoryService(db, tenant_id=tenant_id).get_category(category_id)
    return success_response(result)


@router.put("/categories/{category_id}", summary="修改商品分类")
async def update_category(
    category_id: int,
    payload: GoodsCategoryUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:category:edit"))],
) -> ApiResponse[GoodsCategoryOut]:
    result = await GoodsCategoryService(db, tenant_id=tenant_id).update_category(category_id, payload)
    return success_response(result)


@router.delete("/categories/{category_id}", summary="删除商品分类")
async def delete_category(
    category_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:category:delete"))],
) -> ApiResponse[None]:
    await GoodsCategoryService(db, tenant_id=tenant_id).delete_category(category_id)
    return success_response(None)


# ---------- 计量单位 ----------


@router.get("/units", summary="计量单位列表")
async def list_units(
    query: Annotated[UnitQuery, Query()],
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:list"))],
) -> ApiResponse[list[UnitOut]]:
    result = await UnitService(db, tenant_id=tenant_id).list_units(query)
    return success_response(result)


@router.post("/units", summary="新增计量单位")
async def create_unit(
    payload: UnitCreate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:unit:add"))],
    redis=Depends(get_redis),
) -> ApiResponse[UnitOut]:
    # 幂等校验：同一租户、同一操作人、同一单位名称在 24 小时内只允许提交一次
    if redis is None:
        result = await UnitService(db, tenant_id=tenant_id).create_unit(payload)
    else:
        idem_key = f"idempotency:create_unit:{tenant_id}:{current_user.id}:{payload.name}"
        existed = await redis.set(idem_key, "1", ex=86400, nx=True)
        if not existed:
            raise AppException(ErrorCode.CONFLICT, "重复提交，请勿频繁新增单位")
        result = await UnitService(db, tenant_id=tenant_id).create_unit(payload)
    return success_response(result)


@router.get("/units/{unit_id}", summary="计量单位详情")
async def get_unit(
    unit_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    _: Annotated[SysUser, Depends(require_perm("base:unit:list"))],
) -> ApiResponse[UnitOut]:
    result = await UnitService(db, tenant_id=tenant_id).get_unit(unit_id)
    return success_response(result)


@router.put("/units/{unit_id}", summary="修改计量单位")
async def update_unit(
    unit_id: int,
    payload: UnitUpdate,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:unit:edit"))],
) -> ApiResponse[UnitOut]:
    result = await UnitService(db, tenant_id=tenant_id).update_unit(unit_id, payload)
    return success_response(result)


@router.delete("/units/{unit_id}", summary="删除计量单位")
async def delete_unit(
    unit_id: int,
    db: DbDep,
    tenant_id: TenantDep,
    current_user: Annotated[SysUser, Depends(require_perm("base:unit:delete"))],
) -> ApiResponse[None]:
    await UnitService(db, tenant_id=tenant_id).delete_unit(unit_id)
    return success_response(None)
