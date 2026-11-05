"""经营看板相关的Schema定义。"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DashboardOverview(BaseModel):
    """经营看板概览数据。"""
    
    # 销售数据
    today_sales_amount: Decimal  # 今日销售额
    today_sales_count: int  # 今日销售单数
    month_sales_amount: Decimal  # 本月销售额
    month_sales_count: int  # 本月销售单数
    
    # 库存数据
    total_inventory_value: Decimal  # 库存总金额
    total_inventory_qty: Decimal  # 库存总数量
    low_stock_count: int  # 低库存商品数
    
    # 应收应付
    total_receivable: Decimal  # 应收总额
    total_payable: Decimal  # 应付总额
    
    # 客户供应商
    total_customers: int  # 客户总数
    total_suppliers: int  # 供应商总数
    
    model_config = ConfigDict(from_attributes=True)


class SalesTrendItem(BaseModel):
    """销售趋势数据项。"""
    
    date: date  # 日期
    amount: Decimal  # 销售金额
    count: int  # 销售单数
    
    model_config = ConfigDict(from_attributes=True)


class InventoryCategoryItem(BaseModel):
    """库存分类数据项。"""
    
    category_name: str  # 分类名称
    value: Decimal  # 库存金额
    qty: Decimal  # 库存数量
    
    model_config = ConfigDict(from_attributes=True)
