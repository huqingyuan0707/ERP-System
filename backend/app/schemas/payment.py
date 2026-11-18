"""付款管理 Schema（F-04，对外 camelCase）。

状态口径（G-08：待核销 → 部分核销 → 已核销）：
  - fin_payment 无 status 列，「核销进度」由 allocated_amount 与 amount 推导：
    0 待核销 / 1 部分核销 / 2 已核销（详见 derive_payment_status）。
  - 核销（writeoff）不改变付款单本身金额，只累加 allocated_amount 并同步冲减对应应付。

金额口径（G-10）：unallocated_amount = amount - allocated_amount，由 Service 维护，禁止前端直传。
对外字段一律 camelCase，内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


ZERO = Decimal("0")


class PaymentStatus:
    """付款单核销进度常量（派生口径，与 fin_payment 无状态列一致）。"""

    WAIT = 0  # 待核销：allocated_amount <= 0
    PARTIAL = 1  # 部分核销：0 < allocated_amount < amount
    DONE = 2  # 已核销：allocated_amount >= amount


# 核销进度中文文案：列表、详情与汇总展示统一口径
PAYMENT_STATUS_TEXT: dict[int, str] = {
    PaymentStatus.WAIT: "待核销",
    PaymentStatus.PARTIAL: "部分核销",
    PaymentStatus.DONE: "已核销",
}


def derive_payment_status(allocated_amount: Decimal, amount: Decimal) -> int:
    """由已核销/付款金额推导核销进度（唯一权威实现，列表/详情/写后重算共用）。"""
    allocated = Decimal(str(allocated_amount))
    total = Decimal(str(amount))
    if allocated >= total:
        return PaymentStatus.DONE
    if allocated > 0:
        return PaymentStatus.PARTIAL
    return PaymentStatus.WAIT


class PaymentCreate(BaseModel):
    """新增付款单：单号由后端生成，核销进度初始为待核销。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int  # 供应商，必填且启用
    payment_date: date  # 付款日期，报表按此归集
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)  # 付款总额，> 0
    method: str | None = Field(default=None, max_length=32)  # 结算方式，走字典 settlement_type
    account: str | None = Field(default=None, max_length=128)  # 付款账户
    remark: str | None = Field(default=None, max_length=255)  # 备注


class PaymentUpdate(BaseModel):
    """修改付款单：仅未核销（allocated_amount == 0）可改；不传的字段保持原值。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int | None = None  # 不传保持原值
    payment_date: date | None = None  # 不传保持原值
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    method: str | None = Field(default=None, max_length=32)  # 显式 null 表示清空
    account: str | None = Field(default=None, max_length=128)  # 显式 null 表示清空
    remark: str | None = Field(default=None, max_length=255)  # 显式 null 表示清空
    # 乐观锁版本号：前端回填详情/列表的 version；不传时以服务端当前版本为准（向后兼容）
    version: int | None = Field(default=None, ge=1)


class WriteoffItemIn(BaseModel):
    """核销明细行入参：核销时间由 Service 写入，禁止前端直传 writeoff_at。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    payable_id: int  # 被核销的应付，必须与付款单同一供应商
    writeoff_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)  # 本次核销金额，> 0


class WriteoffCreate(BaseModel):
    """付款核销入参：一次可核销多条应付；总额由 Service 校验不超过未分配余额。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[WriteoffItemIn] = Field(min_length=1, max_length=200)  # 核销明细 1~200 行

    @model_validator(mode="after")
    def reject_duplicate_payable(self) -> "WriteoffCreate":
        """同一应付不允许重复录入多行，否则未付校验会被绕过。"""
        payable_ids = [item.payable_id for item in self.items]
        if len(payable_ids) != len(set(payable_ids)):
            raise ValueError("同一应付不能重复录入多行，请合并核销金额")
        return self


class PaymentItemOut(BaseModel):
    """核销明细出参：应付单号由 Service 关联 fin_payable 补齐。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    payable_id: int
    payable_source_no: str | None = None  # 被核销应付的原入库单号
    writeoff_amount: Decimal = ZERO  # 本次核销金额
    writeoff_at: datetime | None = None  # 核销时间，对账归集基准


class PaymentOut(BaseModel):
    """付款单出参（主表）；列表接口 items 为空数组，核销进度由 allocated 推导。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    payment_no: str  # 付款单号，FK+yyyyMMdd+4 位序号
    supplier_id: int
    supplier_name: str | None = None  # 供应商名称
    payment_date: date
    amount: Decimal  # 付款总额，> 0
    method: str | None = None  # 结算方式（字典 settlement_type）
    account: str | None = None  # 付款账户
    allocated_amount: Decimal = ZERO  # 已核销合计，Service 维护
    unallocated_amount: Decimal = ZERO  # 未分配 = amount - allocated_amount
    status: int = PaymentStatus.WAIT  # 核销进度（派生，非数据库列）
    remark: str | None = None
    version: int = 1  # 乐观锁版本号
    created_at: datetime | None = None
    items: list[PaymentItemOut] = Field(default_factory=list)  # 核销明细（详情用）


class PaymentQuery(PageQuery):
    """付款单列表查询：供应商、核销进度、付款日期区间与关键字。"""

    supplier_id: int | None = None  # 按供应商筛选
    status: int | None = Field(
        default=None, ge=PaymentStatus.WAIT, le=PaymentStatus.DONE
    )  # 核销进度（派生口径翻译成 SQL 条件）
    payment_date_start: date | None = None  # 付款日期起（含）
    payment_date_end: date | None = None  # 付款日期止（含）


class PaymentSummary(BaseModel):
    """汇总卡片：与列表同筛选、同数据范围。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total_amount: Decimal = ZERO
    total_allocated: Decimal = ZERO
    total_unallocated: Decimal = ZERO
    count: int = 0
