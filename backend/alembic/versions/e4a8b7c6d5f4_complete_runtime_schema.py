"""Create tables that previously relied on application ``create_all``.

Revision ID: e4a8b7c6d5f4
Revises: 96df38455139
Create Date: 2026-11-01 14:00:00.000000

``init_db`` originally created these runtime tables directly from ORM metadata.
That works when the service starts first, but it leaves an empty database
incomplete when CI or deployment invokes Alembic by itself.  This migration
owns the missing tables and the tenant lookup indexes for the older tables.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4a8b7c6d5f4"
down_revision: str | None = "96df38455139"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _user_columns() -> list[sa.Column]:
    return [
        *_timestamps(),
        sa.Column("created_by", _BIGINT_PK),
        sa.Column("updated_by", _BIGINT_PK),
    ]


def _tenant_column(*, server_default: bool = True) -> sa.Column:
    kwargs: dict[str, object] = {"nullable": False, "comment": "租户 ID"}
    if server_default:
        kwargs["server_default"] = "1"
    return sa.Column("tenant_id", _BIGINT_PK, **kwargs)


def _metadata() -> sa.MetaData:
    """Define the formerly unmanaged ORM tables as a frozen schema."""
    metadata = sa.MetaData()

    # The initial migration owns these base tables.  Register their primary
    # keys here solely so SQLAlchemy can resolve the inventory foreign keys;
    # ``create_all(checkfirst=True)`` sees them as already present.
    sa.Table(
        "bd_warehouse",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True),
    )
    sa.Table(
        "bd_goods",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True),
    )

    sys_task = sa.Table(
        "sys_task",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("params", sa.Text()),
        sa.Column("file_path", sa.String(512)),
        sa.Column("error_msg", sa.Text()),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("created_by", _BIGINT_PK),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    sa.Index("ix_sys_task_created_by", sys_task.c.created_by)
    sa.Index("ix_sys_task_task_id", sys_task.c.task_id, unique=True)

    inv_stock = sa.Table(
        "inv_stock",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        _tenant_column(server_default=False),
        sa.Column(
            "warehouse_id", _BIGINT_PK, sa.ForeignKey("bd_warehouse.id"), nullable=False
        ),
        sa.Column("goods_id", _BIGINT_PK, sa.ForeignKey("bd_goods.id"), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("locked_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("available_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("avg_cost", sa.Numeric(18, 4), nullable=False),
        *_user_columns(),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    sa.Index("ix_inv_stock_tenant_id", inv_stock.c.tenant_id)
    sa.Index("ix_inv_stock_warehouse_id", inv_stock.c.warehouse_id)
    sa.Index("ix_inv_stock_goods_id", inv_stock.c.goods_id)
    sa.Index(
        "uq_inv_stock_warehouse_goods_tenant",
        inv_stock.c.warehouse_id,
        inv_stock.c.goods_id,
        inv_stock.c.tenant_id,
        unique=True,
        sqlite_where=sa.text("is_deleted = 0"),
        postgresql_where=sa.text("is_deleted = false"),
    )

    inv_stock_flow = sa.Table(
        "inv_stock_flow",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        _tenant_column(server_default=False),
        sa.Column(
            "warehouse_id", _BIGINT_PK, sa.ForeignKey("bd_warehouse.id"), nullable=False
        ),
        sa.Column("goods_id", _BIGINT_PK, sa.ForeignKey("bd_goods.id"), nullable=False),
        sa.Column("flow_type", sa.String(32), nullable=False),
        sa.Column("direction", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("before_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("after_qty", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=False),
        sa.Column("source_type", sa.String(32)),
        sa.Column("source_no", sa.String(64)),
        sa.Column("biz_time", sa.String(32), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_timestamps(),
    )
    sa.Index("ix_inv_stock_flow_tenant_id", inv_stock_flow.c.tenant_id)
    sa.Index("ix_inv_stock_flow_warehouse_id", inv_stock_flow.c.warehouse_id)
    sa.Index("ix_inv_stock_flow_goods_id", inv_stock_flow.c.goods_id)
    sa.Index("ix_inv_stock_flow_source_no", inv_stock_flow.c.source_no)
    sa.Index("ix_inv_stock_flow_biz_time", inv_stock_flow.c.biz_time)

    fin_account = sa.Table(
        "fin_account",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False, comment="科目编码，如 1001、1102"),
        sa.Column("name", sa.String(128), nullable=False, comment="科目名称，如 库存商品、应付账款"),
        sa.Column(
            "parent_id", _BIGINT_PK, sa.ForeignKey("fin_account.id"), comment="父科目 ID"
        ),
        sa.Column("type", sa.Integer(), nullable=False, comment="科目类型"),
        sa.Column("direction", sa.Integer(), nullable=False, comment="借贷方向：1=借方增加，2=贷方增加"),
        sa.Column("balance", sa.Numeric(18, 4), nullable=False, comment="科目余额"),
        sa.Column("is_leaf", sa.Boolean(), nullable=False, comment="是否末级科目，只有末级科目可记账"),
        sa.Column("status", sa.Integer(), nullable=False, comment="状态：1=启用，0=禁用"),
        sa.Column("remark", sa.String(255), comment="备注"),
        _tenant_column(),
        *_user_columns(),
    )
    sa.Index("uq_fin_account_code_tenant", fin_account.c.code, fin_account.c.tenant_id, unique=True)
    sa.Index("ix_fin_account_parent", fin_account.c.parent_id)
    sa.Index("ix_fin_account_tenant_id", fin_account.c.tenant_id)

    fin_voucher = sa.Table(
        "fin_voucher",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("voucher_no", sa.String(64), nullable=False, comment="凭证号，如 FV202611010001"),
        sa.Column("voucher_date", sa.String(8), nullable=False, comment="凭证日期，格式 YYYYMMDD"),
        sa.Column("summary", sa.String(255), comment="凭证摘要"),
        sa.Column(
            "status",
            sa.Integer(),
            nullable=False,
            comment="凭证状态：1=待审，2=已审，3=已记账，4=作废",
        ),
        sa.Column("total_debit", sa.Numeric(18, 4), nullable=False, comment="凭证总借方金额"),
        sa.Column("total_credit", sa.Numeric(18, 4), nullable=False, comment="凭证总贷方金额"),
        sa.Column("source_type", sa.String(32), comment="来源单据类型，如 purchase_in、sale_out"),
        sa.Column("source_no", sa.String(64), comment="来源单号"),
        sa.Column("remark", sa.String(255), comment="备注"),
        _tenant_column(),
        *_user_columns(),
    )
    sa.Index("ix_fin_voucher_no", fin_voucher.c.voucher_no)
    sa.Index("ix_fin_voucher_tenant_date", fin_voucher.c.tenant_id, fin_voucher.c.voucher_date)
    sa.Index("ix_fin_voucher_tenant_id", fin_voucher.c.tenant_id)

    fin_entry = sa.Table(
        "fin_entry",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column(
            "voucher_id",
            _BIGINT_PK,
            sa.ForeignKey("fin_voucher.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属凭证 ID",
        ),
        sa.Column(
            "account_id",
            _BIGINT_PK,
            sa.ForeignKey("fin_account.id"),
            nullable=False,
            comment="科目 ID",
        ),
        sa.Column("debit_amount", sa.Numeric(18, 4), nullable=False, comment="借方发生额"),
        sa.Column("credit_amount", sa.Numeric(18, 4), nullable=False, comment="贷方发生额"),
        sa.Column("summary", sa.String(255), comment="分录摘要"),
        sa.Column("source_type", sa.String(32), comment="来源单据类型"),
        sa.Column("source_no", sa.String(64), comment="来源单号"),
        _tenant_column(),
        *_user_columns(),
    )
    sa.Index("ix_fin_entry_voucher", fin_entry.c.voucher_id)
    sa.Index("ix_fin_entry_account", fin_entry.c.account_id)
    sa.Index("ix_fin_entry_tenant_id", fin_entry.c.tenant_id)

    fin_ar_ap = sa.Table(
        "fin_ar_ap",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("partner_type", sa.Integer(), nullable=False, comment="业务方类型：1=供应商，2=客户"),
        sa.Column("partner_id", _BIGINT_PK, nullable=False, comment="业务方 ID（供应商/客户主键）"),
        sa.Column("record_type", sa.Integer(), nullable=False, comment="记录类型：1=应付，2=应收"),
        sa.Column(
            "source_type",
            sa.String(32),
            nullable=False,
            comment="来源单据类型，如 purchase_in、sale_out",
        ),
        sa.Column("source_no", sa.String(64), nullable=False, comment="来源单号"),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False, comment="金额"),
        sa.Column("currency", sa.String(8), nullable=False, comment="币种"),
        sa.Column(
            "status",
            sa.Integer(),
            nullable=False,
            comment="对账状态：1=未对账，2=部分对账，3=已对账，4=作废",
        ),
        sa.Column("reconciled_amount", sa.Numeric(18, 4), nullable=False, comment="已对账金额"),
        sa.Column("biz_date", sa.String(8), nullable=False, comment="业务日期，格式 YYYYMMDD"),
        sa.Column("due_date", sa.String(8), comment="到期日期，格式 YYYYMMDD"),
        sa.Column("remark", sa.String(255), comment="备注"),
        _tenant_column(),
        *_user_columns(),
    )
    sa.Index("ix_fin_ar_ap_partner", fin_ar_ap.c.partner_type, fin_ar_ap.c.partner_id)
    sa.Index("ix_fin_ar_ap_status", fin_ar_ap.c.status)
    sa.Index(
        "uq_fin_ar_ap_source",
        fin_ar_ap.c.source_type,
        fin_ar_ap.c.source_no,
        fin_ar_ap.c.tenant_id,
        unique=True,
    )
    sa.Index("ix_fin_ar_ap_tenant_id", fin_ar_ap.c.tenant_id)

    idempotency_key = sa.Table(
        "idem_idempotency_key",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(255), nullable=False, comment="幂等键，全局唯一"),
        sa.Column("tenant_id", _BIGINT_PK, nullable=False, comment="租户 ID"),
        sa.Column("user_id", _BIGINT_PK, nullable=False, comment="操作人 ID"),
        sa.Column(
            "biz_type",
            sa.String(64),
            nullable=False,
            comment="业务类型，如 change_stock、lock_stock",
        ),
        sa.Column("biz_no", sa.String(128), comment="业务单号，与幂等键共同确定幂等语义"),
        sa.Column("request_body_hash", sa.String(64), comment="请求体 SHA-256 前16位，用于参数一致性校验"),
        sa.Column("response_body", sa.Text(), comment="首次成功响应体 JSON，重复请求时返回"),
        sa.Column("status", sa.Integer(), nullable=False, comment="处理状态：1=成功，2=处理中，3=失败"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="幂等键过期时间，过期后可清理",
        ),
    )
    sa.Index("uq_idempotency_key", idempotency_key.c.key, unique=True)
    sa.Index("ix_idempotency_tenant", idempotency_key.c.tenant_id)
    sa.Index("ix_idempotency_expires", idempotency_key.c.expires_at)
    sa.Index("ix_idem_idempotency_key_tenant_id", idempotency_key.c.tenant_id)
    sa.Index("ix_idem_idempotency_key_user_id", idempotency_key.c.user_id)

    return metadata


_BASE_TENANT_INDEXES: tuple[tuple[str, str], ...] = (
    ("sys_user", "ix_sys_user_tenant_id"),
    ("sys_dept", "ix_sys_dept_tenant_id"),
    ("sys_role", "ix_sys_role_tenant_id"),
    ("bd_warehouse", "ix_bd_warehouse_tenant_id"),
    ("bd_goods", "ix_bd_goods_tenant_id"),
    ("bd_goods_category", "ix_bd_goods_category_tenant_id"),
    ("bd_unit", "ix_bd_unit_tenant_id"),
    ("bd_supplier", "ix_bd_supplier_tenant_id"),
    ("bd_customer", "ix_bd_customer_tenant_id"),
)

_LEGACY_UNIQUE_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("sys_user", "ix_sys_user_username", "username"),
    ("sys_role", "ix_sys_role_code", "code"),
    ("bd_warehouse", "ix_bd_warehouse_code", "code"),
    ("bd_goods", "ix_bd_goods_code", "code"),
    ("bd_supplier", "ix_bd_supplier_code", "code"),
    ("bd_customer", "ix_bd_customer_code", "code"),
)


def _index_exists(conn: sa.Connection, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(conn)
    return inspector.has_table(table_name) and index_name in {
        index["name"] for index in inspector.get_indexes(table_name)
    }


def _index_is_unique(conn: sa.Connection, table_name: str, index_name: str) -> bool:
    for index in sa.inspect(conn).get_indexes(table_name):
        if index["name"] == index_name:
            return bool(index.get("unique"))
    return False


def upgrade() -> None:
    _metadata().create_all(bind=op.get_bind(), checkfirst=True)

    conn = op.get_bind()
    for table_name, index_name in _BASE_TENANT_INDEXES:
        if not _index_exists(conn, table_name, index_name):
            op.create_index(index_name, table_name, ["tenant_id"], unique=False)

        # TenantMixin declares a column comment; the earlier add-column
        # migration predates it, so backfill the metadata-visible comment.
        op.alter_column(
            table_name,
            "tenant_id",
            existing_type=_BIGINT_PK,
            comment="租户 ID",
        )

    # The original per-table unique indexes conflict with tenant-scoped
    # uniqueness.  Keep them as normal lookup indexes; the composite unique
    # indexes from 96df38455139 enforce the actual constraint.
    for table_name, index_name, column_name in _LEGACY_UNIQUE_INDEXES:
        if _index_exists(conn, table_name, index_name) and _index_is_unique(
            conn, table_name, index_name
        ):
            op.drop_index(index_name, table_name=table_name)
            op.create_index(index_name, table_name, [column_name], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    for table_name, index_name, column_name in reversed(_LEGACY_UNIQUE_INDEXES):
        if _index_exists(conn, table_name, index_name) and not _index_is_unique(
            conn, table_name, index_name
        ):
            op.drop_index(index_name, table_name=table_name)
            op.create_index(index_name, table_name, [column_name], unique=True)

    for table_name, index_name in reversed(_BASE_TENANT_INDEXES):
        if _index_exists(conn, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)
        op.alter_column(
            table_name,
            "tenant_id",
            existing_type=_BIGINT_PK,
            comment=None,
        )

    metadata = _metadata()
    # Do not drop the two base-table references registered in ``_metadata``.
    for table_name in (
        "fin_entry",
        "fin_voucher",
        "fin_account",
        "inv_stock_flow",
        "inv_stock",
        "fin_ar_ap",
        "idem_idempotency_key",
        "sys_task",
    ):
        metadata.tables[table_name].drop(bind=conn, checkfirst=True)
