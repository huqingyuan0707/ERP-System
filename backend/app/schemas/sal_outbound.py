"""销售出库 Schema（SA-03）：字段对齐 sal_outbound / sal_outbound_item。

口径：
  - 单价取来源销售订单明细的含税单价（不允许改价），行金额 = round(qty × price, 2)；
  - 草稿保存即锁定库存，确认时释放锁定并扣账面、生成应收；草稿修改/删除同步调整锁定；
  - 状态：0 草稿 / 1 已确认；确认后不可改，冲正走销售退货（SA-04）。
对外 camelCase，内部 snake_case。
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class SalOutboundStatus:
    """销售出库状态常量（与 sal_outbound.status 一一对应）。"""

    DRAFT = 0
    CONFIRMED = 1


OUTBOUND_STATUS_TEXT: dict[int, str] = {
    SalOutboundStatus.DRAFT: "草稿",
    SalOutboundStatus.CONFIRMED: "已确认",
}


class SalOutboundItemIn(BaseModel):
    """出库明细行入参：只指定订单明细行与本次数量；单价/金额由后端按订单明细重算。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_item_id: int  # 对应 sal_order_item.id，必须属于本出库单的来源订单
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class SalOutboundCreate(BaseModel):
    """新增销售出库（保存即草稿并锁定库存）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_id: int  # 来源销售订单，必填
    warehouse_id: int  # 出库仓库，必填
    outbound_date: date  # 出库日期，应收到期日 = 该日期 + 客户 credit_days
    remark: str | None = Field(default=None, max_length=255)
    items: list[SalOutboundItemIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_order_item(self) -> "SalOutboundCreate":
        ids = [item.order_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一订单明细行不能重复录入多行，请合并数量")
        return self


class SalOutboundUpdate(BaseModel):
    """修改出库草稿：仅草稿可改；明细传了就整单替换并调整库存锁定。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int | None = None
    outbound_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[SalOutboundItemIn] | None = Field(default=None, max_length=200)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_order_item(self) -> "SalOutboundUpdate":
        if self.items is None:
            return self
        ids = [item.order_item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一订单明细行不能重复录入多行，请合并数量")
        return self


class SalOutboundItemOut(BaseModel):
    """出库明细出参；单价/金额取来源订单明细，商品与单位名称由 Service 补齐。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    order_item_id: int
    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    spec: str | None = None
    unit_name: str | None = None
    qty: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    returned_qty: Decimal = Decimal("0")  # 已退数量（已确认退货，按本单号 + 商品）
    returnable_qty: Decimal = Decimal("0")  # 可退数量 = 本单出库 - 已退


class SalOutboundOut(BaseModel):
    """出库单出参（主表 + 明细）；列表接口 items 为空数组。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    outbound_no: str
    customer_id: int
    customer_name: str | None = None
    warehouse_id: int
    warehouse_name: str | None = None
    order_id: int
    order_no: str | None = None
    outbound_date: date
    status: int
    total_amount: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    remark: str | None = None
    version: int = 0
    items: list[SalOutboundItemOut] = Field(default_factory=list)


class SalOutboundQuery(PageQuery):
    """出库单列表筛选。"""

    status: int | None = Field(default=None, ge=0, le=1)
    order_id: int | None = None
    customer_id: int | None = None
    warehouse_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    keyword: str | None = None


class SalOutboundFromOrderItem(BaseModel):
    """按订单预填的明细行（剩余 quantity = 订购 - 已出库）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_item_id: int
    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    spec: str | None = None
    unit_name: str | None = None
    qty: Decimal
    outbound_qty: Decimal
    remaining_qty: Decimal
    price: Decimal
    tax_rate: Decimal


class SalOutboundFromOrderOut(BaseModel):
    """按订单预填出参（只读）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_id: int
    order_no: str
    customer_id: int
    customer_name: str | None = None
    outbound_date: date
    remark: str | None = None
    items: list[SalOutboundFromOrderItem] = Field(default_factory=list)
