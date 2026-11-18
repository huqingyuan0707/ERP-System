"""应付管理 Schema（F-02，对外 camelCase，只读）。"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


ZERO = Decimal("0")


class PayableQuery(PageQuery):
    """应付列表筛选：供应商/状态/来源单号/单据日期/逾期/关键字。"""

    supplier_id: int | None = None
    status: int | None = Field(default=None, ge=0, le=2)
    source_no: str | None = None
    bill_date_start: date | None = None
    bill_date_end: date | None = None
    overdue: bool | None = None
    # 仅未结清（unpaid_amount > 0，负数应退/预付视为已结清）；F-04 付款核销选择应付用
    unsettled: bool | None = None


class PayableOut(BaseModel):
    """应付行；未付为负时 creditAmount 给出应退/预付的正数金额。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    supplier_id: int
    supplier_name: str | None = None
    source_type: str
    source_no: str
    bill_date: date
    due_date: date | None = None
    amount: Decimal
    paid_amount: Decimal
    unpaid_amount: Decimal
    credit_amount: Decimal = ZERO
    status: int
    aging_days: int = 0
    aging_bucket: str = ""
    remark: str | None = None
    created_at: datetime | None = None


class PayableSummary(BaseModel):
    """汇总卡片：与列表同筛选、同数据范围。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total_amount: Decimal = ZERO
    total_paid: Decimal = ZERO
    total_unpaid: Decimal = ZERO
    credit_amount: Decimal = ZERO
    overdue_amount: Decimal = ZERO
    count: int = 0


class AgingBucketOut(BaseModel):
    """账龄分桶：仅正数未结项分桶，负数（应退/预付）单列。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    bucket: str
    count: int
    amount: Decimal


class WriteoffRecord(BaseModel):
    """付款核销记录（F-04 写入，F-02 详情只读展示）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    payment_id: int
    writeoff_amount: Decimal
    writeoff_at: datetime


class ReturnRecord(BaseModel):
    """按原入库单号关联、冲减该应付的采购退货记录。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    return_no: str
    return_date: date
    total_amount: Decimal


class PayableDetailOut(PayableOut):
    """详情：应付主行 + 付款核销记录 + 退货冲减记录。"""

    writeoffs: list[WriteoffRecord] = Field(default_factory=list)
    returns: list[ReturnRecord] = Field(default_factory=list)
