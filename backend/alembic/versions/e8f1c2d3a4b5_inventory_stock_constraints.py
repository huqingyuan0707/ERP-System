"""Enforce inventory stock invariants without rewriting historical rows.

Revision ID: e8f1c2d3a4b5
Revises: d4e9a1b7c3f5

The preflight scans every inventory row, including soft-deleted rows. Any
violation blocks the migration by row ID before DDL is applied; no quantities
or costs are repaired or otherwise changed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e8f1c2d3a4b5"
down_revision: str | None = "d4e9a1b7c3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("ck_inv_stock_qty_nonnegative", "qty >= 0"),
    ("ck_inv_stock_locked_qty_nonnegative", "locked_qty >= 0"),
    ("ck_inv_stock_available_qty_nonnegative", "available_qty >= 0"),
    (
        "ck_inv_stock_available_qty_consistent",
        "available_qty = qty - locked_qty",
    ),
    ("ck_inv_stock_qty_ge_locked", "qty >= locked_qty"),
    ("ck_inv_stock_avg_cost_nonnegative", "avg_cost >= 0"),
)


def _invalid_ids(conn: sa.Connection) -> list[int]:
    stock = sa.table(
        "inv_stock",
        sa.column("id", sa.BigInteger()),
        sa.column("qty", sa.Numeric(18, 4)),
        sa.column("locked_qty", sa.Numeric(18, 4)),
        sa.column("available_qty", sa.Numeric(18, 4)),
        sa.column("avg_cost", sa.Numeric(18, 4)),
    )
    invalid = sa.or_(
        stock.c.qty < 0,
        stock.c.locked_qty < 0,
        stock.c.available_qty < 0,
        stock.c.available_qty != stock.c.qty - stock.c.locked_qty,
        stock.c.qty < stock.c.locked_qty,
        stock.c.avg_cost < 0,
    )
    return list(conn.execute(sa.select(stock.c.id).where(invalid).order_by(stock.c.id)).scalars())


def _preflight(conn: sa.Connection) -> None:
    """执行前的数据校验，确保没有违反约束的数据。"""
    ids = _invalid_ids(conn)
    if ids:
        raise RuntimeError(
            "Migration blocked; invalid inv_stock invariants: "
            f"ids={ids}. \n"
            "请手动修复以下问题后再执行迁移：\n"
            "1. qty >= 0: 库存数量不能为负 - 检查是否存在负库存数据\n"
            "2. locked_qty >= 0: 锁定数量不能为负 - 检查是否有异常锁定\n"
            "3. available_qty >= 0: 可用数量不能为负 - 应该由qty和locked_qty计算得出\n"
            "4. available_qty = qty - locked_qty: 可用数量必须等于账面数量减去锁定数量\n"
            "5. qty >= locked_qty: 账面数量不能小于锁定数量\n"
            "6. avg_cost >= 0: 平均成本不能为负 - 检查是否有异常成本数据\n"
            "建议修复步骤：\n"
            "- 首先查询详细的错误数据：SELECT id, qty, locked_qty, available_qty, avg_cost FROM inv_stock WHERE id IN (上述id列表)\n"
            "- 核对inv_stock_flow流水与销售出库草稿，确认锁定来源\n"
            "- 禁止直接UPDATE覆盖locked_qty，应通过业务单据修正\n"
            "- 修正后再执行迁移"
        )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        conn.execute(sa.text("LOCK TABLE inv_stock IN SHARE ROW EXCLUSIVE MODE"))
    _preflight(conn)
    with op.batch_alter_table("inv_stock") as batch_op:
        for name, condition in _CONSTRAINTS:
            batch_op.create_check_constraint(name, condition)


def downgrade() -> None:
    with op.batch_alter_table("inv_stock") as batch_op:
        for name, _condition in reversed(_CONSTRAINTS):
            batch_op.drop_constraint(name, type_="check")
