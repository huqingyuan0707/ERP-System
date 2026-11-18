"""SAL-02/04~09 销售订单：补齐 billno.sal_order 单号规则与 sal:order:* 按钮权限

Revision ID: b7d2e9f4a6c1
Revises: f4a6b8c0d2e9
Create Date: 2026-11-18 12:00:00.000000

变更内容（纯数据，无表结构变更）：
  1. sys_config 新增 billno.sal_order = "SO,4"（销售订单号前缀 SO + yyyyMMdd + 4 位序号）；
  2. 补齐 sal:order:add / edit / submit / audit / close 按钮权限（挂到 /sales/order 菜单
     sal:order:list 下）并授权 admin 角色。

sal_order / sal_order_item 表结构已在基线迁移建好，无需再动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b7d2e9f4a6c1"
down_revision: str | None = "f4a6b8c0d2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 单号规则：(键, 值, 说明)
_CONFIGS: list[tuple[str, str, str]] = [
    ("billno.sal_order", "SO,4", "销售订单号规则"),
]

# (按钮名称, 权限码, 父菜单权限码, 排序)
_BUTTONS: list[tuple[str, str, str, int]] = [
    ("新增销售订单", "sal:order:add", "sal:order:list", 1),
    ("修改销售订单", "sal:order:edit", "sal:order:list", 2),
    ("提交销售订单", "sal:order:submit", "sal:order:list", 3),
    ("审核销售订单", "sal:order:audit", "sal:order:list", 4),
    ("关闭销售订单", "sal:order:close", "sal:order:list", 5),
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

    # 1. 销售订单号规则：SO + yyyyMMdd + 4 位序号
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
            sa.text("DELETE FROM sys_permission WHERE perm_code = :code"), {"code": perm_code}
        )

    for key, _, _ in _CONFIGS:
        conn.execute(sa.text("DELETE FROM sys_config WHERE key = :key"), {"key": key})
