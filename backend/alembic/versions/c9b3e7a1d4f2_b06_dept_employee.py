"""B-06 员工档案表与员工/部门按钮权限

Revision ID: c9b3e7a1d4f2
Revises: 7f496af09f7f
Create Date: 2026-11-05 12:00:00.000000

依据《数据库表设计.md》5.7 节新建 bd_employee（含 user_id），
并补齐 base:dept:* / base:employee:* 按钮权限，授权给 admin 角色。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9b3e7a1d4f2"
down_revision: str | None = "7f496af09f7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

_BUTTONS: list[tuple[str, str, int]] = [
    ("部门查看", "base:dept:list", 8),
    ("新增部门", "base:dept:add", 9),
    ("修改部门", "base:dept:edit", 10),
    ("删除部门", "base:dept:delete", 11),
    ("新增员工", "base:employee:add", 1),
    ("修改员工", "base:employee:edit", 2),
    ("删除员工", "base:employee:delete", 3),
]


def _perm_exists(conn: sa.Connection, perm_code: str) -> bool:
    return (
        conn.execute(
            sa.text("SELECT 1 FROM sys_permission WHERE perm_code = :code"),
            {"code": perm_code},
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
    if not sa.inspect(conn).has_table("bd_employee"):
        op.create_table(
            "bd_employee",
            sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
            sa.Column("tenant_id", _BIGINT_PK, nullable=False, server_default="1"),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
            ),
            sa.Column("created_by", _BIGINT_PK),
            sa.Column("updated_by", _BIGINT_PK),
            sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.Column("code", sa.String(32), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("dept_id", _BIGINT_PK, sa.ForeignKey("sys_dept.id"), nullable=False),
            sa.Column("position", sa.String(64)),
            sa.Column("mobile", sa.String(32)),
            sa.Column("entry_date", sa.Date()),
            sa.Column("status", sa.Integer(), server_default="1", nullable=False),
            sa.Column("user_id", _BIGINT_PK, sa.ForeignKey("sys_user.id")),
            sa.Column("remark", sa.String(255)),
        )
        op.create_index("ix_bd_employee_tenant_id", "bd_employee", ["tenant_id"])
        op.create_index("ix_bd_employee_code", "bd_employee", ["code"])
        op.create_index("idx_employee_dept", "bd_employee", ["dept_id"])
        op.create_index(
            "uq_employee_code_tenant", "bd_employee", ["code", "tenant_id"], unique=True
        )
        op.create_index(
            "uq_employee_user_tenant", "bd_employee", ["user_id", "tenant_id"], unique=True
        )

    for name, perm_code, sort in _BUTTONS:
        if not _perm_exists(conn, perm_code):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_permission (name, type, perm_code, parent_id, sort)"
                    " VALUES (:name, 3, :code,"
                    " (SELECT id FROM sys_permission WHERE perm_code = 'base:employee:list'),"
                    " :sort)"
                ),
                {"name": name, "code": perm_code, "sort": sort},
            )
        _grant(conn, perm_code)


def downgrade() -> None:
    conn = op.get_bind()
    for _, perm_code, _ in _BUTTONS:
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
    if sa.inspect(conn).has_table("bd_employee"):
        op.drop_table("bd_employee")
