"""Enforce base-data uniqueness and isolate future audit/task records.

Revision ID: f2b6c8d0e1a3
Revises: e4a8b7c6d5f4

Preflight is read-only: duplicates and non-normalized keys block the migration;
legacy cross-tenant references and invalid numbers are reported by row ID only.
No historical strings, ownership or amounts are rewritten. Unknown audit/task
ownership remains NULL; even a matching user ID is not evidence of its tenant.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op


revision = "f2b6c8d0e1a3"
down_revision = "e4a8b7c6d5f4"
branch_labels = None
depends_on = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_BARCODE_WHERE = "is_deleted IS FALSE AND barcode IS NOT NULL AND barcode <> ''"
_ROOT_WHERE = "parent_id IS NULL"
_TENANT_TABLES = ("sys_oper_log", "sys_task")
logger = logging.getLogger("alembic.runtime.migration")

# Frozen relationship list: never import evolving application models in a migration.
_RELATIONS = (
    ("bd_goods_category", "parent_id", "bd_goods_category"),
    ("bd_goods", "category_id", "bd_goods_category"),
    ("bd_goods", "unit_id", "bd_unit"),
    ("bd_warehouse", "manager_id", "sys_user"),
    ("sys_user", "dept_id", "sys_dept"),
    ("sys_dept", "parent_id", "sys_dept"),
    ("inv_stock", "goods_id", "bd_goods"),
    ("inv_stock", "warehouse_id", "bd_warehouse"),
    ("inv_stock_flow", "goods_id", "bd_goods"),
    ("inv_stock_flow", "warehouse_id", "bd_warehouse"),
    ("fin_account", "parent_id", "fin_account"),
    ("fin_entry", "voucher_id", "fin_voucher"),
    ("fin_entry", "account_id", "fin_account"),
)


def _duplicate_ids(conn, table, column, predicate, *, optional=False) -> list[int]:
    groups = defaultdict(list)
    stmt = sa.select(table.c.id, table.c.tenant_id, table.c[column]).where(predicate)
    for row_id, tenant_id, value in conn.execute(stmt):
        if optional and (value is None or value == ""):
            continue
        normalized = value.strip() if isinstance(value, str) else value
        # Whitespace-only barcodes normalize to absent. Still catch exact raw
        # duplicates: the new raw-value index must not fail with opaque SQL errors.
        key = ("normalized", normalized) if normalized or not optional else ("raw", value)
        groups[(tenant_id, key)].append(row_id)
    return sorted(row_id for ids in groups.values() if len(ids) > 1 for row_id in ids)


def _non_normalized_ids(conn, table, column, predicate) -> list[int]:
    # Even a single padded key would bypass the raw-value unique index after
    # the API starts stripping new inputs. Require manual review, never UPDATE.
    stmt = sa.select(table.c.id, table.c[column]).where(predicate)
    return sorted(
        row_id
        for row_id, value in conn.execute(stmt)
        if isinstance(value, str) and value != value.strip()
    )


def _report_references(conn, tables) -> None:
    for source, column, target in _RELATIONS:
        if source not in tables or target not in tables or column not in tables[source].c:
            continue
        child, parent = tables[source], tables[target].alias()
        if "tenant_id" not in child.c or "tenant_id" not in parent.c:
            continue
        stmt = (
            sa.select(child.c.id, parent.c.id)
            .select_from(child.join(parent, child.c[column] == parent.c.id))
            .where(child.c.tenant_id != parent.c.tenant_id)
            .order_by(child.c.id)
        )
        ids = [tuple(row) for row in conn.execute(stmt)]
        if ids:
            logger.warning(
                "Legacy cross-tenant reference %s.%s -> %s; ids=%s; unchanged",
                source,
                column,
                target,
                ids,
            )

    for link, left_column, left_name, right_column, right_name in (
        ("sys_user_role", "user_id", "sys_user", "role_id", "sys_role"),
        ("sys_role_dept", "role_id", "sys_role", "dept_id", "sys_dept"),
    ):
        if not all(name in tables for name in (link, left_name, right_name)):
            continue
        bridge, left, right = tables[link], tables[left_name], tables[right_name]
        stmt = (
            sa.select(bridge.c[left_column], bridge.c[right_column])
            .select_from(
                bridge.join(left, bridge.c[left_column] == left.c.id).join(
                    right, bridge.c[right_column] == right.c.id
                )
            )
            .where(left.c.tenant_id != right.c.tenant_id)
        )
        ids = [tuple(row) for row in conn.execute(stmt)]
        if ids:
            logger.warning("Legacy cross-tenant reference %s; ids=%s; unchanged", link, ids)


def _invalid_number(value, scale: int, integer_digits: int) -> bool:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return True
    if not number.is_finite() or number < 0:
        return True
    if number == 0:
        return False
    _, digits, exponent = number.as_tuple()
    # Count insignificant zeroes without Decimal.normalize/quantize, which can
    # silently round to the active decimal context precision.
    trailing_zeros = 0
    for digit in reversed(digits):
        if digit:
            break
        trailing_zeros += 1
    return exponent + trailing_zeros < -scale or number.adjusted() >= integer_digits


def _report_numbers(conn, tables) -> None:
    specs = {
        "bd_goods": {
            "cost_price": (2, 16),
            "sale_price": (2, 16),
            "stock_lower": (4, 14),
            "stock_upper": (4, 14),
        },
        "bd_customer": {"credit_limit": (2, 16)},
    }
    unit_precisions = {}
    if "bd_unit" in tables and "precision" in tables["bd_unit"].c:
        units = tables["bd_unit"]
        invalid = []
        for row_id, tenant_id, precision in conn.execute(
            sa.select(units.c.id, units.c.tenant_id, units.c.precision)
        ):
            if not isinstance(precision, int) or not 0 <= precision <= 4:
                invalid.append(row_id)
            else:
                unit_precisions[(tenant_id, row_id)] = precision
        if invalid:
            logger.warning("Legacy invalid bd_unit.precision; ids=%s; unchanged", sorted(invalid))
    for name, columns in specs.items():
        if name not in tables:
            continue
        table = tables[name]
        for column, (scale, integer_digits) in columns.items():
            if column not in table.c:
                continue
            # Read the original DB value as text: a Numeric result processor
            # would round excess SQLite decimals before they can be reported.
            fields = [table.c.id, sa.cast(table.c[column], sa.String)]
            quantity = name == "bd_goods" and column.startswith("stock_")
            if quantity and "unit_id" in table.c:
                fields.extend([table.c.tenant_id, table.c.unit_id])
            invalid = []
            for row in conn.execute(sa.select(*fields)):
                allowed = unit_precisions.get((row[2], row[3]), scale) if len(row) == 4 else scale
                if _invalid_number(row[1], allowed, integer_digits):
                    invalid.append(row[0])
            if invalid:
                logger.warning(
                    "Legacy invalid/overprecision %s.%s; ids=%s; unchanged",
                    name,
                    column,
                    sorted(invalid),
                )


def _preflight(conn) -> None:
    metadata = sa.MetaData()
    names = {name for source, _, target in _RELATIONS for name in (source, target)}
    names.update(("bd_customer", "sys_user_role", "sys_role_dept"))
    existing = set(sa.inspect(conn).get_table_names())
    for name in sorted(names & existing):
        sa.Table(name, metadata, autoload_with=conn, resolve_fks=False)
    tables = metadata.tables
    goods, categories = tables["bd_goods"], tables["bd_goods_category"]
    blockers = []
    for label, ids in (
        (
            "uq_goods_barcode_active_tenant",
            _duplicate_ids(
                conn,
                goods,
                "barcode",
                goods.c.is_deleted.is_(False),
                optional=True,
            ),
        ),
        (
            "uq_goods_category_root_name_tenant",
            _duplicate_ids(
                conn,
                categories,
                "name",
                categories.c.parent_id.is_(None),
            ),
        ),
    ):
        if ids:
            blockers.append(f"duplicate normalized values {label}: ids={ids}")
    for label, ids in (
        (
            "bd_goods.barcode",
            _non_normalized_ids(
                conn,
                goods,
                "barcode",
                goods.c.is_deleted.is_(False),
            ),
        ),
        # Both category indexes retain soft-deleted rows and cover every level.
        (
            "bd_goods_category.name",
            _non_normalized_ids(
                conn,
                categories,
                "name",
                sa.true(),
            ),
        ),
    ):
        if ids:
            blockers.append(f"non-normalized {label}: ids={ids}")
    _report_references(conn, tables)
    _report_numbers(conn, tables)
    if blockers:
        raise RuntimeError(
            "Migration blocked; "
            + "; ".join(blockers)
            + ". Review these row IDs before alembic upgrade head. "
            "No historical data was normalized, deleted or reassigned."
        )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        # Prevent concurrent inserts/updates from racing the duplicate preflight.
        conn.execute(sa.text("LOCK TABLE bd_goods, bd_goods_category IN SHARE ROW EXCLUSIVE MODE"))
    _preflight(conn)  # All checks happen before any schema or data modification.

    for name in _TENANT_TABLES:
        op.add_column(name, sa.Column("tenant_id", _BIGINT_PK, nullable=True))
        op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"], unique=False)
    op.create_index(
        "uq_goods_barcode_active_tenant",
        "bd_goods",
        ["barcode", "tenant_id"],
        unique=True,
        postgresql_where=sa.text(_BARCODE_WHERE),
        sqlite_where=sa.text(_BARCODE_WHERE),
    )
    op.create_index(
        "uq_goods_category_root_name_tenant",
        "bd_goods_category",
        ["name", "tenant_id"],
        unique=True,
        postgresql_where=sa.text(_ROOT_WHERE),
        sqlite_where=sa.text(_ROOT_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_goods_category_root_name_tenant", table_name="bd_goods_category")
    op.drop_index("uq_goods_barcode_active_tenant", table_name="bd_goods")
    for name in reversed(_TENANT_TABLES):
        op.drop_index(f"ix_{name}_tenant_id", table_name=name)
        op.drop_column(name, "tenant_id")
