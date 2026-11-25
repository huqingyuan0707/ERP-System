"""销售退货 Schema（SA-04）：字段对齐 sal_return / sal_return_item。

口径：source_no 定位原出库单（进而定位原应收 fin_receivable），
确认时按「原单号 + 商品」校验累计退货量，冲减原应收（不建红字），库存回补。
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class SalReturnStatus:
    DRAFT = 0
    CONFIRMED = 1


RETURN_STATUS_TEXT: dict[int, str] = {
    SalReturnStatus.DRAFT: "草稿",
    SalReturnStatus.CONFIRMED: "已确认",
}


class SalReturnItemIn(BaseModel):
    """退货明细行入参：单价由后端按原出库单明细取值，金额 Service 重算。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int  # 商品 ID，必须属于原出库单
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class SalReturnCreate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    source_no: str = Field(min_length=1, max_length=64)  # 原出库单号
    warehouse_id: int  # 退货入库仓库
    return_date: date
    remark: str | None = Field(default=None, max_length=255)
    items: list[SalReturnItemIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalReturnCreate":
        ids = [item.goods_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class SalReturnUpdate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int | None = None
    return_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    items: list[SalReturnItemIn] | None = Field(default=None, max_length=200)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalReturnUpdate":
        if self.items is None:
            return self
        ids = [item.goods_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class SalReturnItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    unit_name: str | None = None
    qty: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")


class SalReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    return_no: str
    customer_id: int
    customer_name: str | None = None
    warehouse_id: int
    warehouse_name: str | None = None
    source_no: str
    return_date: date
    status: int
    total_amount: Decimal = Decimal("0")
    remark: str | None = None
    version: int = 0
    items: list[SalReturnItemOut] = Field(default_factory=list)


class SalReturnQuery(PageQuery):
    status: int | None = Field(default=None, ge=0, le=1)
    customer_id: int | None = None
    warehouse_id: int | None = None
    source_no: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    keyword: str | None = None


class SalReturnSourceItem(BaseModel):
    """原出库单可退明细：按「原单号 + 商品」给出已退与可退数量。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    unit_name: str | None = None
    outbound_qty: Decimal  # 原出库数量
    returned_qty: Decimal  # 已确认退货数量
    returnable_qty: Decimal  # 可退数量
    price: Decimal  # 原出库单价


class SalReturnSourceOut(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    outbound_id: int
    outbound_no: str
    customer_id: int
    customer_name: str | None = None
    warehouse_id: int
    warehouse_name: str | None = None
    outbound_date: date
    total_amount: Decimal
    items: list[SalReturnSourceItem] = Field(default_factory=list)
