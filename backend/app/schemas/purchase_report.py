"""采购报表 Schema（R-02，对外 camelCase）。"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel

ZERO = Decimal("0")


class PurchaseReportQuery(PageQuery):
    """采购报表筛选：日期区间、供应商、商品、仓库、视图与汇总维度。"""

    view: Literal["detail", "summary", "rank"] = "detail"
    group_by: Literal["goods", "supplier"] = "goods"
    start_date: date | None = None
    end_date: date | None = None
    supplier_id: int | None = None
    goods_id: int | None = None
    warehouse_id: int | None = None


class PurchaseReportTotals(BaseModel):
    """合计：入库 - 退货，与应付净额/库存流水可对账。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    inbound_qty: Decimal = ZERO
    return_qty: Decimal = ZERO
    net_qty: Decimal = ZERO
    inbound_amount: Decimal = ZERO
    return_amount: Decimal = ZERO
    net_amount: Decimal = ZERO
    row_count: int = 0


class PurchaseReportOut(BaseModel):
    """报表出参：items 结构随 view 变化（detail/summary/rank）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    view: str
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    totals: PurchaseReportTotals
