"""库存相关 Schema。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import PageQuery, to_camel


# 流水查询区间跨度上限（天）：流水表只增不删，无上界的区间会退化为全表扫描。
# 366 天覆盖“整年同比查询”场景，同时拦住恶意/误操作的大范围查询。
MAX_FLOW_RANGE_DAYS = 366


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


# 流水类型取值：与 inv_stock_flow.flow_type 及 ChangeStockIn.biz_type 保持一致，
# 同一份枚举只在此处定义，避免多处散落字符串导致前后端不一致。
FLOW_TYPE_VALUES = (
    "purchase_in",  # 采购入库：数量增加
    "purchase_return",  # 采购退货：退回供应商，数量减少
    "sale_out",  # 销售出库：数量减少
    "sale_return",  # 销售退货：客户退回，数量增加
    "transfer_in",  # 调拨入库：调入仓数量增加
    "transfer_out",  # 调拨出库：调出仓数量减少
    "check_gain",  # 盘盈：实盘大于账面，数量增加
    "check_loss",  # 盘亏：实盘小于账面，数量减少
)


# ---------- 库存查询（只读） ----------


class StockQuery(PageQuery):
    """库存列表查询参数（GET /inventory/stocks）。

    复用父类的 page / pageSize / keyword，keyword 命中商品编码、名称、条码。
    """

    # 对外字段统一 camelCase（warehouseId、onlyAvailable 等）
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 仓库筛选：ge=1 拦截 0 与负数，避免把非法 ID 带进 SQL
    warehouse_id: int | None = Field(default=None, ge=1, description="仓库 ID")
    # 商品筛选
    goods_id: int | None = Field(default=None, ge=1, description="商品 ID")
    # 拣货场景：只看可用量大于 0 的行
    only_available: bool = Field(default=False, description="仅显示可用量大于 0 的库存行")
    # 补货场景：只看跌破商品库存下限的行（stock_lower > 0 时才参与比较）
    below_lower: bool = Field(default=False, description="仅显示低于库存下限的库存行")


class StockListItem(BaseModel):
    """库存列表行（只读视图，不对应单张表）。

    字段来源：
      - 数量三量、平均成本、上下限来自 inv_stock / bd_goods；
      - 仓库编码名称来自 bd_warehouse，商品编码名称规格来自 bd_goods，单位名称来自 bd_unit；
      - stockAmount 为展示口径 = ROUND(qty * avg_cost, 2)，由 SQL 计算，不回写数据库。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int  # inv_stock 主键
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    goods_id: int
    goods_code: str
    goods_name: str
    goods_spec: str | None = None
    unit_name: str | None = None
    qty: Decimal  # 账面数量
    locked_qty: Decimal  # 锁定数量（销售出库草稿等占用）
    available_qty: Decimal  # 可用数量 = qty - locked_qty
    avg_cost: Decimal  # 移动加权平均成本
    stock_amount: Decimal  # 库存金额（展示口径）
    stock_lower: Decimal  # 商品库存下限
    stock_upper: Decimal  # 商品库存上限
    is_below_lower: bool  # 是否低于下限，前端用于标红
    updated_at: datetime  # 最近一次库存变动时间


class StockSummary(BaseModel):
    """库存汇总（当前筛选条件下的合计，与列表使用同一套过滤条件）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    total_qty: Decimal  # 账面数量合计
    total_locked_qty: Decimal  # 锁定数量合计
    total_available_qty: Decimal  # 可用数量合计
    total_stock_amount: Decimal  # 库存金额合计
    line_count: int  # 命中行数（不受分页影响）


class FlowQuery(PageQuery):
    """库存流水查询参数（GET /inventory/flows）。

    流水表只增不删，数据量会持续增长，因此：
      - 支持按仓库、商品、流水类型、方向、来源单号精确筛选；
      - 支持 bizTime 区间（startDate / endDate），跨度上限 366 天，防止全表扫描。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int | None = Field(default=None, ge=1, description="仓库 ID")
    goods_id: int | None = Field(default=None, ge=1, description="商品 ID")
    # 流水类型必须命中白名单，避免任意字符串进入查询条件
    flow_type: Literal[
        "purchase_in",
        "purchase_return",
        "sale_out",
        "sale_return",
        "transfer_in",
        "transfer_out",
        "check_gain",
        "check_loss",
    ] | None = Field(default=None, description="流水类型")
    # 方向：1=入库，-1=出库。查询参数从 URL 传来是字符串，
    # 因此在 before 阶段显式转 int，否则 Literal[-1, 1] 会因 "-1" != -1 而 422。
    direction: int | None = Field(default=None, description="1=入库, -1=出库")

    @field_validator("direction", mode="before")
    @classmethod
    def parse_direction(cls, value):
        """把 query 字符串（含空串）规范化为 int 或 None，并限制取值只能是 -1 / 1。"""
        if value is None or value == "":
            return None
        if isinstance(value, str):
            text_value = value.strip()
            if text_value == "":
                return None
            if text_value not in {"-1", "1"}:
                raise ValueError("direction 只能是 1 或 -1")
            return int(text_value)
        if value not in {-1, 1}:
            raise ValueError("direction 只能是 1 或 -1")
        return value
    # 来源单号：定位某张单据产生的全部流水
    source_no: str | None = Field(default=None, max_length=64, description="来源单号")
    # 业务时间区间：按 biz_time 过滤，报表与追溯的归集基准
    start_date: date | None = Field(default=None, description="业务时间起（含）")
    end_date: date | None = Field(default=None, description="业务时间止（含）")

    @model_validator(mode="after")
    def validate_biz_range(self) -> Self:
        """区间校验：起始不得晚于结束，跨度不超过 366 天。"""
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError("startDate 不能晚于 endDate")
            if (self.end_date - self.start_date).days > MAX_FLOW_RANGE_DAYS:
                raise ValueError(f"查询区间跨度不能超过 {MAX_FLOW_RANGE_DAYS} 天")
        return self


class FlowListItem(BaseModel):
    """库存流水列表行（只读视图）。

    保留流水全量留痕字段（变动前/后数量、单位成本），并一次 join 带出商品与仓库的
    编码名称，避免前端逐行回查造成 N+1。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: int
    warehouse_id: int
    warehouse_code: str
    warehouse_name: str
    goods_id: int
    goods_code: str
    goods_name: str
    goods_spec: str | None = None
    flow_type: str  # 见 FLOW_TYPE_VALUES
    direction: int  # 1=入库，-1=出库
    qty: Decimal  # 变动数量（绝对值）
    before_qty: Decimal  # 变动前账面数量
    after_qty: Decimal  # 变动后账面数量
    unit_cost: Decimal  # 本次结转单位成本
    amount: Decimal  # 本次结转金额 = ROUND(qty * unit_cost, 2)，展示口径
    source_type: str | None = None  # 来源单据类型
    source_no: str | None = None  # 来源单号
    biz_time: datetime  # 业务时间
    operator_id: int | None = None  # 操作人用户 ID
    remark: str | None = None
    created_at: datetime  # 入库写入时间


class ChangeStockOut(BaseModel):
    """库存变更出参（INV-06）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int
    goods_id: int
    qty: Decimal
    locked_qty: Decimal
    available_qty: Decimal
    avg_cost: Decimal
