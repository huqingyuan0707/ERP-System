"""采购订单 Schema（P-02）：字段严格对齐《数据库表设计.md》6.3/6.4 节 pur_order / pur_order_item。

状态口径（与 app/models/pur.py 注释一致，禁止裸数字散落业务代码）：
  - 0 草稿 / 1 待审 / 2 已审 / 3 部分入库 / 4 完成 / 5 关闭

对外字段一律 camelCase，内部 Python 侧 snake_case。
本文件只定义采购订单自身的入参/出参，不包含入库与财务逻辑。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class PurOrderStatus:
    """采购订单状态常量（与 pur_order.status 一一对应）。"""

    DRAFT = 0  # 草稿：可编辑、可删除、可提交
    PENDING = 1  # 待审：不可编辑，等待审核
    APPROVED = 2  # 已审：可据此建入库单
    PARTIAL_INBOUND = 3  # 部分入库：由入库确认事务回写
    COMPLETED = 4  # 完成：全部入库后由入库确认事务回写
    CLOSED = 5  # 关闭：手工关闭，不再入库


class PurOrderItemIn(BaseModel):
    """订单明细行入参：单价由采购员填写，金额由 Service 重算，禁止前端直传 amount。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int  # 商品 ID，必填
    unit_id: int  # 计量单位 ID，必填（订单明细非空列）
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)  # 订购数量，必须 > 0
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)  # 含税单价，>= 0
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)  # 税率 0～1，0.13 表示 13%
    # 行级溯源：从申请单转单时由后端写入，手工建单为空；仅用于追溯，不参与校验
    apply_item_id: int | None = None


class PurOrderCreate(BaseModel):
    """新增采购订单（保存即草稿）；单号由后端生成。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int  # 供应商，必填
    order_date: date  # 下单日期，必填
    remark: str | None = Field(default=None, max_length=255)  # 备注
    # 单据级溯源：从申请单转单时回传后端给的值，手工建单为空；仅审计溯源用
    source_apply_id: int | None = None
    items: list[PurOrderItemIn] = Field(min_length=1, max_length=200)  # 明细 1~200 行

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurOrderCreate":
        """同一订单不允许重复录入同一商品，否则入库与对账会重复计算。"""
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class PurOrderUpdate(BaseModel):
    """修改采购订单草稿：仅草稿可改，明细传了就整单替换。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    supplier_id: int | None = None  # 不传保持原值
    order_date: date | None = None  # 不传保持原值
    remark: str | None = Field(default=None, max_length=255)  # 显式 null 表示清空
    items: list[PurOrderItemIn] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "PurOrderUpdate":
        """明细整体替换时同样校验商品不重复。"""
        if self.items is None:
            return self
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self



class PurOrderItemOut(BaseModel):
    """订单明细出参；商品/单位名称由 Service 批量补齐，避免前端逐行查询。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_id: int
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 订购数量
    price: Decimal = Decimal("0")  # 含税单价
    amount: Decimal = Decimal("0")  # 行金额，Service 重算
    tax_rate: Decimal = Decimal("0")  # 税率
    inbound_qty: Decimal = Decimal("0")  # 累计已入库数量
    apply_item_id: int | None = None  # 来源申请明细 ID（仅溯源）


class PurOrderOut(BaseModel):
    """订单出参（主表 + 明细）；列表接口 items 为空数组。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    order_no: str  # 订单号，PO+yyyyMMdd+4 位序号
    supplier_id: int
    supplier_name: str | None = None  # 供应商名称
    order_date: date
    status: int = PurOrderStatus.DRAFT
    total_amount: Decimal = Decimal("0")  # 含税合计
    tax_amount: Decimal = Decimal("0")  # 税额
    audit_by: int | None = None  # 审核人用户 ID
    audit_by_name: str | None = None  # 审核人姓名
    audit_at: date | None = None  # 审核日期
    audit_remark: str | None = None  # 审核意见/驳回原因
    remark: str | None = None  # 备注
    source_apply_id: int | None = None  # 来源申请单 ID（仅溯源）
    source_apply_no: str | None = None  # 来源申请单号，供列表与详情展示
    version: int = 1  # 乐观锁版本号
    items_count: int = 0  # 明细行数
    created_at: datetime | None = None
    items: list[PurOrderItemOut] = Field(default_factory=list)  # 明细（详情用）



class PurOrderQuery(PageQuery):
    """订单列表查询：状态、供应商、下单日期区间，关键字匹配订单号/备注。"""

    status: int | None = Field(default=None, ge=PurOrderStatus.DRAFT, le=PurOrderStatus.CLOSED)
    supplier_id: int | None = None  # 按供应商筛选
    order_date_start: date | None = None  # 下单日期起（含）
    order_date_end: date | None = None  # 下单日期止（含）
    source_apply_id: int | None = None  # 按来源申请单反查（转单溯源）


class PurOrderFromApplyItem(BaseModel):
    """转单预填明细：单价默认取商品 cost_price，前端可修改后再提交。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    apply_item_id: int  # 来源申请明细 ID，前端提交建单时回传
    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_id: int | None = None  # 商品默认单位 ID
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 申请数量，作为订购数量预填
    price: Decimal = Decimal("0")  # 预填单价，取 bd_goods.cost_price
    tax_rate: Decimal = Decimal("0")  # 预填税率，默认 0，由采购员按实际情况调整
    need_date: date | None = None  # 需求日期，供到货排期参考，不写入订单


class PurOrderFromApplyOut(BaseModel):
    """转单预填出参：只读组装，不落库；前端据此填充新建订单表单。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    source_apply_id: int  # 来源申请单 ID
    apply_no: str  # 来源申请单号
    dept_id: int  # 申请部门
    dept_name: str | None = None  # 申请部门名称
    applicant_id: int  # 申请人用户 ID
    applicant_name: str | None = None  # 申请人姓名
    need_date: date | None = None  # 主表需求日期
    order_date: date  # 预填下单日期（取当天）
    remark: str | None = None  # 预填备注，带出来源单号便于追溯
    items: list[PurOrderFromApplyItem] = Field(default_factory=list)  # 预填明细

