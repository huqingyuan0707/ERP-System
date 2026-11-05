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
