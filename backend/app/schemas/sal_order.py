"""销售订单 Schema（SAL-02/04~09/15）：字段严格对齐 app/models/sal.py 的
sal_order / sal_order_item（设计文档 7.3/7.4 节）。

状态口径（与模型注释一致，禁止裸数字散落业务代码）：
  - 0 草稿 / 1 待审 / 2 已审 / 3 部分出库 / 4 完成 / 5 关闭
  - 状态机：草稿 →提交→ 待审 →审核通过→ 已审 →出库确认回写→ 部分出库 → 完成
    待审（驳回）→ 草稿；已审 / 部分出库 →关闭→ 关闭（剩余未出库数量作废）

金额口径（模型 SalOrder 注释）：
  - price / amount 均为含税值，行金额 = round(qty * price, 2)（银行家舍入）；
  - discount 为整单折扣金额（非折扣率），total_amount = Σ行金额 - discount，必须 >= 0；
  - tax_amount 按折扣比例同口径缩放（税额 = Σ(amount - amount/(1+rate)) 后按折扣比例缩放）。

信用口径（SAL-15，与模型 SalOrder 注释一致）：
  - 已占用 = 未结清应收净额（fin_receivable.unreceived_amount 合计）
    + 已审未出库订单金额（含部分出库订单的未出库部分）；
  - credit_limit = 0 视为不启用信用控制；超额（占用 + 本单金额 > 额度）拒绝并提示。

对外字段一律 camelCase（alias_generator=to_camel），内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class SalOrderStatus:
    """销售订单状态常量（与 sal_order.status 一一对应）。"""

    DRAFT = 0  # 草稿：可编辑、可删除、可提交
    PENDING = 1  # 待审：不可编辑，等待审核
    APPROVED = 2  # 已审：占用信用额度，等待出库
    PARTIAL_OUT = 3  # 部分出库：由出库确认事务回写
    COMPLETED = 4  # 完成：全部出库后由出库确认事务回写
    CLOSED = 5  # 关闭：手工关闭，不再出库


# 状态中文文案：列表状态列与错误提示统一口径
ORDER_STATUS_TEXT: dict[int, str] = {
    SalOrderStatus.DRAFT: "草稿",
    SalOrderStatus.PENDING: "待审",
    SalOrderStatus.APPROVED: "已审",
    SalOrderStatus.PARTIAL_OUT: "部分出库",
    SalOrderStatus.COMPLETED: "完成",
    SalOrderStatus.CLOSED: "关闭",
}

# 审核动作：approved=通过（待审→已审）；rejected=驳回（待审→草稿并记录驳回原因）
AUDIT_RESULT_APPROVED = "approved"
AUDIT_RESULT_REJECTED = "rejected"

# 允许手工关闭的状态：SAL-08 明确「已审核 / 部分出库可关闭」；
# 待审单先驳回再删，草稿直接删除，完成/关闭不可再关
CLOSABLE_STATUSES = (
    SalOrderStatus.APPROVED,
    SalOrderStatus.PARTIAL_OUT,
)

# 占用信用额度的状态：已审未出库 + 部分出库（未出库部分仍占用）
CREDIT_OCCUPYING_STATUSES = (
    SalOrderStatus.APPROVED,
    SalOrderStatus.PARTIAL_OUT,
)


class SalOrderItemIn(BaseModel):
    """订单明细行入参：单价由销售员填写，金额由 Service 重算，禁止前端直传 amount。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    goods_id: int  # 商品 ID，必填
    unit_id: int  # 计量单位 ID，必填（订单明细非空列）
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)  # 订购数量，必须 > 0
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)  # 含税单价，>= 0
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)  # 税率 0～1，0.13 表示 13%


class SalOrderCreate(BaseModel):
    """新增销售订单（保存即草稿）；单号由后端生成，信用校验在 Service（SAL-15）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    customer_id: int  # 客户，必填；必须存在、未删除且启用
    order_date: date  # 下单日期，必填
    # 整单折扣金额（非折扣率）：total = Σ行金额 - discount，不能超过行金额合计
    discount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    remark: str | None = Field(default=None, max_length=255)  # 备注
    items: list[SalOrderItemIn] = Field(min_length=1, max_length=200)  # 明细 1~200 行

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalOrderCreate":
        """同一订单不允许重复录入同一商品，否则出库与对账会重复计算。"""
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class SalOrderUpdate(BaseModel):
    """修改销售订单草稿：仅草稿可改，明细传了就整单替换。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    customer_id: int | None = None  # 不传保持原值
    order_date: date | None = None  # 不传保持原值
    discount: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )  # 不传保持原值
    remark: str | None = Field(default=None, max_length=255)  # 显式 null 表示清空
    items: list[SalOrderItemIn] | None = Field(default=None, max_length=200)
    # 乐观锁版本号：前端回填详情/列表的 version；不传时以服务端当前版本为准（向后兼容）
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalOrderUpdate":
        """明细整体替换时同样校验商品不重复。"""
        if self.items is None:
            return self
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self


