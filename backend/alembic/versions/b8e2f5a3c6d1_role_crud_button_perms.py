"""role crud button perms

Revision ID: b8e2f5a3c6d1
Revises: a5c9d3e7f1b2
Create Date: 2026-10-30 16:00:00.000000

为角色管理页补充按钮权限种子：sys:role:add/edit/delete，并授予 admin。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8e2f5a3c6d1"
down_revision: str | None = "a5c9d3e7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUTTONS: list[tuple[str, str, int]] = [
    ("新增角色", "sys:role:add", 2),
    ("修改角色", "sys:role:edit", 3),
    ("删除角色", "sys:role:delete", 4),
]


def _perm_exists(conn: sa.Connection, perm_code: str) -> bool:
    return (
        conn.execute(
            sa.text("SELECT 1 FROM sys_permission WHERE perm_code = :code"), {"code": perm_code}
        ).first()
        is not None
    )


def _grant(conn: sa.Connection, perm_code: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO sys_role_permission (role_id, permission_id)"
            " SELECT r.id, p.id FROM sys_role r, sys_permission p"
            " WHERE r.code = 'admin' AND p.perm_code = :code"
            " AND NOT EXISTS (SELECT 1 FROM sys_role_permission rp"
            "   WHERE rp.role_id = r.id AND rp.permission_id = p.id)"
        ),
        {"code": perm_code},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for name, perm_code, sort in _BUTTONS:
        if _perm_exists(conn, perm_code):
            _grant(conn, perm_code)
            continue
        conn.execute(
            sa.text(
                "INSERT INTO sys_permission"
                " (name, type, perm_code, parent_id, sort, is_deleted)"
                " VALUES (:name, 3, :code,"
                " (SELECT id FROM sys_permission WHERE perm_code = 'sys:role:list'), :sort, 0)"
            ),
            {"name": name, "code": perm_code, "sort": sort},
        )
        _grant(conn, perm_code)


def downgrade() -> None:
    conn = op.get_bind()
    for perm_code in ("sys:role:add", "sys:role:edit", "sys:role:delete"):
        conn.execute(
            sa.text(
                "DELETE FROM sys_role_permission WHERE permission_id IN"
                " (SELECT id FROM sys_permission WHERE perm_code = :code)"
            ),
            {"code": perm_code},
        )
        conn.execute(
            sa.text("DELETE FROM sys_permission WHERE perm_code = :code"), {"code": perm_code}
        )
