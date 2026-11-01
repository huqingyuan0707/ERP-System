"""财务核心模型（表名前缀 fin_）。

包含：
  - FinAccount：总账科目（资产/负债/所有者权益/成本/损益）
  - FinEntry：总账分录（凭证行，借贷方发生额）
  - FinVoucher：记账凭证（多条分录的集合，借贷必须平衡）
  - ArApRecord：应收应付记录（与采购/销售单据关联，用于对账）
"""

from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import BIGINT_PK, TenantMixin, TimestampMixin, UserMixin


class FinAccount(TenantMixin, TimestampMixin, UserMixin, Base):
    """总账科目表：fin_account。

    科目树通过 parent_id 自关联，支持多级科目体系。
    """

    __tablename__ = "fin_account"
    __table_args__ = (
        Index("uq_fin_account_code_tenant", "code", "tenant_id", unique=True),
        Index("ix_fin_account_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, comment="科目编码，如 1001、1102")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="科目名称，如 库存商品、应付账款")
    parent_id: Mapped[int | None] = mapped_column(
        BIGINT_PK, ForeignKey("fin_account.id"), nullable=True, comment="父科目 ID"
    )
    # 科目类型：1=资产，2=负债，3=所有者权益，4=成本，5=损益
    type: Mapped[int] = mapped_column(Integer, nullable=False, comment="科目类型")
    direction: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="借贷方向：1=借方增加，2=贷方增加"
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="科目余额"
    )
    is_leaf: Mapped[bool] = mapped_column(
        default=True, nullable=False, comment="是否末级科目，只有末级科目可记账"
    )
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="状态：1=启用，0=禁用"
    )
    remark: Mapped[str | None] = mapped_column(String(255), comment="备注")


class FinVoucher(TenantMixin, TimestampMixin, UserMixin, Base):
    """记账凭证表：fin_voucher。

    一条凭证包含多条分录，凭证级借贷必须平衡。
    """

    __tablename__ = "fin_voucher"
    __table_args__ = (
        Index("ix_fin_voucher_no", "voucher_no"),
        Index("ix_fin_voucher_tenant_date", "tenant_id", "voucher_date"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    voucher_no: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="凭证号，如 FV202611010001"
    )
    voucher_date: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="凭证日期，格式 YYYYMMDD"
    )
    # 摘要：凭证级说明
    summary: Mapped[str | None] = mapped_column(String(255), comment="凭证摘要")
    # 凭证状态：1=待审，2=已审，3=已记账，4=作废
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="凭证状态：1=待审，2=已审，3=已记账，4=作废"
    )
    # 总借方金额 / 总贷方金额，用于快速校验平衡
    total_debit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="凭证总借方金额"
    )
    total_credit: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="凭证总贷方金额"
    )
    source_type: Mapped[str | None] = mapped_column(
        String(32), comment="来源单据类型，如 purchase_in、sale_out"
    )
    source_no: Mapped[str | None] = mapped_column(
        String(64), comment="来源单号"
    )
    remark: Mapped[str | None] = mapped_column(String(255), comment="备注")


class FinEntry(TenantMixin, TimestampMixin, UserMixin, Base):
    """总账分录表：fin_entry。

    每条分录关联一个科目，记录借方或贷方发生额。
    """

    __tablename__ = "fin_entry"
    __table_args__ = (
        Index("ix_fin_entry_voucher", "voucher_id"),
        Index("ix_fin_entry_account", "account_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    voucher_id: Mapped[int] = mapped_column(
        BIGINT_PK,
        ForeignKey("fin_voucher.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属凭证 ID",
    )
    account_id: Mapped[int] = mapped_column(
        BIGINT_PK,
        ForeignKey("fin_account.id"),
        nullable=False,
        comment="科目 ID",
    )
    # 借方发生额 / 贷方发生额，两者只能有一个有值
    debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="借方发生额"
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="贷方发生额"
    )
    summary: Mapped[str | None] = mapped_column(String(255), comment="分录摘要")
    source_type: Mapped[str | None] = mapped_column(String(32), comment="来源单据类型")
    source_no: Mapped[str | None] = mapped_column(String(64), comment="来源单号")


class ArApRecord(TenantMixin, TimestampMixin, UserMixin, Base):
    """应收应付记录表：fin_ar_ap。

    与采购单/销售单/收款单/付款单关联，记录应收/应付金额及对账状态。
    """

    __tablename__ = "fin_ar_ap"
    __table_args__ = (
        Index("ix_fin_ar_ap_partner", "partner_type", "partner_id"),
        Index("ix_fin_ar_ap_status", "status"),
        Index("uq_fin_ar_ap_source", "source_type", "source_no", "tenant_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 业务方类型：1=供应商（应付），2=客户（应收）
    partner_type: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="业务方类型：1=供应商，2=客户"
    )
    partner_id: Mapped[int] = mapped_column(
        BIGINT_PK, nullable=False, comment="业务方 ID（供应商/客户主键）"
    )
    # 记录类型：1=应付，2=应收
    record_type: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="记录类型：1=应付，2=应收"
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="来源单据类型，如 purchase_in、sale_out"
    )
    source_no: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="来源单号"
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, comment="金额"
    )
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", nullable=False, comment="币种"
    )
    # 状态：1=未对账，2=部分对账，3=已对账，4=作废
    status: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="对账状态：1=未对账，2=部分对账，3=已对账，4=作废"
    )
    reconciled_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False, comment="已对账金额"
    )
    biz_date: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="业务日期，格式 YYYYMMDD"
    )
    due_date: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="到期日期，格式 YYYYMMDD"
    )
    remark: Mapped[str | None] = mapped_column(String(255), comment="备注")
