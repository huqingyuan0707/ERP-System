"""Seed export permissions without rewriting historical RBAC data.

Revision ID: d4e9a1b7c3f5
Revises: f2b6c8d0e1a3

The upgrade is deliberately idempotent.  A duplicated historical permission
code is left intact; the lowest ID is the canonical row used for new admin
role grants.  The downgrade is a conservative no-op: these global seed rows
and grants may predate this revision or be referenced by later data, so there
is no reliable, non-destructive way to identify only rows created here.
Downgrade followed by upgrade is therefore safe and does not duplicate data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "d4e9a1b7c3f5"
down_revision: str | None = "f2b6c8d0e1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPORT_PERMISSIONS: tuple[tuple[str, str, str | None, int], ...] = (
    ("导出库存", "inv:stock:export", "inv:stock:list", 9),
    ("导出报表", "report:export", None, 9),
    ("导出对账单", "fin:statement:export", "fin:statement:list", 9),
)

_PERMISSION = sa.table(
    "sys_permission",
    sa.column("id", sa.BigInteger()),
    sa.column("name", sa.String()),
    sa.column("type", sa.Integer()),
    sa.column("perm_code", sa.String()),
    sa.column("parent_id", sa.BigInteger()),
    sa.column("route_path", sa.String()),
    sa.column("sort", sa.Integer()),
    sa.column("is_deleted", sa.Boolean()),
)
_ROLE = sa.table(
    "sys_role",
    sa.column("id", sa.BigInteger()),
    sa.column("code", sa.String()),
    sa.column("status", sa.Integer()),
    sa.column("is_deleted", sa.Boolean()),
)
_ROLE_PERMISSION = sa.table(
    "sys_role_permission",
    sa.column("role_id", sa.BigInteger()),
    sa.column("permission_id", sa.BigInteger()),
)


def _canonical_permission_id(conn: sa.Connection, perm_code: str) -> int | None:
    """Return the lowest ID for a code without assuming historical uniqueness."""
    return conn.execute(
        sa.select(sa.func.min(_PERMISSION.c.id)).where(_PERMISSION.c.perm_code == perm_code)
    ).scalar()


def _report_parent_id(conn: sa.Connection) -> int | None:
    """Prefer the report directory, then a report menu, when either exists."""
    return conn.execute(
        sa.select(_PERMISSION.c.id)
        .where(
            sa.or_(
                sa.and_(
                    _PERMISSION.c.type == 1,
                    sa.or_(
                        _PERMISSION.c.name == "报表中心",
                        _PERMISSION.c.route_path == "/report",
                        _PERMISSION.c.perm_code == "report",
                        _PERMISSION.c.perm_code == "rpt",
                    ),
                ),
                _PERMISSION.c.perm_code.in_(
                    (
                        "rpt:purchase:list",
                        "rpt:sales:list",
                        "rpt:inventory:list",
                        "rpt:finance:list",
                        "rpt:dashboard:list",
                    )
                ),
            )
        )
        .order_by(
            sa.case(
                (
                    sa.and_(
                        _PERMISSION.c.type == 1,
                        sa.or_(
                            _PERMISSION.c.name == "报表中心",
                            _PERMISSION.c.route_path == "/report",
                            _PERMISSION.c.perm_code == "report",
                            _PERMISSION.c.perm_code == "rpt",
                        ),
                    ),
                    0,
                ),
                else_=1,
            ),
            _PERMISSION.c.id,
        )
        .limit(1)
    ).scalar_one_or_none()


def _parent_id(conn: sa.Connection, parent_code: str | None) -> int | None:
    if parent_code is None:
        return _report_parent_id(conn)
    return _canonical_permission_id(conn, parent_code)


def _grant_enabled_admin_roles(conn: sa.Connection, permission_id: int) -> None:
    missing_grants = sa.select(_ROLE.c.id, sa.literal(permission_id)).where(
        _ROLE.c.code == "admin",
        _ROLE.c.status == 1,
        _ROLE.c.is_deleted.is_(False),
        ~sa.exists(
            sa.select(1).where(
                _ROLE_PERMISSION.c.role_id == _ROLE.c.id,
                _ROLE_PERMISSION.c.permission_id == permission_id,
            )
        ),
    )
    conn.execute(
        sa.insert(_ROLE_PERMISSION).from_select(
            ("role_id", "permission_id"),
            missing_grants,
        )
    )


def upgrade() -> None:
    conn = op.get_bind()
    for name, perm_code, parent_code, sort in _EXPORT_PERMISSIONS:
        permission_id = _canonical_permission_id(conn, perm_code)
        if permission_id is None:
            conn.execute(
                sa.insert(_PERMISSION).values(
                    name=name,
                    type=3,
                    perm_code=perm_code,
                    parent_id=_parent_id(conn, parent_code),
                    sort=sort,
                    is_deleted=False,
                )
            )
            permission_id = _canonical_permission_id(conn, perm_code)
            if permission_id is None:  # Defensive: the preceding INSERT must create this row.
                raise RuntimeError(f"Failed to seed permission {perm_code}")
        _grant_enabled_admin_roles(conn, permission_id)


def downgrade() -> None:
    """Preserve seed data because this revision cannot prove which rows it owns."""
