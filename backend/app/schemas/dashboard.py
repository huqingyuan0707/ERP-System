"""经营看板相关的 Schema 定义（对外统一 camelCase）。"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.common import to_camel


class DashboardOverview(BaseModel):
    """经营看板概览数据。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    # 销售数据：SA-03/SA-04 未上线前固定为 0，不使用模拟数据
    today_sales_amount: Decimal
    today_sales_count: int
    month_sales_amount: Decimal
    month_sales_count: int

    # 库存数据
    total_inventory_value: Decimal
    total_inventory_qty: Decimal
    low_stock_count: int

    # 应收应付
    total_receivable: Decimal
    total_payable: Decimal

    # 客户供应商
    total_customers: int
    total_suppliers: int


class SalesTrendItem(BaseModel):
    """趋势数据项（当前为已确认采购入库减退货的净额）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    date: date
    amount: Decimal
    count: int


class InventoryCategoryItem(BaseModel):
    """库存分类数据项。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    category_name: str
    value: Decimal
    qty: Decimal