class SalOrderAudit(BaseModel):
    """审核动作入参：approved 通过（并做信用校验）；rejected 驳回且必须填写原因。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    result: Literal["approved", "rejected"]  # 审核结论，只允许这两个值
    # 审核意见：驳回时必填，通过时可选；最长 255 与 sal_order.audit_remark 对齐
    remark: str | None = Field(default=None, max_length=255)
    # 乐观锁版本号：防止审核期间单据被他人改动
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_remark_when_rejected(self) -> "SalOrderAudit":
        """驳回必须给出原因，便于销售员修改后重新提交。"""
        if self.result == AUDIT_RESULT_REJECTED and not (self.remark or "").strip():
            raise ValueError("驳回销售订单必须填写驳回原因")
        return self


class SalOrderClose(BaseModel):
    """关闭动作入参：剩余未出库数量作废，关闭后不可再出库。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 关闭原因：复用 sal_order.audit_remark 存储（不新增列、不改迁移基线）
    reason: str | None = Field(default=None, max_length=255)
    # 乐观锁版本号
    version: int | None = Field(default=None, ge=1)


class SalOrderItemOut(BaseModel):
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
    outbound_qty: Decimal = Decimal("0")  # 累计已出库数量
    remaining_qty: Decimal = Decimal("0")  # 剩余可出库数量 = qty - outbound_qty


class SalOrderOut(BaseModel):
    """订单出参（主表 + 明细）；列表接口 items 为空数组，但含出库进度汇总。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    order_no: str  # 订单号，SO+yyyyMMdd+4 位序号
    customer_id: int
    customer_name: str | None = None  # 客户名称
    order_date: date
    status: int = SalOrderStatus.DRAFT
    total_amount: Decimal = Decimal("0")  # 折扣后含税合计
    tax_amount: Decimal = Decimal("0")  # 折扣后税额
    discount: Decimal = Decimal("0")  # 整单折扣金额
    audit_by: int | None = None  # 审核人用户 ID
    audit_by_name: str | None = None  # 审核人姓名
    audit_at: date | None = None  # 审核日期
    audit_remark: str | None = None  # 审核意见 / 驳回原因 / 关闭原因
    remark: str | None = None  # 备注
    version: int = 1  # 乐观锁版本号
    items_count: int = 0  # 明细行数
    total_qty: Decimal = Decimal("0")  # 订购数量合计（所有有效明细行）
    outbound_qty: Decimal = Decimal("0")  # 累计出库数量合计
    outbound_progress: Decimal = Decimal("0")  # 已出库进度百分比，0～100
    created_at: datetime | None = None
    items: list[SalOrderItemOut] = Field(default_factory=list)  # 明细（详情用）


class SalOrderQuery(PageQuery):
    """订单列表查询：状态、客户、下单日期区间，关键字匹配订单号/备注。"""

    status: int | None = Field(default=None, ge=SalOrderStatus.DRAFT, le=SalOrderStatus.CLOSED)
    customer_id: int | None = None  # 按客户筛选
    order_date_start: date | None = None  # 下单日期起（含）
    order_date_end: date | None = None  # 下单日期止（含）


class CreditSummaryOut(BaseModel):
    """客户信用额度概览（SAL-15）：订单新增/编辑页展示额度、已占用与可用余额。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    customer_id: int
    customer_name: str | None = None  # 客户名称
    credit_limit: Decimal = Decimal("0")  # 信用额度；0 表示不启用信用控制
    credit_days: int = 0  # 信用账期（天），供到期日参考
    # 已占用 = 未结清应收净额 + 已审/部分出库订单未出库金额
    occupied_amount: Decimal = Decimal("0")
    # 可用余额 = max(额度 - 已占用, 0)；额度为 0 时为 None（不控制）
    available_amount: Decimal | None = None


class SalOrderFromQuotationItem(BaseModel):
    """报价转订单预填明细：数量/单价与报价一致，前端确认后提交 convert。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    goods_id: int
    goods_code: str | None = None  # 商品编码
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 规格
    unit_id: int | None = None  # 商品默认单位 ID（报价明细无单位，取商品档案）
    unit_name: str | None = None  # 单位名称
    qty: Decimal = Decimal("0")  # 报价数量，作为订购数量预填
    price: Decimal = Decimal("0")  # 报价含税单价
    tax_rate: Decimal = Decimal("0")  # 预填税率，报价无税率字段，默认 0


class SalOrderFromQuotationOut(BaseModel):
    """报价转订单预填出参（只读）：前端据此填充新建订单表单，不落库。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    source_quotation_id: int  # 来源报价单 ID
    quote_no: str  # 来源报价单号
    customer_id: int  # 客户
    customer_name: str | None = None  # 客户名称
    credit_limit: Decimal = Decimal("0")  # 客户信用额度，供 SAL-15 前端展示
    occupied_amount: Decimal = Decimal("0")  # 客户当前已占用额度
    order_date: date  # 预填下单日期（取当天）
    remark: str | None = None  # 预填备注，带出来源报价单号便于追溯
    items: list[SalOrderFromQuotationItem] = Field(default_factory=list)  # 预填明细


class SalOrderConvert(BaseModel):
    """报价转订单入参：明细/价格以后端从报价单复制为准，保证「转单后明细价格一致」。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    order_date: date  # 下单日期，必填
    discount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    remark: str | None = Field(default=None, max_length=255)  # 不传取报价备注 + 来源标注
