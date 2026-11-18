"""F-04 付款管理：补齐 fin:payment:add/edit/writeoff/delete 按钮权限

Revision ID: f4a6b8c0d2e9
Revises: ff36274a4c73
Create Date: 2026-11-18 12:00:00.000000

变更内容（纯数据，无表结构变更）：
  补齐 fin:payment:add / edit / writeoff / delete 按钮权限（挂到 /finance/payment 菜单
  fin:payment:list 下）并授权 admin 角色。

fin_payment / fin_payment_item 表结构已在既有迁移建好，billno.fin_payment 单号规则
已在 a5c9d3e7f1b2 种子化，均无需再动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f4a6b8c0d2e9"
down_revision: str | None = "ff36274a4c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (按钮名称, 权限码, 父菜单权限码, 排序)
_BUTTONS: list[tuple[str, str, str, int]] = [
    ("新增付款", "fin:payment:add", "fin:payment:list", 1),
    ("修改付款", "fin:payment:edit", "fin:payment:list", 2),
    ("付款核销", "fin:payment:writeoff", "fin:payment:list", 3),
    ("删除付款", "fin:payment:delete", "fin:payment:list", 4),
]


def _perm_exists(conn: sa.Connection, perm_code: str) -> bool:
    """判断权限码是否已存在，避免重复插入。"""
    return (
        conn.execute(
            sa.text("SELECT 1 FROM sys_permission WHERE perm_code = :code"),
            {"code": perm_code},
        ).first()
        is not None
    )


def _grant(conn: sa.Connection, perm_code: str) -> None:
    """把权限码授予所有 admin 角色（角色按租户隔离，不能只取一个）。"""
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

    # 按钮权限：先补齐权限项，再授予 admin
    for button_name, perm_code, parent_code, sort in _BUTTONS:
        if not _perm_exists(conn, perm_code):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_permission (name, type, perm_code, parent_id, sort)"
                    " VALUES (:name, 3, :code,"
                    " (SELECT id FROM sys_permission WHERE perm_code = :parent), :sort)"
                ),
                {"name": button_name, "code": perm_code, "parent": parent_code, "sort": sort},
            )
        _grant(conn, perm_code)


def downgrade() -> None:
    conn = op.get_bind()

    # 反向：先撤销授权并删除权限项
    for _, perm_code, _, _ in _BUTTONS:
        conn.execute(
            sa.text(
                "DELETE FROM sys_role_permission WHERE permission_id IN"
                " (SELECT id FROM sys_permission WHERE perm_code = :code)"
            ),
            {"code": perm_code},
        )
        conn.execute(
            sa.text("DELETE FROM sys_permission WHERE perm_code = :code"),
            {"code": perm_code},
        )
