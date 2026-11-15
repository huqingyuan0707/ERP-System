"""库存追溯列补齐：inv_stock 首入/末出日期与 inv_stock_flow 操作人

Revision ID: a7b3d9e1f5c4
Revises: d1a4f8c2b6e9
Create Date: 2026-11-07 10:00:00.000000

变更内容（模型早已定义、但历史上从未落过迁移，PostgreSQL 库缺列会导致
  采购入库/退货确认等一切走 inv_stock 的写路径 500 —— 本迁移补齐缺口）：
  1. inv_stock 新增 first_in_date（DATE 可空）：首次入库日期，呆滞预警计算基准；
  2. inv_stock 新增 last_out_date（DATE 可空）：最近出库日期，呆滞预警计算基准；
  3. inv_stock_flow 新增 operator_id（BIGINT 可空）：流水操作人，只写不更新。

三列均可空，历史行为 NULL，不影响既有数据。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b3d9e1f5c4"
down_revision: str | None = "d1a4f8c2b6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(conn: sa.Connection, table: str, column: str) -> bool:
    """判断表上是否已有该列。

    迁移链上 7f496af09f7f 已经把同样三列加过一遍，干净库升级到这里时列已存在，
    无条件 add_column 会抛 DuplicateColumnError，因此列操作必须幂等。
    """
    inspector = sa.inspect(conn)
    if not inspector.has_table(table):
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    """按模型口径补齐三列（全部可空，无需回填）；已存在则跳过。"""
    conn = op.get_bind()
    if not _column_exists(conn, "inv_stock", "first_in_date"):
        op.add_column("inv_stock", sa.Column("first_in_date", sa.Date(), nullable=True))
    if not _column_exists(conn, "inv_stock", "last_out_date"):
        op.add_column("inv_stock", sa.Column("last_out_date", sa.Date(), nullable=True))
    if not _column_exists(conn, "inv_stock_flow", "operator_id"):
        op.add_column("inv_stock_flow", sa.Column("operator_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """回滚：删除三列（历史数据一并丢弃，仅开发环境回滚用）；不存在则跳过。"""
    conn = op.get_bind()
    if _column_exists(conn, "inv_stock_flow", "operator_id"):
        op.drop_column("inv_stock_flow", "operator_id")
    if _column_exists(conn, "inv_stock", "last_out_date"):
        op.drop_column("inv_stock", "last_out_date")
    if _column_exists(conn, "inv_stock", "first_in_date"):
        op.drop_column("inv_stock", "first_in_date")
