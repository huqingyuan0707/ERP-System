"""dashboard menu and role assign perm

Revision ID: c3a7f1d2e4b5
Revises: dc982042fab9
Create Date: 2026-10-30 12:00:00.000000

为存量库补充 C-03 种子数据：经营看板菜单、角色分配按钮权限及内置角色授权。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c3a7f1d2e4b5"
down_revision: str | None = "dc982042fab9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INSERT_DASHBOARD = sa.text(
    "INSERT INTO sys_permission"
    " (name, type, perm_code, route_path, component, icon, sort, is_deleted)"
    " VALUES ('经营看板', 2, 'rpt:dashboard:list',"
    " '/dashboard', 'dashboard/DashboardPage', 'Odometer', 0, false)"
)

_INSERT_ROLE_ASSIGN = sa.text(
    "INSERT INTO sys_permission"
    " (name, type, perm_code, parent_id, sort, is_deleted)"
    " VALUES ('角色分配', 3, 'sys:role:assign',"
    " (SELECT id FROM sys_permission WHERE perm_code = 'sys:role:list'), 5, false)"
)

_GRANT = sa.text(
    "INSERT INTO sys_role_permission (role_id, permission_id) "
    "SELECT r.id, p.id FROM sys_role r, sys_permission p "
    "WHERE r.code IN :role_codes AND p.perm_code = :perm_code "
    "AND NOT EXISTS ("
    "  SELECT 1 FROM sys_role_permission rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
).bindparams(sa.bindparam("role_codes", expanding=True))


def _perm_exists(conn: sa.Connection, perm_code: str) -> bool:
    return (
        conn.execute(
            sa.text("SELECT 1 FROM sys_permission WHERE perm_code = :code"),
            {"code": perm_code},
        ).first()
        is not None
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _perm_exists(conn, "rpt:dashboard:list"):
        conn.execute(_INSERT_DASHBOARD)
        conn.execute(_GRANT, {"role_codes": ("admin", "viewer"), "perm_code": "rpt:dashboard:list"})
    if not _perm_exists(conn, "sys:role:assign"):
        conn.execute(_INSERT_ROLE_ASSIGN)
        conn.execute(_GRANT, {"role_codes": ("admin",), "perm_code": "sys:role:assign"})


def downgrade() -> None:
    conn = op.get_bind()
    for perm_code in ("sys:role:assign", "rpt:dashboard:list"):
        conn.execute(
            sa.text(
                "DELETE FROM sys_role_permission WHERE permission_id IN "
                "(SELECT id FROM sys_permission WHERE perm_code = :code)"
            ),
            {"code": perm_code},
        )
        conn.execute(
            sa.text("DELETE FROM sys_permission WHERE perm_code = :code"), {"code": perm_code}
        )
