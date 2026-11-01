"""add tenant_id to base tables

Revision ID: 96df38455139
Revises: c7e1a9b4d2f3
Create Date: 2026-11-01 12:00:00.000000

B-03 多租户隔离：为核心业务表添加 tenant_id 列与联合唯一索引，实现行级数据隔离。
涉及表：sys_user、sys_dept、sys_role、bd_warehouse、bd_goods、bd_goods_category、
bd_unit、bd_supplier、bd_customer、inv_stock、inv_stock_flow。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# revision id 必须 ≤ 32 字符：alembic 的 alembic_version.version_num 列是
# VARCHAR(32)，用 33 字符的长名字会让 stamp/upgrade 直接报
# "value too long for type character varying(32)"。本仓统一用 12 位哈希。
revision: str = "96df38455139"
# 父版本必须是当前唯一 head（c7e1a9b4d2f3）。
# 若写成 a5c9d3e7f1b2（历史分叉点）会与 c7e1a9b4d2f3 并列成两个 head，
# 导致 `alembic upgrade head` 直接报 "Multiple head revisions are present" 而失败。
down_revision: str | None = "c7e1a9b4d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def _table_has_column(conn, table: str, column: str) -> bool:
    inspector = sa.inspect(conn)
    cols = {c["name"] for c in inspector.get_columns(table)}
    return column in cols


def _index_exists(conn, table: str, name: str) -> bool:
    inspector = sa.inspect(conn)
    return name in {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    conn = op.get_bind()

    # ---- sys_user ----
    if not _table_has_column(conn, "sys_user", "tenant_id"):
        op.add_column(
            "sys_user",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "sys_user", "uq_user_username_tenant"):
        op.create_index(
            "uq_user_username_tenant", "sys_user", ["username", "tenant_id"], unique=True
        )

    # ---- sys_dept ----
    if not _table_has_column(conn, "sys_dept", "tenant_id"):
        op.add_column(
            "sys_dept",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "sys_dept", "uq_dept_name_tenant"):
        op.create_index(
            "uq_dept_name_tenant", "sys_dept", ["name", "tenant_id"], unique=True
        )

    # ---- sys_role ----
    if not _table_has_column(conn, "sys_role", "tenant_id"):
        op.add_column(
            "sys_role",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "sys_role", "uq_role_code_tenant"):
        op.create_index(
            "uq_role_code_tenant", "sys_role", ["code", "tenant_id"], unique=True
        )

    # ---- bd_warehouse ----
    if not _table_has_column(conn, "bd_warehouse", "tenant_id"):
        op.add_column(
            "bd_warehouse",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_warehouse", "uq_warehouse_code_tenant"):
        op.create_index(
            "uq_warehouse_code_tenant",
            "bd_warehouse",
            ["code", "tenant_id"],
            unique=True,
        )

    # ---- bd_goods ----
    if not _table_has_column(conn, "bd_goods", "tenant_id"):
        op.add_column(
            "bd_goods",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_goods", "uq_goods_code_tenant"):
        op.create_index(
            "uq_goods_code_tenant", "bd_goods", ["code", "tenant_id"], unique=True
        )

    # ---- bd_goods_category ----
    if not _table_has_column(conn, "bd_goods_category", "tenant_id"):
        op.add_column(
            "bd_goods_category",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_goods_category", "uq_goods_category_name_tenant"):
        op.create_index(
            "uq_goods_category_name_tenant",
            "bd_goods_category",
            ["name", "parent_id", "tenant_id"],
            unique=True,
        )

    # ---- bd_unit ----
    if not _table_has_column(conn, "bd_unit", "tenant_id"):
        op.add_column(
            "bd_unit",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_unit", "uq_unit_name_tenant"):
        op.create_index(
            "uq_unit_name_tenant", "bd_unit", ["name", "tenant_id"], unique=True
        )

    # ---- bd_supplier ----
    if not _table_has_column(conn, "bd_supplier", "tenant_id"):
        op.add_column(
            "bd_supplier",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_supplier", "uq_supplier_code_tenant"):
        op.create_index(
            "uq_supplier_code_tenant", "bd_supplier", ["code", "tenant_id"], unique=True
        )

    # ---- bd_customer ----
    if not _table_has_column(conn, "bd_customer", "tenant_id"):
        op.add_column(
            "bd_customer",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "bd_customer", "uq_customer_code_tenant"):
        op.create_index(
            "uq_customer_code_tenant", "bd_customer", ["code", "tenant_id"], unique=True
        )

    # ---- inv_stock ----
    if not _table_has_column(conn, "inv_stock", "tenant_id"):
        op.add_column(
            "inv_stock",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )
    if not _index_exists(conn, "inv_stock", "uq_inv_stock_warehouse_goods_tenant"):
        op.create_index(
            "uq_inv_stock_warehouse_goods_tenant",
            "inv_stock",
            ["warehouse_id", "goods_id", "tenant_id"],
            unique=True,
            sqlite_where=sa.text("is_deleted = 0"),
            postgresql_where=sa.text("is_deleted = false"),
        )

    # ---- inv_stock_flow ----
    if not _table_has_column(conn, "inv_stock_flow", "tenant_id"):
        op.add_column(
            "inv_stock_flow",
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
        )


def downgrade() -> None:
    conn = op.get_bind()

    # 删除索引
    for name, table in (
        ("uq_inv_stock_warehouse_goods_tenant", "inv_stock"),
        ("uq_customer_code_tenant", "bd_customer"),
        ("uq_supplier_code_tenant", "bd_supplier"),
        ("uq_unit_name_tenant", "bd_unit"),
        ("uq_goods_category_name_tenant", "bd_goods_category"),
        ("uq_goods_code_tenant", "bd_goods"),
        ("uq_warehouse_code_tenant", "bd_warehouse"),
        ("uq_role_code_tenant", "sys_role"),
        ("uq_dept_name_tenant", "sys_dept"),
        ("uq_user_username_tenant", "sys_user"),
    ):
        if _index_exists(conn, table, name):
            op.drop_index(name, table_name=table)

    # 删除列
    for table in (
        "inv_stock_flow",
        "inv_stock",
        "bd_customer",
        "bd_supplier",
        "bd_unit",
        "bd_goods_category",
        "bd_goods",
        "bd_warehouse",
        "sys_role",
        "sys_dept",
        "sys_user",
    ):
        if _table_has_column(conn, table, "tenant_id"):
            op.drop_column(table, "tenant_id")
