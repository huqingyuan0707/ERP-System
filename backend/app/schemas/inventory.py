"""库存相关 Schema。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    biz_time: datetime
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
    biz_time: datetime
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
    biz_time: datetime
    remark: str | None = None


class ChangeStockIn(BaseModel):
    """库存变更入参（INV-06）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    direction: Literal[-1, 1] = Field(description="1=入库, -1=出库")
    qty: Decimal = Field(gt=0)
    unit_cost: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=4,
        allow_inf_nan=False,
    )
    biz_type: Literal[
        "purchase_in",
        "purchase_return",
        "sale_out",
        "sale_return",
        "transfer_in",
        "transfer_out",
        "check_gain",
        "check_loss",
    ]
    source_no: str
    biz_time: datetime
    idempotency_key: str | None = Field(default=None, description="幂等键，防止重复提交")
    remark: str | None = None

    @model_validator(mode="after")
    def validate_direction(self) -> Self:
        expected = {
            "purchase_in": 1,
            "purchase_return": -1,
            "sale_out": -1,
            "sale_return": 1,
            "transfer_in": 1,
            "transfer_out": -1,
            "check_gain": 1,
            "check_loss": -1,
        }[self.biz_type]
        if self.direction != expected:
            raise ValueError(f"{self.biz_type} 的库存方向必须为 {expected}")
        return self


class ChangeStockOut(BaseModel):
    """库存变更出参（INV-06）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    qty: Decimal
    locked_qty: Decimal
    available_qty: Decimal
    avg_cost: Decimal
