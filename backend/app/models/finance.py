"""财务模块模型（表名前缀 fin_）。

本文件定义财务模块的全部 9 张表，对应《数据库表设计.md》第 9 节：
  - FinReceivable：应收（销售出库确认时生成，退货按 source_no 冲减）
  - FinPayable：应付（采购入库确认时生成，退货按 source_no 冲减）
  - FinReceipt / FinReceiptItem：收款单与核销明细（可多次核销）
  - FinPayment / FinPaymentItem：付款单与核销明细
  - FinExpense：费用登记（V1 无审批，不生成付款单、不写应付）
  - FinStatement / FinStatementItem：对账单快照与明细行

V1 不建 fin_account / fin_voucher / fin_entry / fin_ar_ap（总账凭证与科目核算属 V2），
早期草稿中的这 4 张表模型已删除，对应引用方需改用本文件的 FinReceivable / FinPayable。

状态枚举（详见设计文档第 1 节）：
  - fin_receivable.status：0 未收 / 1 部分收 / 2 已收
  - fin_payable.status：0 未付 / 1 部分付 / 2 已付
  - fin_expense.status：1 已登记
  - fin_statement.status：1 已生成

冲减与核销口径（1.3）：
  - 退货冲减不建红字，直接减原应收/应付的 amount 与未收/未付，冲减后 amount 保持非负；
    未收/未付为负表示预收/预付/应退，API 额外返回 creditAmount
  - 核销后由 Service 重算 received/paid 与 unreceived/unpaid 及状态，字段不允许前端直传
"""

from datetime import date, datetime  # date：DATE 字段；datetime：核销时间 writeoff_at
from decimal import Decimal  # NUMERIC 字段（金额）的类型标注

