"""供应商对账单 Schema（F-06，对外 camelCase）。

本切片只实现供应商侧（party_type=2）；客户侧需 F-01/F-03 落地后再接。
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery, to_camel


class StatementQuery(PageQuery):
    """对账单列表筛选。"""

    party_type: int | None = Field(default=None, ge=1, le=2)
    party_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    statement_no: str | None = None


class StatementItemOut(BaseModel):
    """对账明细行。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    biz_date: date
    source_no: str | None = None
    summary: str | None = None
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class StatementOut(BaseModel):
    """对账单主表（含往来单位名称）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    statement_no: str
    party_type: int
    party_id: int
    party_name: str | None = None
    period_start: date
    period_end: date
    begin_amount: Decimal
    current_amount: Decimal
    end_amount: Decimal
    status: int
    remark: str | None = None
    created_at: str | None = None


class StatementDetailOut(StatementOut):
    """对账单详情：主表 + 明细行。"""

    items: list[StatementItemOut] = Field(default_factory=list)


class StatementCreate(BaseModel):
    """生成快照入参；仅支持供应商。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    party_type: Literal[2]
    party_id: int = Field(gt=0, description="供应商 ID")
    period_start: date
    period_end: date
    remark: str | None = None


class StatementBuildItem(BaseModel):
    """未落库的预览明细行。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    biz_date: date
    source_no: str | None = None
    summary: str | None = None
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class StatementBuildOut(BaseModel):
    """生成前预览：期初 + 本期明细 + 期末。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    party_type: int
    party_id: int
    party_name: str | None = None
    period_start: date
    period_end: date
    begin_amount: Decimal
    current_amount: Decimal
    end_amount: Decimal
    items: list[StatementBuildItem] = Field(default_factory=list)
