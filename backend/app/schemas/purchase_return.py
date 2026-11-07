"""采购退货 Schema（P-04）：字段严格对齐《数据库表设计.md》6.7/6.8 节 pur_return / pur_return_item。

状态口径（与 app/models/pur.py 注释一致，禁止裸数字散落业务代码）：
  - 0 草稿 / 1 已确认；确认后不可改。

冲减口径（需求 1.3 / G1 / G10）：
  - 退货单以 source_no（原入库单号）定位原应付 fin_payable，按「原单号 + 商品」校验累计可退量；
  - 确认时校验：可退数量（原入库累计 - 已退）、当前可用量（available_qty >= 退货数量）、
    退货金额（<= 原应付当前 amount）；任一不满足返回 409；
  - 库存按当前 avg_cost 出库（avg_cost 不变），财务按退货单价冲减应付，差额体现在毛利。

对外字段一律 camelCase，内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class PurReturnStatus:
    """采购退货状态常量（与 pur_return.status 一一对应）。"""

    DRAFT = 0  # 草稿：可编辑、可删除、可确认
    CONFIRMED = 1  # 已确认：库存/流水/应付冲减均已写入，不可再改


# 状态中文文案：列表、详情与打印展示统一口径
RETURN_STATUS_TEXT: dict[int, str] = {
    PurReturnStatus.DRAFT: "草稿",
    PurReturnStatus.CONFIRMED: "已确认",
}


class PurReturnItemIn(BaseModel):
    """退货明细行入参：金额由 Service 重算，禁止前端直传 amount。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int  # 商品，必须来自原入库单
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)  # 退货数量，> 0
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)  # 退货含税单价，>= 0


class PurReturnCreate(BaseModel):
    """新增采购退货（保存即草稿）；单号由后端生成，原入库单号必填。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int  # 供应商，必填（应与原入库单供应商一致）
    warehouse_id: int  # 退货出库仓库，必填
    source_no: str = Field(min_length=1, max_length=64)  # 原入库单号，定位原应付与可退量
    return_date: date  # 退货日期，报表按此归集
    remark: str | None = Field(default=None, max_length=255)  # 备注
    items: list[PurReturnItemIn] = Field(min_length=1, max_length=200)  # 明细 1~200 行

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurReturnCreate":
        """同一商品不允许重复录入多行，否则可退量与库存扣减会重复计算。"""
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class PurReturnUpdate(BaseModel):
    """修改采购退货草稿：仅草稿可改，明细传了就整单替换。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int | None = None  # 不传保持原值
    warehouse_id: int | None = None  # 不传保持原值
    source_no: str | None = Field(default=None, min_length=1, max_length=64)  # 不传保持原值
    return_date: date | None = None  # 不传保持原值
    remark: str | None = Field(default=None, max_length=255)  # 显式 null 表示清空
    items: list[PurReturnItemIn] | None = Field(default=None, max_length=200)
    # 乐观锁版本号：前端回填详情/列表的 version；不传时以服务端当前版本为准（向后兼容）
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurReturnUpdate":
        """明细整体替换时同样不允许重复商品。"""
        if self.items is None:
            return self
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class PurReturnItemOut(BaseModel):
    """退货明细出参；原入库/已退/可退数量由 Service 按 source_no + 商品汇总补齐。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 退货数量
    price: Decimal = Decimal("0")  # 退货含税单价
    amount: Decimal = Decimal("0")  # 行金额，Service 重算
    original_qty: Decimal = Decimal("0")  # 原入库数量（source_no 单内该商品合计）
    returned_qty: Decimal = Decimal("0")  # 已退数量（含本单，已确认口径）
    returnable_qty: Decimal = Decimal("0")  # 可退数量 = 原入库 - 已退（不含本单时另计）


class PurReturnOut(BaseModel):
    """退货单出参（主表 + 明细）；列表接口 items 为空数组。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    return_no: str  # 退货单号，PR+yyyyMMdd+4 位序号
    supplier_id: int
    supplier_name: str | None = None  # 供应商名称
    warehouse_id: int
    warehouse_name: str | None = None  # 退货出库仓库名称
    source_no: str  # 原入库单号
    return_date: date
    status: int = PurReturnStatus.DRAFT
    total_amount: Decimal = Decimal("0")  # 退货总金额，确认时按此冲减原应付
    remark: str | None = None  # 备注
    version: int = 1  # 乐观锁版本号
    items_count: int = 0  # 明细行数
    created_at: datetime | None = None
    items: list[PurReturnItemOut] = Field(default_factory=list)  # 明细（详情用）


class PurReturnQuery(PageQuery):
    """退货单列表查询：状态、供应商、仓库、原入库单号、退货日期区间与关键字。"""

    status: int | None = Field(default=None, ge=PurReturnStatus.DRAFT, le=PurReturnStatus.CONFIRMED)
    supplier_id: int | None = None  # 按供应商筛选
    warehouse_id: int | None = None  # 按仓库筛选
    source_no: str | None = None  # 按原入库单号精确反查（数量可追溯）
    return_date_start: date | None = None  # 退货日期起（含）
    return_date_end: date | None = None  # 退货日期止（含）


class SourceInboundItem(BaseModel):
    """原入库单按商品聚合的可退明细：原入库/已退/可退数量一次算好供前端展示。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_name: str | None = None  # 单位名称
    original_qty: Decimal = Decimal("0")  # 原入库数量（该商品各明细行合计）
    returned_qty: Decimal = Decimal("0")  # 已退数量（已确认退货单，含软删主表排除）
    returnable_qty: Decimal = Decimal("0")  # 可退数量 = 原入库 - 已退
    price: Decimal = Decimal("0")  # 原入库含税单价，作为退货单价默认值


class SourcePayableOut(BaseModel):
    """原入库单对应的应付快照：退货金额上限（当前 amount）供前端确认前提示。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    amount: Decimal = Decimal("0")  # 当前应付金额（已被历史退货冲减后）
    paid_amount: Decimal = Decimal("0")  # 已付金额
    unpaid_amount: Decimal = Decimal("0")  # 未付金额，可为负（负数表示供应商应退/预付）
    status: int = 0  # 0 未付 / 1 部分付 / 2 已付


class SourceInboundOut(BaseModel):
    """按原入库单号查询的可退数据：头信息 + 按商品聚合明细 + 应付快照。

    只读组装，不落库；原入库单不存在 / 未确认返回 404 / 409。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    inbound_id: int  # 原入库单 ID
    inbound_no: str  # 原入库单号（即 source_no）
    supplier_id: int  # 供应商（退货单供应商应与其一致）
    supplier_name: str | None = None  # 供应商名称
    warehouse_id: int  # 原入库仓库
    warehouse_name: str | None = None  # 仓库名称
    inbound_date: date  # 原入库日期
    payable: SourcePayableOut | None = None  # 原应付快照，无应付时为 null
    items: list[SourceInboundItem] = Field(default_factory=list)  # 按商品聚合的可退明细
