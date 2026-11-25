"""应收管理 Schema（F-01，对外 camelCase，只读）。"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel

ZERO = Decimal("0")


class ReceivableQuery(PageQuery):
    """应收列表筛选：客户/状态/来源单号/单据日期/逾期/关键字。"""

    customer_id: int | None = None
    status: int | None = Field(default=None, ge=0, le=2)
    source_no: str | None = None
    bill_date_start: date | None = None
    bill_date_end: date | None = None
    overdue: bool | None = None
    # 仅未结清（unreceived_amount > 0，负数应退/预收视为已结清）；F-03 收款核销选择应收用
    unsettled: bool | None = None


class ReceivableOut(BaseModel):
    """应收行；未收为负时 creditAmount 给出应退/预收的正数金额。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    customer_id: int
    customer_name: str | None = None
    source_type: str
    source_no: str
    bill_date: date
    due_date: date | None = None
    amount: Decimal
    received_amount: Decimal
    unreceived_amount: Decimal
    credit_amount: Decimal = ZERO
    status: int
    aging_days: int = 0
    aging_bucket: str = ""
    remark: str | None = None
    created_at: datetime | None = None


class ReceivableSummary(BaseModel):
    """汇总卡片：与列表同筛选、同数据范围。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total_amount: Decimal = ZERO
    total_received: Decimal = ZERO
    total_unreceived: Decimal = ZERO
    credit_amount: Decimal = ZERO
    overdue_amount: Decimal = ZERO
    count: int = 0


class AgingBucketOut(BaseModel):
    """账龄分桶：仅正数未结项分桶，负数（应退/预收）单列。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    bucket: str
    count: int
    amount: Decimal


class ReceiptWriteoffRecord(BaseModel):
    """收款核销记录（F-03 写入，F-01 详情只读展示）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    receipt_id: int
    writeoff_amount: Decimal
    writeoff_at: datetime


class SalesReturnRecord(BaseModel):
    """按原出库单号关联、冲减该应收的销售退货记录。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    return_no: str
    return_date: date
    total_amount: Decimal


class ReceivableDetailOut(ReceivableOut):
    """详情：应收主行 + 收款核销记录 + 退货冲减记录。"""

    writeoffs: list[ReceiptWriteoffRecord] = Field(default_factory=list)
    returns: list[SalesReturnRecord] = Field(default_factory=list)
