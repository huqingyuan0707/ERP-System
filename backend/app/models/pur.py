"""采购模块模型（表名前缀 pur_）。

本文件定义采购模块的全部 8 张表，对应《数据库表设计.md》第 6 节：
  - PurApply / PurApplyItem：采购申请主表与明细
  - PurOrder / PurOrderItem：采购订单主表与明细（inbound_qty 记录累计已入库）
  - PurInbound / PurInboundItem：采购入库主表与明细（确认后生成应付、写库存）
  - PurReturn / PurReturnItem：采购退货主表与明细（source_no 定位原入库单，G1/G10）

状态枚举（详见设计文档第 1 节，代码中统一用常量，禁止裸数字散落业务代码）：
  - pur_apply.status：0 草稿 / 1 待审 / 2 已审
  - pur_order.status：0 草稿 / 1 待审 / 2 已审 / 3 部分入库 / 4 完成 / 5 关闭
  - pur_inbound / pur_return.status：0 草稿 / 1 已确认

公共字段通过 Mixin 继承（见 app/models/mixins.py），明细表不继承 VersionMixin
（明细不独立乐观锁，随主表 version 一起控制并发）。
"""

from datetime import date  # DATE 字段（下单日期、需求日期等）的类型标注
from decimal import Decimal  # NUMERIC 字段（金额、数量）的类型标注

