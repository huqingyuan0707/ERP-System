"""销售报表 Schema（R-03，对外 camelCase）。"""

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel

ZERO = Decimal("0")


class SalesReportQuery(PageQuery):
    """销售报表筛选：日期区间、客户、商品、仓库、视图与汇总维度。"""

    view: Literal["detail", "summary", "customer_rank", "goods_rank"] = "detail"
    group_by: Literal["goods", "customer"] = "goods"
    start_date: date | None = None
    end_date: date | None = None
    customer_id: int | None = None
    goods_id: int | None = None
    warehouse_id: int | None = None


class SalesReportTotals(BaseModel):
    """合计：出库 - 退货，与应收净额/库存流水可对账。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    outbound_qty: Decimal = ZERO
    return_qty: Decimal = ZERO
    net_qty: Decimal = ZERO
    outbound_amount: Decimal = ZERO
    return_amount: Decimal = ZERO
    net_amount: Decimal = ZERO
    row_count: int = 0


class SalesReportOut(BaseModel):
    """报表出参：items 结构随 view 变化。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    view: str
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    totals: SalesReportTotals
