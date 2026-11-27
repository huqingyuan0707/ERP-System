"""经营看板相关的 Schema 定义（对外统一 camelCase）。"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import to_camel


class DashboardOverview(BaseModel):
    """经营看板概览数据（均为真实聚合，不含模拟数据）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    # 销售：确认出库 - 确认退货，按业务日期
    today_sales_amount: Decimal = Decimal("0")
    today_sales_count: int = 0
    month_sales_amount: Decimal = Decimal("0")
    month_sales_count: int = 0
    # 采购：确认入库 - 确认退货，按业务日期
    today_purchase_amount: Decimal = Decimal("0")
    month_purchase_amount: Decimal = Decimal("0")

    # 库存
    total_inventory_value: Decimal = Decimal("0")
    total_inventory_qty: Decimal = Decimal("0")
    low_stock_count: int = 0
    unhandled_alert_count: int = 0

    # 应收应付
    total_receivable: Decimal = Decimal("0")
    total_payable: Decimal = Decimal("0")

    # 客户供应商
    total_customers: int = 0
    total_suppliers: int = 0


class SalesTrendItem(BaseModel):
    """趋势数据项（销售/采购净额，按日连续）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    date: date
    amount: Decimal = Decimal("0")
    count: int = 0


class InventoryCategoryItem(BaseModel):
    """库存分类数据项。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    category_name: str
    value: Decimal = Decimal("0")
    qty: Decimal = Decimal("0")


class RankItem(BaseModel):
    """客户/商品销售净额排行项。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    name: str
    amount: Decimal = Decimal("0")


class StockAlertItem(BaseModel):
    """低库存列表项（看板只读展示）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    warehouse_name: str
    goods_name: str
    available_qty: Decimal = Decimal("0")
    stock_lower: Decimal = Decimal("0")
