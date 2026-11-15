"""销售报价 Schema（SA-01）：字段按《数据库表设计.md》7.1/7.2 节
sal_quotation / sal_quotation_item。

状态口径（与 app/models/sal.py 保持一致，禁止裸数字散落业务代码）：
  - 0 草稿：可编辑、可删除、可发送
  - 1 已发送：不可编辑、不可删除，可作废；已过有效期不能再发送
  - 2 已失效：终态，仅作报价留档与「复制为新订单」的取数来源

金额口径（.trae 固定规则 §2 / 设计文档 G5）：
  - price / amount 均为含税值，amount = qty * price 后 quantize(0.01)（默认银行家舍入）；
  - 明细无税率字段，主表 tax_amount 落 0，V1 不做税额拆分。

对外字段一律 camelCase（alias_generator=to_camel），内部 Python 侧 snake_case。
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import PageQuery, to_camel


class SalQuotationStatus:
    """销售报价状态常量（与 sal_quotation.status 一一对应，禁止业务代码写裸数字）。

    用类常量而不是模块常量：既能在类体内被引用（模块级常量定义在类之后会被判为未定义），
    也不会与 ruff isort 的「导入块后空行数」规则冲突。
    """

    DRAFT = 0  # 草稿：可编辑、可删除、可发送
    SENT = 1  # 已发送：不可编辑、不可删除，可作废
    INVALID = 2  # 已失效：终态，仅作留档与复制预填订单的取数来源


# 状态文案：列表状态列与错误提示共用，避免前端再维护一份映射
STATUS_TEXTS: dict[int, str] = {
    SalQuotationStatus.DRAFT: "草稿",
    SalQuotationStatus.SENT: "已发送",
    SalQuotationStatus.INVALID: "已失效",
}


class SalQuotationItemIn(BaseModel):
    """报价明细行入参：商品、数量与含税单价，行金额由 Service 统一重算（防止前端伪造）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 商品 ID 必填；是否存在、是否启用由 Service 统一校验（避免每个明细行查一次库）
    goods_id: int
    # 报价数量必须大于 0，按 NUMERIC(18,4) 精度录入；<=0 直接 422 拦截
    qty: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    # 含税单价，NUMERIC(18,2)；允许 0（赠品/免费试用报价），不允许负数
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


class SalQuotationCreate(BaseModel):
    """新增销售报价（保存即草稿）；报价号由后端生成（QT+yyyyMMdd+4 位）。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 客户必填：必须存在、未删除且启用（Service 校验）
    customer_id: int
    # 报价日期：新建时必填
    quote_date: date
    # 报价有效期：可空（不填则发送时不做过期校验）；填写时必须 >= 报价日期，否则 422
    valid_until: date | None = None
    # 备注：报价说明、付款/交货条款等，最长 255 与表结构对齐
    remark: str | None = Field(default=None, max_length=255)
    # 明细：至少 1 行，最多 200 行（上限与 Service 侧常量一致，防止一次提交过大报文）
    items: list[SalQuotationItemIn] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalQuotationCreate":
        """同一张报价不允许重复报价同一商品，避免复制预填订单时出现两行同品。"""
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self

    @model_validator(mode="after")
    def reject_expired_valid_until(self) -> "SalQuotationCreate":
        """有效期必须 >= 报价日期（验收要求：有效期早于报价日期返回 422）。"""
        if self.valid_until is not None and self.valid_until < self.quote_date:
            raise ValueError("报价有效期不能早于报价日期")
        return self


class SalQuotationUpdate(BaseModel):
    """修改报价草稿：整单覆盖式更新（主表字段 + 明细行全量替换）。

    只有草稿（status=0）允许调用；已发送/已失效修改一律 409。
    单独传 quote_date 或 valid_until 时，与库中另一字段的组合校验在 Service 侧完成
    （Schema 无法读到库中旧值）。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    # 客户：不传保持原值；传入时必须存在、未删除且启用
    customer_id: int | None = None
    # 报价日期 / 有效期：不传保持原值
    quote_date: date | None = None
    valid_until: date | None = None
    # 备注：显式传 null 表示清空备注
    remark: str | None = Field(default=None, max_length=255)
    # 明细：不传表示只改主表字段；传了就整单替换（旧行软删，保留历史便于追溯），最多 200 行
    items: list[SalQuotationItemIn] | None = Field(default=None, max_length=200)
    # 乐观锁版本号：前端编辑时回传；未传仍靠条件 UPDATE 防并发覆盖
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_duplicate_goods(self) -> "SalQuotationUpdate":
        """明细整体替换时同样校验商品不重复。"""
        if self.items is None:
            return self
        goods_ids = [item.goods_id for item in self.items]
        if len(goods_ids) != len(set(goods_ids)):
            raise ValueError("同一商品不能重复录入多行，请合并数量")
        return self

    @model_validator(mode="after")
    def reject_crossed_dates(self) -> "SalQuotationUpdate":
        """两个字段同时出现时提前拦截有效期早于报价日期；组合旧值的校验在 Service。"""
        if (
            self.quote_date is not None
            and self.valid_until is not None
            and self.valid_until < self.quote_date
        ):
            raise ValueError("报价有效期不能早于报价日期")
        return self


class SalQuotationItemOut(BaseModel):
    """报价明细出参；商品编码/名称/规格/单位由 Service 批量补齐，避免前端逐行查商品。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    goods_id: int
    goods_code: str | None = None  # 商品编码，用于打印与复制预填订单
    goods_name: str | None = None  # 商品名称
    spec: str | None = None  # 商品规格
    unit_name: str | None = None  # 计量单位名称
    qty: Decimal = Decimal("0")  # 报价数量
    price: Decimal = Decimal("0")  # 含税单价
    amount: Decimal = Decimal("0")  # 行金额（qty * price，Service 重算后落库值）


class SalQuotationOut(BaseModel):
    """报价单出参（主表 + 明细）；列表接口只返回主表字段与 itemsCount，明细为空表示未加载。"""

    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)

    id: int
    quote_no: str  # 报价单号，QT+yyyyMMdd+4 位序号
    customer_id: int
    customer_name: str | None = None  # 客户名称，列表与打印抬头使用
    quote_date: date | None = None  # 报价日期
    valid_until: date | None = None  # 报价有效期
    status: int = SalQuotationStatus.DRAFT  # 0 草稿 / 1 已发送 / 2 已失效
    total_amount: Decimal = Decimal("0")  # 含税合计
    tax_amount: Decimal = Decimal("0")  # 税额（V1 报价不拆税，固定 0）
    remark: str | None = None  # 备注
    version: int = 1  # 乐观锁版本号，前端提交修改时回传
    items_count: int = 0  # 明细行数，列表页直接用，免去加载明细
    created_at: datetime | None = None
    items: list[SalQuotationItemOut] = Field(default_factory=list)  # 明细行（详情/打印使用）


class SalQuotationQuery(PageQuery):
    """报价单列表查询：支持状态、报价日期区间与关键字（报价号/客户名称）。"""

    # None 表示不按状态筛选；0/1/2 分别对应草稿/已发送/已失效
    status: int | None = Field(
        default=None, ge=SalQuotationStatus.DRAFT, le=SalQuotationStatus.INVALID
    )
    # 报价日期区间（含边界），前端传 YYYY-MM-DD
    quote_date_start: date | None = None
    quote_date_end: date | None = None


# 单张报价明细行数上限：防止一次提交过大报文拖垮事务
QUOTATION_ITEM_MAX_COUNT = 200