from sqlalchemy import (  # SQLAlchemy 列类型与约束构件
    CheckConstraint,  # CHECK 约束，数据库层兜底校验数量/金额/税率
    Date,  # DATE 列类型，只存日期不含时间
    ForeignKey,  # 外键声明
    Index,  # 自定义索引（唯一索引、普通索引、部分索引）
    Numeric,  # NUMERIC 精确小数，金额/数量专用，禁止 float
    SmallInteger,  # SMALLINT，状态/方向类小整数
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


# 采购申请主表：pur_apply，状态机 0 草稿 → 1 待审 → 2 已审，驳回回草稿并写 audit_remark
class PurApply(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """采购申请表：pur_apply。

    字段含义：
      - apply_no：申请单号，前缀 PA + yyyyMMdd + 4 位序号，全库唯一（软删不复用）
      - dept_id：申请部门，数据范围「本部门」过滤优先用此字段
      - applicant_id：申请人
      - need_date：需求日期，到货期限参考
      - status：0 草稿 / 1 待审 / 2 已审
      - audit_by / audit_at / audit_remark：审核人 / 审核时间 / 驳回原因
    """

    __tablename__ = "pur_apply"

    # 申请单号唯一：单号不复用，用普通唯一索引（不过滤 is_deleted）
    __table_args__ = (
        Index("uq_pur_apply_no", "apply_no", unique=True),
        # 部门与申请人是数据范围过滤与列表查询的高频条件
        Index("idx_pur_apply_dept", "dept_id"),
        Index("idx_pur_apply_applicant", "applicant_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    apply_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 申请单号，唯一
    # 申请部门：指向系统部门表；不级联删除，避免误删部门连带删历史单据
    dept_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_dept.id"), nullable=False)
    # 申请人：指向系统用户表
    applicant_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    need_date: Mapped[date | None] = mapped_column(Date)  # 需求日期，可空
    # 0 草稿/1 待审/2 已审
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    audit_by: Mapped[int | None] = mapped_column(BIGINT_PK)  # 审核人用户 ID，未审为空
    audit_at: Mapped[date | None] = mapped_column(Date)  # 审核时间（业务日期粒度）
    audit_remark: Mapped[str | None] = mapped_column(String(255))  # 驳回原因，退回草稿时写入
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 采购申请明细表：pur_apply_item，一条申请包含多行商品需求
class PurApplyItem(*_ITEM_MIXINS, Base):
    """采购申请明细表：pur_apply_item。

    字段含义：
      - apply_id：所属申请主表 ID，主表删除时明细级联删除
      - goods_id：商品
      - qty：申请数量，必须大于 0
      - need_date：行级需求日期，可覆盖主表日期
    """

    __tablename__ = "pur_apply_item"
    __table_args__ = (
        # 明细按主表 ID 查询是最高频路径，外键必须建索引（设计文档 11.3）
        Index("idx_apply_item_apply", "apply_id"),
        # 申请数量必须为正数，数据库层兜底
        CheckConstraint("qty > 0", name="ck_pur_apply_item_qty_positive"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    # 所属申请：级联删除，主表删除时明细一并清理
    apply_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_apply.id", ondelete="CASCADE"), nullable=False
    )
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 申请数量，>0
    need_date: Mapped[date | None] = mapped_column(Date)  # 行级需求日期，可空


# 采购订单主表：pur_order，状态机 0 草稿 → 1 待审 → 2 已审 → 3 部分入库 → 4 完成；
# 前三种状态可关闭(5)
class PurOrder(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """采购订单表：pur_order。

    字段含义：
      - order_no：订单号，前缀 PO + yyyyMMdd + 4 位序号
      - supplier_id：供应商
      - order_date：下单日期
      - status：0 草稿/1 待审/2 已审/3 部分入库/4 完成/5 关闭
      - total_amount：含税合计，等于各行明细 amount 之和，不再二次四舍五入
      - tax_amount：税额，按含税口径 Σ(amount - amount/(1+tax_rate)) 汇总
    """

    __tablename__ = "pur_order"
    __table_args__ = (
        Index("uq_pur_order_no", "order_no", unique=True),  # 订单号全库唯一，不复用
        Index("idx_pur_order_supplier", "supplier_id"),  # 供应商是列表筛选与应付追溯的高频条件
        CheckConstraint("total_amount >= 0", name="ck_pur_order_total_nonnegative"),  # 金额非负
        CheckConstraint("tax_amount >= 0", name="ck_pur_order_tax_nonnegative"),  # 税额非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    order_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 订单号，唯一
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_supplier.id"), nullable=False
    )  # 供应商，不级联删除
    order_date: Mapped[date] = mapped_column(Date, nullable=False)  # 下单日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 单据状态
    # 含税合计
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 税额
    audit_by: Mapped[int | None] = mapped_column(BIGINT_PK)  # 审核人用户 ID
    audit_at: Mapped[date | None] = mapped_column(Date)  # 审核时间
    audit_remark: Mapped[str | None] = mapped_column(String(255))  # 驳回原因
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 采购订单明细表：pur_order_item，inbound_qty 由入库确认事务累加，用于回写订单状态
class PurOrderItem(*_ITEM_MIXINS, Base):
    """采购订单明细表：pur_order_item。

    字段含义：
      - order_id：所属订单，级联删除
      - unit_id：计量单位
      - qty：订购数量，>0
      - price：含税单价，≥0
      - amount：行金额 = round(qty × price, 2)，由 Service 重算，不允许前端直传
      - tax_rate：税率，0～1（0.13 表示 13%）
      - inbound_qty：累计已入库数量，默认 0，入库确认时累加（部分入库与完成判定依据）
    """

    __tablename__ = "pur_order_item"
    __table_args__ = (
        Index("idx_pur_order_item_order", "order_id"),  # 按订单查明细的高频索引
        CheckConstraint("qty > 0", name="ck_pur_order_item_qty_positive"),  # 订购数量必须为正
        CheckConstraint("price >= 0", name="ck_pur_order_item_price_nonnegative"),  # 单价非负
        # 税率取值 0～1，0.13 表示 13%
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 1", name="ck_pur_order_item_tax_rate"),
        # 累计入库非负
        CheckConstraint("inbound_qty >= 0", name="ck_pur_order_item_inbound_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    order_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_order.id", ondelete="CASCADE"), nullable=False
    )  # 所属订单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    # 单位
    unit_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_unit.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 订购数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 含税单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0, nullable=False)  # 税率 0～1
    # 累计已入库
    inbound_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)


# 采购入库主表：pur_inbound，确认后同一事务写库存+流水+回写订单+生成应付
class PurInbound(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """采购入库表：pur_inbound。

    字段含义：
      - inbound_no：入库单号，前缀 PI + yyyyMMdd + 4 位序号
      - warehouse_id：入库仓库
      - order_id：来源采购订单（G4：一张入库单对应一张订单，订单可多次部分入库）
      - inbound_date：入库日期，应付到期日 = 该日期 + 供应商 credit_days
      - status：0 草稿 / 1 已确认；确认后不可改，冲正走采购退货
    """

    __tablename__ = "pur_inbound"
    __table_args__ = (
        Index("uq_pur_inbound_no", "inbound_no", unique=True),  # 入库单号唯一
        Index("idx_pur_inbound_order", "order_id"),  # 按来源订单查入库记录
        Index("idx_pur_inbound_warehouse", "warehouse_id"),  # 按仓库筛选
        CheckConstraint("total_amount >= 0", name="ck_pur_inbound_total_nonnegative"),  # 金额非负
        CheckConstraint("tax_amount >= 0", name="ck_pur_inbound_tax_nonnegative"),  # 税额非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    inbound_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 入库单号，唯一
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_supplier.id"), nullable=False
    )  # 供应商，不级联删除
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 入库仓库
    order_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_order.id"), nullable=False
    )  # 来源订单
    inbound_date: Mapped[date] = mapped_column(Date, nullable=False)  # 入库日期，应付到期日基准
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    # 含税合计
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 税额
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 采购入库明细表：pur_inbound_item，order_item_id 用于入库确认时回写订单累计入库量
class PurInboundItem(*_ITEM_MIXINS, Base):
    """采购入库明细表：pur_inbound_item。

    字段含义：
      - order_item_id：对应的采购订单明细，入库确认时按它累加 inbound_qty
      - qty：入库数量，>0
      - price：入库含税单价，同时作为移动加权平均成本的入仓价格
      - batch_no：批次号，可空
    """

    __tablename__ = "pur_inbound_item"
    __table_args__ = (
        Index("idx_pur_inbound_item_inbound", "inbound_id"),  # 按入库单查明细
        CheckConstraint("qty > 0", name="ck_pur_inbound_item_qty_positive"),  # 入库数量必须为正
        CheckConstraint("price >= 0", name="ck_pur_inbound_item_price_nonnegative"),  # 单价非负
        # 税率 0～1
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 1", name="ck_pur_inbound_item_tax_rate"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    inbound_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_inbound.id", ondelete="CASCADE"), nullable=False
    )  # 所属入库单，级联删除
    # 对应订单明细：不级联（订单明细删除受订单状态约束），由 Service 保证一致性
    order_item_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_order_item.id"), nullable=False
    )
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 入库数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 入库含税单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=0, nullable=False)  # 税率 0～1
    batch_no: Mapped[str | None] = mapped_column(String(64))  # 批次号，可空


# 采购退货主表：pur_return，source_no 定位原入库单（G1/G10），确认后冲减应付并扣库存
class PurReturn(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """采购退货表：pur_return。

    字段含义：
      - source_no：原入库单号，必填；用于定位原应付 fin_payable 并按「原单号+商品」
        校验累计退货量，是 G1 冲减口径与 G10 定位原单的关键字段
      - return_date：退货日期，报表按此归集
      - status：0 草稿 / 1 已确认；确认后不可改
      - total_amount：退货总金额，确认时按此冲减原应付 amount 与 unpaid_amount
    """

    __tablename__ = "pur_return"
    __table_args__ = (
        Index("uq_pur_return_no", "return_no", unique=True),  # 退货单号唯一
        # 按原单号冲减应付与校验累计退货量，必须建索引
        Index("idx_pur_return_source_no", "source_no"),
        # 退货金额非负
        CheckConstraint("total_amount >= 0", name="ck_pur_return_total_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    return_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 退货单号，唯一
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_supplier.id"), nullable=False
    )  # 供应商
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 退货出库仓库
    source_no: Mapped[str] = mapped_column(String(64), nullable=False)  # 原入库单号，必填
    return_date: Mapped[date] = mapped_column(Date, nullable=False)  # 退货日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    # 退货总金额
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 采购退货明细表：pur_return_item，累计可退量按 source_no + goods_id 汇总校验
class PurReturnItem(*_ITEM_MIXINS, Base):
    """采购退货明细表：pur_return_item。

    字段含义：
      - qty：退货数量，>0；累计（同 source_no + goods_id）不得超过原入库量
      - price：退货单价；库存按当前 avg_cost 出库，财务按此单价冲减应付
      - amount：行金额，Service 重算
    """

    __tablename__ = "pur_return_item"
    __table_args__ = (
        Index("idx_pur_return_item_return", "return_id"),  # 按退货单查明细
        CheckConstraint("qty > 0", name="ck_pur_return_item_qty_positive"),  # 退货数量必须为正
        CheckConstraint("price >= 0", name="ck_pur_return_item_price_nonnegative"),  # 退货单价非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    return_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("pur_return.id", ondelete="CASCADE"), nullable=False
    )  # 所属退货单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 退货数量
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 退货单价
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行金额，Service 重算
