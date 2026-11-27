"""库存调拨 Schema（I-03）：对外 camelCase，数据库 snake_case。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class TransferItemIn(BaseModel):
    """调拨明细入参（商品 + 数量）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int = Field(gt=0, description="商品 ID")
    qty: Decimal = Field(gt=0, description="调拨数量，必须大于 0")


class TransferItemOut(BaseModel):
    """调拨明细出参。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    transfer_id: int
    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    goods_spec: str | None = None
    qty: Decimal
    cost: Decimal


class TransferCreate(BaseModel):
    """新建调拨单入参。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    from_warehouse_id: int = Field(gt=0, description="调出仓库 ID")
    to_warehouse_id: int = Field(gt=0, description="调入仓库 ID")
    transfer_date: date = Field(description="调拨日期")
    items: list[TransferItemIn] = Field(min_length=1, description="明细行，至少 1 行")
    remark: str | None = Field(default=None, max_length=255, description="备注")


class TransferUpdate(BaseModel):
    """修改调拨单草稿入参（仅草稿可改）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    from_warehouse_id: int | None = Field(default=None, gt=0, description="调出仓库 ID")
    to_warehouse_id: int | None = Field(default=None, gt=0, description="调入仓库 ID")
    transfer_date: date | None = Field(default=None, description="调拨日期")
    items: list[TransferItemIn] | None = Field(default=None, description="明细行")
    remark: str | None = Field(default=None, max_length=255, description="备注")
    version: int = Field(gt=0, description="乐观锁版本号")


class TransferQuery(PageQuery):
    """调拨单列表查询参数（GET /inventory/transfers）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int | None = Field(default=None, ge=1, description="仓库 ID（调出或调入）")
    goods_id: int | None = Field(default=None, ge=1, description="商品 ID")
    status: int | None = Field(default=None, ge=0, le=1, description="状态：0 草稿 / 1 已确认")
    start_date: date | None = Field(default=None, description="调拨日期起（含）")
    end_date: date | None = Field(default=None, description="调拨日期止（含）")
    keyword: str | None = Field(default=None, description="关键字：单号")


class TransferOut(BaseModel):
    """调拨单出参（含明细）。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    transfer_no: str
    from_warehouse_id: int
    from_warehouse_name: str | None = None
    to_warehouse_id: int
    to_warehouse_name: str | None = None
    transfer_date: date
    status: int  # 0 草稿 / 1 已确认
    total_qty: Decimal  # 调拨总数量
    remark: str | None = None
    created_by: int | None = None
    created_at: datetime
    version: int
    items: list[TransferItemOut] = []


class TransferConfirm(BaseModel):
    """确认调拨入参。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    version: int = Field(gt=0, description="乐观锁版本号")


class TransferSourceItem(BaseModel):
    """调出仓可用商品明细（预填用，只读）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int
    goods_code: str | None = None
    goods_name: str | None = None
    goods_spec: str | None = None
    available_qty: Decimal  # 调出仓可用数量，界面用于限制最大可调数量
    avg_cost: Decimal  # 调出仓平均成本，用于展示结转成本参考


class TransferSourceOut(BaseModel):
    """调出仓可调商品清单出参。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    warehouse_name: str | None = None
    items: list[TransferSourceItem] = []
