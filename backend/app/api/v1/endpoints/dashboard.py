"""经营看板端点（R-01）：只读聚合，数据范围与缓存由 Service 处理。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbDep, TenantDep, require_perm
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.dashboard import (
    DashboardOverview,
    InventoryCategoryItem,
    RankItem,
    SalesTrendItem,
    StockAlertItem,
)
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/dashboard", tags=["经营看板"])

DashboardReader = Annotated[SysUser, Depends(require_perm("report:dashboard:view"))]


@router.get("/overview", summary="经营看板概览")
async def get_overview(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
) -> ApiResponse[DashboardOverview]:
    return success_response(await DashboardService(db, tenant_id).overview(current_user))


@router.get("/sales-trend", summary="近 N 天销售净额趋势")
async def get_sales_trend(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> ApiResponse[list[SalesTrendItem]]:
    """确认出库 - 确认退货，按业务日期分组。"""
    return success_response(
        await DashboardService(db, tenant_id).trend(current_user, days, sales=True)
    )


@router.get("/purchase-trend", summary="近 N 天采购净额趋势")
async def get_purchase_trend(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> ApiResponse[list[SalesTrendItem]]:
    """确认入库 - 确认退货，按业务日期分组。"""
    return success_response(
        await DashboardService(db, tenant_id).trend(current_user, days, sales=False)
    )


@router.get("/inventory-category", summary="按商品分类统计库存")
async def get_inventory_category(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
) -> ApiResponse[list[InventoryCategoryItem]]:
    return success_response(await DashboardService(db, tenant_id).inventory_category(current_user))


@router.get("/ranking", summary="客户/商品销售净额 Top 5")
async def get_ranking(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
    kind: Annotated[Literal["customer", "goods"], Query()] = "customer",
) -> ApiResponse[list[RankItem]]:
    return success_response(await DashboardService(db, tenant_id).ranking(current_user, kind))


@router.get("/low-stocks", summary="低库存列表（前 10）")
async def get_low_stocks(
    db: DbDep,
    tenant_id: TenantDep,
    current_user: DashboardReader,
) -> ApiResponse[list[StockAlertItem]]:
    return success_response(await DashboardService(db, tenant_id).low_stocks(current_user))