from sqlalchemy import (  # SQLAlchemy 列类型与约束构件
    CheckConstraint,  # CHECK 约束，数据库层兜底校验金额
    Date,  # DATE 列类型
    DateTime,  # TIMESTAMPTZ 列类型（带时区时间戳）
    ForeignKey,  # 外键声明
    Index,  # 自定义索引（唯一索引、普通索引、部分索引）
    Numeric,  # NUMERIC 精确小数，金额专用
    SmallInteger,  # SMALLINT，状态/类型类小整数
    String,  # 变长字符串
    text,  # 原样 SQL 片段，用于部分索引的 WHERE 条件
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


# 核销明细表继承链：有审计与软删，但无乐观锁 version（明细随主表控制并发）
_ITEM_MIXINS = (TimestampMixin, UserMixin, SoftDeleteMixin, TenantMixin)


# 应收单表：fin_receivable，销售出库确认时按出库单 total_amount 生成，一单一条
class FinReceivable(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """应收表：fin_receivable。

    字段含义：
      - source_type / source_no：来源类型固定 sales_outbound + 出库单号，用于退货冲减定位
      - bill_date：单据日期（出库日），账龄与对账期基准
      - due_date：到期日 = 出库日 + 客户 credit_days
      - amount：应收金额，销售退货确认时被冲减，保持非负
      - received_amount：已收金额，核销后由 Service 累加
      - unreceived_amount：未收金额 = amount - received_amount，可为负（负数表示应退/预收）
      - status：0 未收 / 1 部分收 / 2 已收，由 Service 按余额重算
    """

    __tablename__ = "fin_receivable"
    __table_args__ = (
        # 客户 + 状态是应收列表与账龄统计的高频组合（设计文档 11.3）
        Index("idx_receivable_customer_status", "customer_id", "status"),
        # 退货按 source_no 定位原应收冲减，必须建索引
        Index("idx_receivable_source_no", "source_no"),
        # 幂等唯一：同一租户同一来源类型+单号只能生成一条应收，防止并发/重试确认生成重复应收
        Index(
            "uq_receivable_source",
            "tenant_id",
            "source_type",
            "source_no",
            unique=True,
            postgresql_where=text("is_deleted = false"),
            sqlite_where=text("is_deleted = 0"),
        ),
        # 冲减后 amount 必须保持非负，数据库层兜底
        CheckConstraint("amount >= 0", name="ck_receivable_amount_nonnegative"),
        # 已收不能超过应收金额，避免已收大于应收导致账实不符
        CheckConstraint("received_amount <= amount", name="ck_receivable_received_le_amount"),
        # 已收非负
        CheckConstraint("received_amount >= 0", name="ck_receivable_received_nonnegative"),
        # 到期日不能早于单据日期
        CheckConstraint("due_date >= bill_date", name="ck_receivable_due_ge_bill"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户，不级联删除
    # 来源类型，固定 sales_outbound
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_no: Mapped[str] = mapped_column(String(64), nullable=False)  # 来源出库单号
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)  # 单据日期（出库日）
    due_date: Mapped[date | None] = mapped_column(Date)  # 到期日，可空
    # 应收金额，退货后被冲减
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 已收
    received_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 未收，可为负（负数表示应退/预收）
    unreceived_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 0 未收/1 部分收/2 已收
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 应付单表：fin_payable，与应收对称，采购入库确认时按入库单 total_amount 生成
class FinPayable(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """应付表：fin_payable。

    字段含义：
      - source_type / source_no：来源类型固定 purchase_inbound + 入库单号
      - due_date：到期日 = 入库日 + 供应商 credit_days
      - amount / paid_amount / unpaid_amount：应付金额（退货冲减）/ 已付 / 未付（可为负）
      - status：0 未付 / 1 部分付 / 2 已付
    """

    __tablename__ = "fin_payable"
    __table_args__ = (
        # 供应商 + 状态高频组合
        Index("idx_payable_supplier_status", "supplier_id", "status"),
        # 退货按原单号定位冲减
        Index("idx_payable_source_no", "source_no"),
        # 幂等唯一：同一租户同一来源类型+单号只能生成一条应付，防止并发/重试确认生成重复应付
        Index(
            "uq_payable_source",
            "tenant_id",
            "source_type",
            "source_no",
            unique=True,
            postgresql_where=text("is_deleted = false"),
            sqlite_where=text("is_deleted = 0"),
        ),
        # 冲减后 amount 必须保持非负
        CheckConstraint("amount >= 0", name="ck_payable_amount_nonnegative"),
        # 已付不能超过应付金额，避免已付大于应付导致账实不符
        CheckConstraint("paid_amount <= amount", name="ck_payable_paid_le_amount"),
        # 已付非负
        CheckConstraint("paid_amount >= 0", name="ck_payable_paid_nonnegative"),
        # 到期日不能早于单据日期
        CheckConstraint("due_date >= bill_date", name="ck_payable_due_ge_bill"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_supplier.id"), nullable=False
    )  # 供应商，不级联删除
    # 来源类型，固定 purchase_inbound
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_no: Mapped[str] = mapped_column(String(64), nullable=False)  # 来源入库单号
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)  # 单据日期（入库日）
    due_date: Mapped[date | None] = mapped_column(Date)  # 到期日，可空
    # 应付金额，退货后被冲减
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 已付金额
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 未付，可为负（负数表示应退/预付）
    unpaid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    # 0 未付/1 部分付/2 已付
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 收款单表：fin_receipt，可一次核销多条应收，allocated/unallocated 由核销事务维护
class FinReceipt(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """收款单表：fin_receipt。

    字段含义：
      - receipt_no：收款单号（V1 不接入单号运行时，固定前缀 + 序列生成，仍保证唯一）
      - method：结算方式，取值走字典（sys_dict_item，type_code=结算方式）
      - account：收款账户
      - amount：收款总额，必须大于 0
      - allocated_amount：已核销合计 = 各核销明细 writeoff_amount 之和，Service 维护
      - unallocated_amount：未分配 = amount - allocated_amount，Service 维护
    """

    __tablename__ = "fin_receipt"
    __table_args__ = (
        Index("uq_fin_receipt_no", "receipt_no", unique=True),  # 收款单号唯一
        Index("idx_fin_receipt_customer", "customer_id"),  # 按客户筛选收款单
        CheckConstraint("amount > 0", name="ck_fin_receipt_amount_positive"),  # 收款总额必须为正
        # 已核销非负
        CheckConstraint("allocated_amount >= 0", name="ck_fin_receipt_allocated_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    receipt_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 收款单号，唯一
    customer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_customer.id"), nullable=False
    )  # 客户，不级联删除
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)  # 收款日期
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 收款总额，>0
    method: Mapped[str | None] = mapped_column(String(32))  # 结算方式，走字典
    account: Mapped[str | None] = mapped_column(String(128))  # 收款账户
    operator_id: Mapped[int | None] = mapped_column(BIGINT_PK)  # 经办人用户 ID
    # 已核销合计，核销事务累加
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 未分配 = amount - allocated_amount，Service 维护
    unallocated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 收款核销明细表：fin_receipt_item，核销总额 ≤ 收款金额，单条 ≤ 该应收未收，客户必须一致
class FinReceiptItem(*_ITEM_MIXINS, Base):
    """收款核销明细表：fin_receipt_item。

    字段含义：
      - receivable_id：被核销的应收，核销后 Service 重算其 received/unreceived/status
      - writeoff_amount：本次核销金额，>0
      - writeoff_at：核销时间（TIMESTAMPTZ），对账单按此时间归集本期收款
    """

    __tablename__ = "fin_receipt_item"
    __table_args__ = (
        Index("idx_receipt_item_receipt", "receipt_id"),  # 按收款单查核销明细
        # 按应收反查核销记录（设计文档 11.3）
        Index("idx_receipt_item_receivable", "receivable_id"),
        # 核销额为正
        CheckConstraint("writeoff_amount > 0", name="ck_receipt_item_writeoff_positive"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    receipt_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("fin_receipt.id", ondelete="CASCADE"), nullable=False
    )  # 所属收款单，级联删除
    receivable_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("fin_receivable.id"), nullable=False
    )  # 被核销的应收，不级联（应收删除受状态约束）
    writeoff_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 本次核销金额
    # 核销时间，对账归集基准
    writeoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# 付款单表：fin_payment，与收款单对称
class FinPayment(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """付款单表：fin_payment。

    字段含义：
      - payment_no：付款单号（V1 固定前缀 + 序列生成，保证唯一）
      - amount：付款总额，>0
      - allocated_amount / unallocated_amount：已核销 / 未分配，Service 维护
    """

    __tablename__ = "fin_payment"
    __table_args__ = (
        Index("uq_fin_payment_no", "payment_no", unique=True),  # 付款单号唯一
        Index("idx_fin_payment_supplier", "supplier_id"),  # 按供应商筛选付款单
        CheckConstraint("amount > 0", name="ck_fin_payment_amount_positive"),  # 付款总额必须为正
        # 已核销非负
        CheckConstraint("allocated_amount >= 0", name="ck_fin_payment_allocated_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    payment_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 付款单号，唯一
    supplier_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_supplier.id"), nullable=False
    )  # 供应商，不级联删除
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)  # 付款日期
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 付款总额，>0
    method: Mapped[str | None] = mapped_column(String(32))  # 结算方式，走字典
    account: Mapped[str | None] = mapped_column(String(128))  # 付款账户
    operator_id: Mapped[int | None] = mapped_column(BIGINT_PK)  # 经办人用户 ID
    # 已核销合计，核销事务累加
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 未分配 = amount - allocated_amount，Service 维护
    unallocated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 付款核销明细表：fin_payment_item，与收款核销明细对称
class FinPaymentItem(*_ITEM_MIXINS, Base):
    """付款核销明细表：fin_payment_item。

    字段含义：
      - payable_id：被核销的应付，核销后 Service 重算其 paid/unpaid/status
      - writeoff_amount：本次核销金额，>0
      - writeoff_at：核销时间，对账单按此时间归集本期付款
    """

    __tablename__ = "fin_payment_item"
    __table_args__ = (
        Index("idx_payment_item_payment", "payment_id"),  # 按付款单查核销明细
        Index("idx_payment_item_payable", "payable_id"),  # 按应付反查核销记录
        # 核销额为正
        CheckConstraint("writeoff_amount > 0", name="ck_payment_item_writeoff_positive"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    payment_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("fin_payment.id", ondelete="CASCADE"), nullable=False
    )  # 所属付款单，级联删除
    payable_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("fin_payable.id"), nullable=False
    )  # 被核销的应付，不级联
    writeoff_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 本次核销金额
    # 核销时间，对账归集基准
    writeoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# 费用表：fin_expense，V1 无审批，仅登记；不生成付款单、不写应付
class FinExpense(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """费用表：fin_expense。

    字段含义：
      - expense_no：费用单号（V1 固定前缀 + 序列生成，保证唯一）
      - type：费用类型，取值走字典（sys_dict_item，type_code=费用类型）
      - amount：费用金额，必须大于 0
      - dept_id：归属部门（数据范围过滤用）
      - applicant_id：申请人
      - status：1 已登记（V1 无审批流）
    """

    __tablename__ = "fin_expense"
    __table_args__ = (
        Index("uq_fin_expense_no", "expense_no", unique=True),  # 费用单号唯一
        Index("idx_fin_expense_dept", "dept_id"),  # 部门是数据范围过滤高频条件
        CheckConstraint("amount > 0", name="ck_fin_expense_amount_positive"),  # 费用金额必须为正
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    expense_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 费用单号，唯一
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # 费用类型，走字典
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 费用金额，>0
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)  # 费用日期
    # 归属部门
    dept_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_dept.id"), nullable=False)
    # 申请人
    applicant_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)  # 1 已登记
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 对账单主表：fin_statement，生成即快照，不可编辑，可重复导出
class FinStatement(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """对账单表：fin_statement（快照）。

    字段含义：
      - party_type：1 客户 / 2 供应商；party_id 为对应客户或供应商主键
      - period_start / period_end：对账期间，含首尾
      - begin_amount：期初余额，按业务日期重算（不得用当前 amount 倒推，避免未来退货污染历史）
      - current_amount：本期发生额；end_amount = 期初 + 本期
      - status：1 已生成
    唯一约束：同往来单位同期间只能生成一张，重复生成返回 409。
    """

    __tablename__ = "fin_statement"
    __table_args__ = (
        Index("uq_fin_statement_no", "statement_no", unique=True),  # 对账单号唯一
        # 同一往来单位同一期间唯一：重复生成在数据库层兜底（部分索引，软删行不占用唯一键）
        Index(
            "uq_fin_statement_party_period",
            "party_type",
            "party_id",
            "period_start",
            "period_end",
            unique=True,
            sqlite_where=text("is_deleted = 0"),  # SQLite 部分索引条件
            postgresql_where=text("is_deleted = false"),  # PostgreSQL 部分索引条件
        ),
        # 期间起止合法
        CheckConstraint("period_start <= period_end", name="ck_fin_statement_period"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    statement_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 对账单号，唯一
    party_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1 客户 / 2 供应商
    party_id: Mapped[int] = mapped_column(BIGINT_PK, nullable=False)  # 客户或供应商 ID
    period_start: Mapped[date] = mapped_column(Date, nullable=False)  # 期间开始（含）
    period_end: Mapped[date] = mapped_column(Date, nullable=False)  # 期间结束（含）
    # 期初余额，按业务日期重算
    begin_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 本期发生
    current_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 期末余额
    end_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)  # 1 已生成
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 对账单明细行表：fin_statement_item，balance 为行后滚动余额
class FinStatementItem(*_ITEM_MIXINS, Base):
    """对账单明细行表：fin_statement_item。

    字段含义：
      - biz_date：业务日期（应收/应付取来源单日期、退货取退货单日期、收付款取核销时间）
      - debit_amount / credit_amount：借方（增加应收/应付）/ 贷方（减少）
      - balance：本行后余额 = 上一行 balance + debit - credit，生成时由 Service 顺序计算
    """

    __tablename__ = "fin_statement_item"
    __table_args__ = (
        Index("idx_statement_item_statement", "statement_id"),  # 按对账单查明细行
        # 借方非负
        CheckConstraint("debit_amount >= 0", name="ck_statement_item_debit_nonnegative"),
        # 贷方非负
        CheckConstraint("credit_amount >= 0", name="ck_statement_item_credit_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    statement_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("fin_statement.id", ondelete="CASCADE"), nullable=False
    )  # 所属对账单，级联删除
    biz_date: Mapped[date] = mapped_column(Date, nullable=False)  # 业务日期
    source_no: Mapped[str | None] = mapped_column(String(64))  # 来源单号，可空
    summary: Mapped[str | None] = mapped_column(String(255))  # 摘要
    # 借方（增）
    debit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    # 贷方（减）
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # 行后余额
