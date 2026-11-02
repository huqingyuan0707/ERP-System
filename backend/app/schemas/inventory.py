"""库存相关 Schema。"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import to_camel


class StockOut(BaseModel):
    """库存出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    warehouse_id: int
    goods_id: int
    qty: Decimal
    locked_qty: Decimal
    available_qty: Decimal
    avg_cost: Decimal


class StockFlowOut(BaseModel):
    """库存流水出参。"""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )

    id: int
    warehouse_id: int
    goods_id: int
    flow_type: str
    direction: int
    qty: Decimal
    before_qty: Decimal
    after_qty: Decimal
    unit_cost: Decimal
    source_type: str | None = None
    source_no: str | None = None
    biz_time: str
    remark: str | None = None


class LockStockIn(BaseModel):
    """锁定库存入参（INV-05）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    qty: Decimal = Field(gt=0)
    idempotency_key: str | None = Field(default=None, description="幂等键，防止重复提交")
    source_type: str | None = None
    source_no: str | None = None
    biz_time: str
    remark: str | None = None


class UnlockStockIn(BaseModel):
    """释放库存入参（INV-05）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    qty: Decimal = Field(gt=0)
    idempotency_key: str | None = Field(default=None, description="幂等键，防止重复提交")
    source_type: str | None = None
    source_no: str | None = None
    biz_time: str
    remark: str | None = None


class ChangeStockIn(BaseModel):
    """库存变更入参（INV-06）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    direction: Literal[-1, 1] = Field(description="1=入库, -1=出库")
    qty: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(default=Decimal("0"), ge=0)
    biz_type: str
    source_no: str
    biz_time: str
    idempotency_key: str | None = Field(default=None, description="幂等键，防止重复提交")
    remark: str | None = None


class ChangeStockOut(BaseModel):
    """库存变更出参（INV-06）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    qty: Decimal
    locked_qty: Decimal
    available_qty: Decimal
    avg_cost: Decimal
