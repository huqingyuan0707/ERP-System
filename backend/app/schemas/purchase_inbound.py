"""采购入库 Schema（P-03）：字段严格对齐《数据库表设计.md》pur_inbound / pur_inbound_item。

状态口径（与 app/models/pur.py 注释一致，禁止裸数字散落业务代码）：
  - 0 草稿 / 1 已确认；确认后不可改，冲正走采购退货（P-04）。

金额口径：入库单价取来源采购订单明细的含税单价（不允许改价），行金额 = round(qty × price, 2)，
合计与税额由 Service 按 P-02 相同的含税口径重算，禁止前端直传。

对外字段一律 camelCase，内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class PurInboundStatus:
    """采购入库状态常量（与 pur_inbound.status 一一对应）。"""

    DRAFT = 0  # 草稿：可编辑、可删除、可确认
    CONFIRMED = 1  # 已确认：库存/订单/应付均已写入，不可再改


# 状态中文文案：导出、列表与详情展示统一口径
INBOUND_STATUS_TEXT: dict[int, str] = {
    PurInboundStatus.DRAFT: "草稿",
    PurInboundStatus.CONFIRMED: "已确认",
}


class PurInboundItemIn(BaseModel):
    """入库明细行入参：只指定订单明细行与本次数量；单价/金额由后端按订单明细取值重算。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_item_id: int  # 对应 pur_order_item.id，必须属于本入库单的来源订单
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)  # 本次入库数量，> 0
    batch_no: str | None = Field(default=None, max_length=64)  # 批次号，可空


class PurInboundCreate(BaseModel):
    """新增采购入库（保存即草稿）；单号由后端生成，单价取来源订单明细。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_id: int  # 来源采购订单，必填（G4：一张入库单对应一张订单）
    warehouse_id: int  # 入库仓库，必填
    inbound_date: date  # 入库日期，应付到期日 = 该日期 + 供应商 credit_days
    remark: str | None = Field(default=None, max_length=255)  # 备注
    items: list[PurInboundItemIn] = Field(min_length=1, max_length=200)  # 明细 1~200 行

    @model_validator(mode="after")
    def reject_duplicate_order_item(self) -> "PurInboundCreate":
        """同一订单明细行不允许重复录入，否则入库回写会重复累加。"""
        item_ids = [item.order_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("同一订单明细行不能重复录入多行，请合并数量")
        return self


class PurInboundUpdate(BaseModel):
    """修改采购入库草稿：仅草稿可改，明细传了就整单替换。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    warehouse_id: int | None = None  # 不传保持原值
    inbound_date: date | None = None  # 不传保持原值
    remark: str | None = Field(default=None, max_length=255)  # 显式 null 表示清空
    items: list[PurInboundItemIn] | None = Field(default=None, max_length=200)
    # 乐观锁版本号：前端回填详情/列表的 version；不传时以服务端当前版本为准（向后兼容）
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_order_item(self) -> "PurInboundUpdate":
        """明细整体替换时同样不允许重复订单明细行。"""
        if self.items is None:
            return self
        item_ids = [item.order_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("同一订单明细行不能重复录入多行，请合并数量")
        return self


class PurInboundItemOut(BaseModel):
    """入库明细出参；单价/金额取来源订单明细，由 Service 批量补齐商品与单位名称。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    order_item_id: int  # 对应订单明细行
    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 本次入库数量
    price: Decimal = Decimal("0")  # 入库含税单价（= 订单明细单价）
    amount: Decimal = Decimal("0")  # 行金额，Service 重算
    tax_rate: Decimal = Decimal("0")  # 税率
    batch_no: str | None = None  # 批次号


class PurInboundOut(BaseModel):
    """入库单出参（主表 + 明细）；列表接口 items 为空数组。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    inbound_no: str  # 入库单号，PI+yyyyMMdd+4 位序号
    order_id: int  # 来源采购订单 ID
    order_no: str | None = None  # 来源订单号，供列表与详情展示
    supplier_id: int
    supplier_name: str | None = None  # 供应商名称
    warehouse_id: int
    warehouse_name: str | None = None  # 仓库名称
    inbound_date: date
    status: int = PurInboundStatus.DRAFT
    total_amount: Decimal = Decimal("0")  # 含税合计，确认时按此生成应付
    tax_amount: Decimal = Decimal("0")  # 其中税额
    remark: str | None = None  # 备注
    version: int = 1  # 乐观锁版本号
    items_count: int = 0  # 明细行数
    created_at: datetime | None = None
    items: list[PurInboundItemOut] = Field(default_factory=list)  # 明细（详情用）


class PurInboundQuery(PageQuery):
    """入库单列表查询：状态、订单、供应商、仓库、入库日期区间与关键字。"""

    status: int | None = Field(
        default=None, ge=PurInboundStatus.DRAFT, le=PurInboundStatus.CONFIRMED
    )
    order_id: int | None = None  # 按来源订单反查入库记录
    supplier_id: int | None = None  # 按供应商筛选
    warehouse_id: int | None = None  # 按仓库筛选
    inbound_date_start: date | None = None  # 入库日期起（含）
    inbound_date_end: date | None = None  # 入库日期止（含）


class PurInboundFromOrderItem(BaseModel):
    """按订单预填的入库明细行：剩余可入库数量为订购 - 已入库（关闭后作废）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_item_id: int  # 订单明细行 ID，保存/提交时回传
    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 订购数量
    inbound_qty: Decimal = Decimal("0")  # 累计已入库数量
    remaining_qty: Decimal = Decimal("0")  # 剩余可入库数量（本次数量上限）
    price: Decimal = Decimal("0")  # 入库含税单价（= 订单明细单价，不可修改）
    tax_rate: Decimal = Decimal("0")  # 税率


class PurInboundFromOrderOut(BaseModel):
    """按已审/部分入库订单生成入库单预填数据：只读组装，不落库。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_id: int  # 来源订单 ID
    order_no: str  # 来源订单号
    supplier_id: int  # 供应商（取订单供应商）
    supplier_name: str | None = None  # 供应商名称
    inbound_date: date  # 预填入库日期（取当天）
    remark: str | None = None  # 预填备注，带出来源订单号便于追溯
    items: list[PurInboundFromOrderItem] = Field(default_factory=list)  # 剩余可入库明细
