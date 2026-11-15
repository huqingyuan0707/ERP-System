"""销售模块模型（表名前缀 sal_）。

本文件定义销售模块的全部 8 张表，对应《数据库表设计.md》第 7 节：
  - SalQuotation / SalQuotationItem：销售报价主表与明细
  - SalOrder / SalOrderItem：销售订单主表与明细（outbound_qty 记录累计已出库）
  - SalOutbound / SalOutboundItem：销售出库主表与明细（草稿锁库存，确认后生成应收）
  - SalReturn / SalReturnItem：销售退货主表与明细（source_no 定位原出库单，G1/G10）

状态枚举（详见设计文档第 1 节，代码中统一用常量，禁止裸数字散落业务代码）：
  - sal_quotation.status：0 草稿 / 1 已发送 / 2 已失效
  - sal_order.status：0 草稿 / 1 待审 / 2 已审 / 3 部分出库 / 4 完成 / 5 关闭
  - sal_outbound / sal_return.status：0 草稿 / 1 已确认

公共字段通过 Mixin 继承（见 app/models/mixins.py），明细表不继承 VersionMixin
（明细不独立乐观锁，随主表 version 一起控制并发）。
"""

from datetime import date  # DATE 字段（下单日期、报价有效期等）的类型标注
from decimal import Decimal  # NUMERIC 字段（金额、数量）的类型标注

from sqlalchemy import (  # SQLAlchemy 列类型与约束构件
    CheckConstraint,  # CHECK 约束，数据库层兜底校验数量/金额/税率
    Date,  # DATE 列类型
    ForeignKey,  # 外键声明
    Index,  # 自定义索引
    Numeric,  # NUMERIC 精确小数，金额/数量专用
    SmallInteger,  # SMALLINT，状态类小整数
    String,  # 变长字符串
)
from sqlalchemy.orm import Mapped, mapped_column  # SQLAlchemy 2.0 类型化映射写法

from app.db.base import Base  # 所有模型的声明基类
from app.models.mixins import (  # 公共字段 Mixin 与跨库主键类型
    BIGINT_PK,  # PostgreSQL BIGINT / SQLite INTEGER 的主键类型变体
    SoftDeleteMixin,  # is_deleted 逻辑删除
    TenantMixin,  # tenant_id 多租户隔离
    TimestampMixin,  # created_at / updated_at
    UserMixin,  # created_by / updated_by
    VersionMixin,  # version 乐观锁
)


# 明细表公共继承链：有审计与软删，但无乐观锁 version（设计文档第 2 节例外规则）
_ITEM_MIXINS = (TimestampMixin, UserMixin, SoftDeleteMixin, TenantMixin)


