"""SA-01 销售报价：补齐 billno.sal_quotation 单号规则与 sal:quotation:add/edit/delete 按钮权限

Revision ID: e2f4a6b8c0d2
Revises: d1a4f8c2b6e9
Create Date: 2026-11-07 12:00:00.000000

变更内容（纯数据，无表结构变更）：
  1. sys_config 新增 billno.sal_quotation = "QT,4"（报价单号前缀 QT + yyyyMMdd + 4 位序号）；
  2. 补齐 sal:quotation:add / edit / delete 按钮权限（挂到 /sales/quotation 菜单
     sal:quotation:list 下）并授权 admin 角色。

sal_quotation / sal_quotation_item 表结构已在 7f496af09f7f 建好，无需再动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f4a6b8c0d2"
down_revision: str | None = "d1a4f8c2b6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 单号规则：(键, 值, 说明)
_CONFIGS: list[tuple[str, str, str]] = [
    ("billno.sal_quotation", "QT,4", "销售报价单号规则"),
]

# (按钮名称, 权限码, 父菜单权限码, 排序)
_BUTTONS: list[tuple[str, str, str, int]] = [
    ("新增销售报价", "sal:quotation:add", "sal:quotation:list", 1),
    ("修改销售报价", "sal:quotation:edit", "sal:quotation:list", 2),
    ("删除销售报价", "sal:quotation:delete", "sal:quotation:list", 3),
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


def _config_exists(conn: sa.Connection, key: str) -> bool:
    """判断参数键是否已存在，避免重复插入。"""
    return (
        conn.execute(
            sa.text("SELECT 1 FROM sys_config WHERE key = :key"),
            {"key": key},
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

    # 1. 报价单号规则：QT + yyyyMMdd + 4 位序号
    for key, value, name in _CONFIGS:
        if not _config_exists(conn, key):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_config (key, value, name, is_deleted)"
                    " VALUES (:key, :v, :name, false)"
                ),
                {"key": key, "v": value, "name": name},
            )

    # 2. 按钮权限：先补齐权限项，再授予 admin
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

    # 反向：先撤销授权并删除权限项，再删除参数配置
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

    for key, _, _ in _CONFIGS:
        conn.execute(sa.text("DELETE FROM sys_config WHERE key = :key"), {"key": key})
