"""经营看板API endpoint。"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import get_current_user
from app.core.deps import get_db
from app.models.base_data import BdCustomer, BdSupplier
from app.models.inventory import InvStock
from app.models.sys import SysUser
from app.schemas.common import ApiResponse, success_response
from app.schemas.dashboard import (
    DashboardOverview,
    InventoryCategoryItem,
    SalesTrendItem,
)


router = APIRouter(prefix="/dashboard", tags=["经营看板"])


@router.get("/overview", response_model=ApiResponse[DashboardOverview], summary="获取经营看板概览")
async def get_dashboard_overview(
    db: AsyncSession = Depends(get_db),
    current_user: SysUser = Depends(get_current_user),
) -> ApiResponse[DashboardOverview]:
    """获取经营看板概览数据，包括销售、库存、应收应付、客户供应商统计。"""
    tenant_id = current_user.tenant_id
    today = date.today()
    month_start = date(today.year, today.month, 1)  # noqa: F841 - 待销售模块实现后使用

    # 今日销售额和单数（TODO: 等销售模块实现后从销售表查询）
    today_sales_amount = Decimal("0")
    today_sales_count = 0
    
    # 本月销售额和单数（TODO: 等销售模块实现后从销售表查询）
    month_sales_amount = Decimal("0")
    month_sales_count = 0
    
    # 库存总金额和总数量
    inventory_result = await db.execute(
        select(
            func.sum(InvStock.qty * InvStock.avg_cost).label("total_value"),
            func.sum(InvStock.qty).label("total_qty"),
        )
        .where(
            InvStock.tenant_id == tenant_id,
            InvStock.is_deleted.is_(False),
        )
    )
    inventory_row = inventory_result.first()
    total_inventory_value = inventory_row.total_value or Decimal("0")
    total_inventory_qty = inventory_row.total_qty or Decimal("0")
    
    # 低库存商品数（库存低于预警值）
    low_stock_result = await db.execute(
        select(func.count(InvStock.id))
        .where(
            InvStock.tenant_id == tenant_id,
            InvStock.is_deleted.is_(False),
            InvStock.available_qty < 10,  # 假设预警值为10
        )
    )
    low_stock_count = low_stock_result.scalar() or 0
    
    # 应收应付总额（TODO: 等财务模块实现后从财务表查询）
    total_receivable = Decimal("0")
    total_payable = Decimal("0")
    
    # 客户和供应商总数
    customer_count_result = await db.execute(
        select(func.count(BdCustomer.id))
        .where(
            BdCustomer.tenant_id == tenant_id,
            BdCustomer.is_deleted.is_(False),
        )
    )
    total_customers = customer_count_result.scalar() or 0
    
    supplier_count_result = await db.execute(
        select(func.count(BdSupplier.id))
        .where(
            BdSupplier.tenant_id == tenant_id,
            BdSupplier.is_deleted.is_(False),
        )
    )
    total_suppliers = supplier_count_result.scalar() or 0
    
    overview = DashboardOverview(
        today_sales_amount=today_sales_amount,
        today_sales_count=today_sales_count,
        month_sales_amount=month_sales_amount,
        month_sales_count=month_sales_count,
        total_inventory_value=total_inventory_value,
        total_inventory_qty=total_inventory_qty,
        low_stock_count=low_stock_count,
        total_receivable=total_receivable,
        total_payable=total_payable,
        total_customers=total_customers,
        total_suppliers=total_suppliers,
    )
    
    return success_response(overview)


@router.get(
    "/sales-trend",
    response_model=ApiResponse[list[SalesTrendItem]],
    summary="获取销售趋势",
)
async def get_sales_trend(
    days: int = 30,
    db: AsyncSession = Depends(get_db),
    current_user: SysUser = Depends(get_current_user),
) -> ApiResponse[list[SalesTrendItem]]:
    """获取最近N天的销售趋势数据。"""
    # TODO: 等销售模块实现后从销售表查询
    # 当前返回模拟数据
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    trend_data = []
    current_date = start_date
    while current_date <= end_date:
        # 模拟数据
        trend_data.append(
            SalesTrendItem(
                date=current_date,
                amount=Decimal(str(current_date.day * 1000)),
                count=current_date.day,
            )
        )
        current_date += timedelta(days=1)
    
    return success_response(trend_data)


@router.get(
    "/inventory-category",
    response_model=ApiResponse[list[InventoryCategoryItem]],
    summary="获取库存分类统计",
)
async def get_inventory_category(
    db: AsyncSession = Depends(get_db),
    current_user: SysUser = Depends(get_current_user),
) -> ApiResponse[list[InventoryCategoryItem]]:
    """获取按分类统计的库存数据。"""
    # TODO: 等商品分类实现后按分类统计
    # 当前返回模拟数据
    category_data = [
        InventoryCategoryItem(
            category_name="原材料",
            value=Decimal("50000"),
            qty=Decimal("1000"),
        ),
        InventoryCategoryItem(
            category_name="半成品",
            value=Decimal("30000"),
            qty=Decimal("500"),
        ),
        InventoryCategoryItem(
            category_name="成品",
            value=Decimal("80000"),
            qty=Decimal("200"),
        ),
    ]
    
    return success_response(category_data)