# 销售报价主表：sal_quotation，状态机 0 草稿 → 1 已发送 → 2 已失效，可复制预填订单
class SalQuotation(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """销售报价表：sal_quotation。

    字段含义：
      - quote_no：报价单号，前缀 QT + yyyyMMdd + 4 位序号
      - customer_id：客户
      - quote_date：报价日期
      - valid_until：报价有效期，必须 ≥ quote_date（Service 校验）
      - status：0 草稿 / 1 已发送 / 2 已失效
      - total_amount / tax_amount：含税合计 / 税额
    """

    __tablename__ = "sal_quotation"
    __table_args__ = (
        Index("uq_sal_quotation_no", "quote_no", unique=True),  # 报价单号唯一，不复用
        Index("idx_sal_quotation_customer", "customer_id"),  # 按客户筛选报价
        CheckConstraint("total_amount >= 0", name="ck_sal_quotation_total_nonnegative"),  # 金额非负
        CheckConstraint("tax_amount >= 0", name="ck_sal_quotation_tax_nonnegative"),  # 税额非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    quote_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 报价单号，唯一
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户，不级联删除
    quote_date: Mapped[date] = mapped_column(Date, nullable=False)  # 报价日期
    valid_until: Mapped[date | None] = mapped_column(Date)  # 报价有效期，可空
    # 0 草稿/1 已发送/2 已失效
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # 含税合计
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 税额
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 销售报价明细表：sal_quotation_item
class SalQuotationItem(*_ITEM_MIXINS, Base):
    """销售报价明细表：sal_quotation_item。

    字段含义：
      - quotation_id：所属报价单，级联删除
      - qty / price / amount：报价数量 / 含税单价 / 行金额（Service 重算）
    """

    __tablename__ = "sal_quotation_item"
    __table_args__ = (
        Index("idx_sal_quotation_item_quotation", "quotation_id"),  # 按报价单查明细
        CheckConstraint("qty > 0", name="ck_sal_quotation_item_qty_positive"),  # 报价数量必须为正
        CheckConstraint("price >= 0", name="ck_sal_quotation_item_price_nonnegative"),  # 单价非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    quotation_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_quotation.id", ondelete="CASCADE"), nullable=False
    )  # 所属报价单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 报价数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 含税单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算


# 销售订单主表：sal_order，无 warehouse_id（G3），审核时只做客户信用校验，锁库存发生在出库草稿
class SalOrder(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """销售订单表：sal_order。

    字段含义：
      - order_no：订单号，前缀 SO + yyyyMMdd + 4 位序号
      - customer_id：客户；审核时校验「未收应收净额 + 本单金额 ≤ credit_limit」
      - status：0 草稿/1 待审/2 已审/3 部分出库/4 完成/5 关闭
      - discount：整单折扣金额（非折扣率）；total_amount = Σ明细 amount - discount，
        tax_amount 按折扣后金额同比例分摊重算，尾差归最后一行（G8：审核时一并确认折扣）
      - audit_by / audit_at / audit_remark：审核人 / 审核时间 / 驳回原因
    """

    __tablename__ = "sal_order"
    __table_args__ = (
        Index("uq_sal_order_no", "order_no", unique=True),  # 订单号唯一
        Index("idx_sal_order_customer", "customer_id"),  # 按客户筛选与信用校验
        CheckConstraint("total_amount >= 0", name="ck_sal_order_total_nonnegative"),  # 金额非负
        CheckConstraint("tax_amount >= 0", name="ck_sal_order_tax_nonnegative"),  # 税额非负
        CheckConstraint("discount >= 0", name="ck_sal_order_discount_nonnegative"),  # 折扣非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    order_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 订单号，唯一
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户，不级联删除
    order_date: Mapped[date] = mapped_column(Date, nullable=False)  # 下单日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 单据状态
    # 折扣后含税合计
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 折扣后税额
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 整单折扣金额
    discount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    audit_by: Mapped[int | None] = mapped_column(BIGINT_PK)  # 审核人用户 ID
    audit_at: Mapped[date | None] = mapped_column(Date)  # 审核时间
    audit_remark: Mapped[str | None] = mapped_column(String(255))  # 驳回原因
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 销售订单明细表：sal_order_item，outbound_qty 由出库确认事务累加，用于回写订单状态
class SalOrderItem(*_ITEM_MIXINS, Base):
    """销售订单明细表：sal_order_item。

    字段含义：
      - unit_id：计量单位
      - qty：订购数量，>0
      - price：含税单价；amount = round(qty × price, 2)，由 Service 重算
      - tax_rate：税率 0～1
      - outbound_qty：累计已出库数量，出库确认时累加（部分出库与完成判定依据）
    """

    __tablename__ = "sal_order_item"
    __table_args__ = (
        Index("idx_sal_order_item_order", "order_id"),  # 按订单查明细
        CheckConstraint("qty > 0", name="ck_sal_order_item_qty_positive"),  # 订购数量必须为正
        CheckConstraint("price >= 0", name="ck_sal_order_item_price_nonnegative"),  # 单价非负
        # 税率 0～1
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 1", name="ck_sal_order_item_tax_rate"),
        # 累计出库非负
        CheckConstraint("outbound_qty >= 0", name="ck_sal_order_item_outbound_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    order_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_order.id", ondelete="CASCADE"), nullable=False
    )  # 所属订单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    # 单位
    unit_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_unit.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 订购数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 含税单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0, nullable=False)  # 税率 0～1
    # 累计已出库
    outbound_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)


# 销售出库主表：sal_outbound，草稿保存即锁库存，确认后释放锁定并扣账面、生成应收
class SalOutbound(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """销售出库表：sal_outbound。

    字段含义：
      - outbound_no：出库单号，前缀 OUT + yyyyMMdd + 4 位序号
      - warehouse_id：出库仓库（G3：订单无仓库字段，锁定发生在本表草稿保存时）
      - order_id：来源销售订单
      - outbound_date：出库日期，应收到期日 = 该日期 + 客户 credit_days
      - status：0 草稿 / 1 已确认
    """

    __tablename__ = "sal_outbound"
    __table_args__ = (
        Index("uq_sal_outbound_no", "outbound_no", unique=True),  # 出库单号唯一
        Index("idx_sal_outbound_order", "order_id"),  # 按来源订单查出库
        Index("idx_sal_outbound_warehouse", "warehouse_id"),  # 按仓库筛选
        CheckConstraint("total_amount >= 0", name="ck_sal_outbound_total_nonnegative"),  # 金额非负
        CheckConstraint("tax_amount >= 0", name="ck_sal_outbound_tax_nonnegative"),  # 税额非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    outbound_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 出库单号，唯一
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 出库仓库
    order_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_order.id"), nullable=False
    )  # 来源订单
    outbound_date: Mapped[date] = mapped_column(Date, nullable=False)  # 出库日期，应收到期日基准
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    # 含税合计
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 税额
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 销售出库明细表：sal_outbound_item，order_item_id 用于出库确认时回写订单累计出库量
class SalOutboundItem(*_ITEM_MIXINS, Base):
    """销售出库明细表：sal_outbound_item。

    字段含义：
      - order_item_id：对应的销售订单明细，出库确认时按它累加 outbound_qty
      - qty：出库数量，>0；同时是草稿锁定库存与确认扣减账面的数量
    """

    __tablename__ = "sal_outbound_item"
    __table_args__ = (
        Index("idx_sal_outbound_item_outbound", "outbound_id"),  # 按出库单查明细
        CheckConstraint("qty > 0", name="ck_sal_outbound_item_qty_positive"),  # 出库数量必须为正
        CheckConstraint("price >= 0", name="ck_sal_outbound_item_price_nonnegative"),  # 单价非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    outbound_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_outbound.id", ondelete="CASCADE"), nullable=False
    )  # 所属出库单，级联删除
    # 对应订单明细：不级联，由 Service 保证与订单状态一致
    order_item_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_order_item.id"), nullable=False
    )
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 出库数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 含税单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算


# 销售退货主表：sal_return，source_no 定位原出库单（G1/G10），确认后冲减应收并回补库存
class SalReturn(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """销售退货表：sal_return。

    字段含义：
      - source_no：原出库单号，必填；用于定位原应收 fin_receivable 并按「原单号+商品」
        校验累计退货量，是 G1 冲减口径与 G10 定位原单的关键字段
      - return_date：退货日期，报表按此归集
      - status：0 草稿 / 1 已确认；确认后不可改
      - total_amount：退货总金额，确认时按此冲减原应收 amount 与 unreceived_amount
    """

    __tablename__ = "sal_return"
    __table_args__ = (
        Index("uq_sal_return_no", "return_no", unique=True),  # 退货单号唯一
        # 按原单号冲减应收与校验累计退货量，必须建索引
        Index("idx_sal_return_source_no", "source_no"),
        # 退货金额非负
        CheckConstraint("total_amount >= 0", name="ck_sal_return_total_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    return_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 退货单号，唯一
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 退货入库仓库
    source_no: Mapped[str] = mapped_column(String(64), nullable=False)  # 原出库单号，必填
    return_date: Mapped[date] = mapped_column(Date, nullable=False)  # 退货日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    # 退货总金额
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 销售退货明细表：sal_return_item，累计可退量按 source_no + goods_id 汇总校验
class SalReturnItem(*_ITEM_MIXINS, Base):
    """销售退货明细表：sal_return_item。

    字段含义：
      - qty：退货数量，>0；累计（同 source_no + goods_id）不得超过原出库量
      - price：退货单价；库存按当前 avg_cost 入库，财务按此单价冲减应收
    """

    __tablename__ = "sal_return_item"
    __table_args__ = (
        Index("idx_sal_return_item_return", "return_id"),  # 按退货单查明细
        CheckConstraint("qty > 0", name="ck_sal_return_item_qty_positive"),  # 退货数量必须为正
        CheckConstraint("price >= 0", name="ck_sal_return_item_price_nonnegative"),  # 退货单价非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    return_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("sal_return.id", ondelete="CASCADE"), nullable=False
    )  # 所属退货单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 退货数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 退货单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算
