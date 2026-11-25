"""收款管理 Schema（F-03，对外 camelCase）。

口径与 F-04 付款对称：
  - fin_receipt 无状态列，核销进度由 allocated_amount 与 amount 推导；
  - unallocated_amount = amount - allocated_amount，由 Service 维护；
  - 核销不直接改应收金额之外的单据，只写 fin_receipt_item 并重算应收余额/状态。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel

ZERO = Decimal("0")


class ReceiptStatus:
    WAIT = 0
    PARTIAL = 1
    DONE = 2


RECEIPT_STATUS_TEXT: dict[int, str] = {
    ReceiptStatus.WAIT: "待核销",
    ReceiptStatus.PARTIAL: "部分核销",
    ReceiptStatus.DONE: "已核销",
}


def derive_receipt_status(allocated_amount: Decimal, amount: Decimal) -> int:
    allocated = Decimal(str(allocated_amount))
    total = Decimal(str(amount))
    if allocated >= total:
        return ReceiptStatus.DONE
    if allocated > 0:
        return ReceiptStatus.PARTIAL
    return ReceiptStatus.WAIT


class ReceiptCreate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    customer_id: int
    receipt_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    method: str | None = Field(default=None, max_length=32)
    account: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=255)


class ReceiptUpdate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    customer_id: int | None = None
    receipt_date: date | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    method: str | None = Field(default=None, max_length=32)
    account: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=255)
    version: int | None = Field(default=None, ge=1)


class WriteoffItemIn(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    receivable_id: int
    writeoff_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)


class WriteoffCreate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[WriteoffItemIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_receivable(self) -> "WriteoffCreate":
        ids = [item.receivable_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一应收不能重复录入多行，请合并核销金额")
        return self


class ReceiptItemOut(BaseModel):
    """核销明细出参：应收来源单号由 Service 关联 fin_receivable 补齐。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    receivable_id: int
    receivable_source_no: str | None = None
    writeoff_amount: Decimal = ZERO
    writeoff_at: datetime | None = None


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    receipt_no: str
    customer_id: int
    customer_name: str | None = None
    receipt_date: date
    amount: Decimal
    method: str | None = None
    account: str | None = None
    allocated_amount: Decimal = ZERO
    unallocated_amount: Decimal = ZERO
    status: int = ReceiptStatus.WAIT
    remark: str | None = None
    version: int = 1
    created_at: datetime | None = None
    items: list[ReceiptItemOut] = Field(default_factory=list)


class ReceiptQuery(PageQuery):
    customer_id: int | None = None
    status: int | None = Field(default=None, ge=ReceiptStatus.WAIT, le=ReceiptStatus.DONE)
    receipt_date_start: date | None = None
    receipt_date_end: date | None = None


class ReceiptSummary(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total_amount: Decimal = ZERO
    total_allocated: Decimal = ZERO
    total_unallocated: Decimal = ZERO
    count: int = 0
