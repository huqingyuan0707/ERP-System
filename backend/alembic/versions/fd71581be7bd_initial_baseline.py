"""Initial application schema.

Revision ID: fd71581be7bd
Revises:
Create Date: 2026-10-29 22:58:53.194348

The original baseline was empty because the application created tables through
``Base.metadata.create_all`` during startup.  That made a direct
``alembic upgrade head`` against an empty database impossible: the next
migration attempted to alter ``sys_user`` before it existed.  Keep the
baseline self-contained so Alembic is also a valid bootstrap path.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "fd71581be7bd"
down_revision: str | None = None
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


def _audited_columns() -> list[sa.Column]:
    return [
        *_timestamps(),
        sa.Column("created_by", _BIGINT_PK),
        sa.Column("updated_by", _BIGINT_PK),
        # Server defaults are introduced by c7e1a9b4d2f3, matching the
        # historical schema at this revision.
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    ]


def _metadata() -> sa.MetaData:
    """Return the frozen schema that existed before follow-up migrations."""
    metadata = sa.MetaData()

    sys_dept = sa.Table(
        "sys_dept",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("parent_id", _BIGINT_PK, sa.ForeignKey("sys_dept.id")),
        sa.Column("leader", sa.String(64)),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )

    sys_role = sa.Table(
        "sys_role",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("data_scope", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_sys_role_code", sys_role.c.code, unique=True)

    sys_permission = sa.Table(
        "sys_permission",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("parent_id", _BIGINT_PK, sa.ForeignKey("sys_permission.id")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("perm_code", sa.String(128)),
        sa.Column("route_path", sa.String(255)),
        sa.Column("component", sa.String(255)),
        sa.Column("icon", sa.String(64)),
        sa.Column("sort", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
    )
    sa.Index("ix_sys_permission_perm_code", sys_permission.c.perm_code)

    sys_oper_log = sa.Table(
        "sys_oper_log",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("user_id", _BIGINT_PK),
        sa.Column("module", sa.String(64)),
        sa.Column("action", sa.String(64)),
        sa.Column("method", sa.String(16)),
        sa.Column("url", sa.String(255)),
        sa.Column("ip", sa.String(64)),
        sa.Column("params", sa.Text()),
        sa.Column("result", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("status", sa.Integer(), nullable=False),
        *_timestamps(),
    )
    sa.Index("ix_sys_oper_log_user_id", sys_oper_log.c.user_id)

    sys_login_log = sa.Table(
        "sys_login_log",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(255)),
        *_timestamps(),
    )
    sa.Index("ix_sys_login_log_username", sys_login_log.c.username)

    sys_revoked_token = sa.Table(
        "sys_revoked_token",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    sa.Index("ix_sys_revoked_token_jti", sys_revoked_token.c.jti, unique=True)

    bd_goods_category = sa.Table(
        "bd_goods_category",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column(
            "parent_id", _BIGINT_PK, sa.ForeignKey("bd_goods_category.id")
        ),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )

    bd_unit = sa.Table(
        "bd_unit",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("precision", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )

    bd_supplier = sa.Table(
        "bd_supplier",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("contact", sa.String(64)),
        sa.Column("phone", sa.String(32)),
        sa.Column("address", sa.String(255)),
        sa.Column("tax_no", sa.String(64)),
        sa.Column("bank", sa.String(128)),
        sa.Column("account", sa.String(64)),
        sa.Column("credit_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_bd_supplier_code", bd_supplier.c.code, unique=True)

    bd_customer = sa.Table(
        "bd_customer",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("contact", sa.String(64)),
        sa.Column("phone", sa.String(32)),
        sa.Column("address", sa.String(255)),
        sa.Column("tax_no", sa.String(64)),
        sa.Column("bank", sa.String(128)),
        sa.Column("account", sa.String(64)),
        sa.Column("credit_limit", sa.Numeric(18, 2), nullable=False),
        sa.Column("credit_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_bd_customer_code", bd_customer.c.code, unique=True)

    sys_user = sa.Table(
        "sys_user",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("real_name", sa.String(64)),
        sa.Column("mobile", sa.String(32)),
        sa.Column("email", sa.String(128)),
        sa.Column("dept_id", _BIGINT_PK, sa.ForeignKey("sys_dept.id")),
        sa.Column("position", sa.String(64)),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_ip", sa.String(64)),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_sys_user_username", sys_user.c.username, unique=True)

    sa.Table(
        "sys_role_permission",
        metadata,
        sa.Column(
            "role_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_role.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_permission.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    bd_goods = sa.Table(
        "bd_goods",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "category_id", _BIGINT_PK, sa.ForeignKey("bd_goods_category.id")
        ),
        sa.Column("spec", sa.String(128)),
        sa.Column("unit_id", _BIGINT_PK, sa.ForeignKey("bd_unit.id")),
        sa.Column("barcode", sa.String(64)),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("sale_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("stock_lower", sa.Numeric(18, 4), nullable=False),
        sa.Column("stock_upper", sa.Numeric(18, 4), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_bd_goods_name", bd_goods.c.name)
    sa.Index("ix_bd_goods_code", bd_goods.c.code, unique=True)

    sa.Table(
        "sys_user_role",
        metadata,
        sa.Column(
            "user_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_user.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_role.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    bd_warehouse = sa.Table(
        "bd_warehouse",
        metadata,
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("manager_id", _BIGINT_PK, sa.ForeignKey("sys_user.id")),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("address", sa.String(255)),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("remark", sa.String(255)),
        *_audited_columns(),
    )
    sa.Index("ix_bd_warehouse_code", bd_warehouse.c.code, unique=True)

    return metadata


def upgrade() -> None:
    _metadata().create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _metadata().drop_all(bind=op.get_bind(), checkfirst=True)
