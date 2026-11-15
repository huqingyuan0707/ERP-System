"""P-02 采购订单：新增来源申请单溯源字段与 pur:apply:delete / pur:order:* 按钮权限

Revision ID: d1a4f8c2b6e9
Revises: c9b3e7a1d4f2
Create Date: 2026-11-06 12:00:00.000000

变更内容：
  1. pur_order 新增 source_apply_id（可空，外键 pur_apply.id），支持订单→申请单单据级溯源；
  2. pur_order_item 新增 apply_item_id（可空，外键 pur_apply_item.id），支持行级溯源；
  3. 补齐 pur:apply:delete 与 pur:order:add/edit/submit/audit/close 按钮权限并授权 admin。

两列均可空，历史订单为 NULL，不影响既有数据；外键不级联删除（申请单本身为软删）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a4f8c2b6e9"
down_revision: str | None = "c9b3e7a1d4f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

# (按钮名称, 权限码, 父菜单权限码, 排序)
_BUTTONS: list[tuple[str, str, str, int]] = [
    ("删除采购申请", "pur:apply:delete", "pur:apply:list", 5),
    ("新增采购订单", "pur:order:add", "pur:order:list", 1),
    ("修改采购订单", "pur:order:edit", "pur:order:list", 2),
    ("提交采购订单", "pur:order:submit", "pur:order:list", 3),
    ("审核采购订单", "pur:order:audit", "pur:order:list", 4),
    ("关闭采购订单", "pur:order:close", "pur:order:list", 5),
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
    inspector = sa.inspect(conn)

    # 1. 订单主表：来源申请单（单据级溯源）
    order_columns = {col["name"] for col in inspector.get_columns("pur_order")}
    if "source_apply_id" not in order_columns:
        op.add_column("pur_order", sa.Column("source_apply_id", _BIGINT_PK))
        op.create_index("idx_pur_order_source_apply", "pur_order", ["source_apply_id"])
        if conn.dialect.name != "sqlite":
            # SQLite 不支持给已有表追加外键；生产库（PostgreSQL）补外键约束
            op.create_foreign_key(
                "fk_pur_order_source_apply",
                "pur_order",
                "pur_apply",
                ["source_apply_id"],
                ["id"],
            )

    # 2. 订单明细表：来源申请明细（行级溯源）
    item_columns = {col["name"] for col in inspector.get_columns("pur_order_item")}
    if "apply_item_id" not in item_columns:
        op.add_column("pur_order_item", sa.Column("apply_item_id", _BIGINT_PK))
        op.create_index("idx_pur_order_item_apply_item", "pur_order_item", ["apply_item_id"])
        if conn.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_pur_order_item_apply_item",
                "pur_order_item",
                "pur_apply_item",
                ["apply_item_id"],
                ["id"],
            )

    # 3. 按钮权限：先补齐权限项，再授予 admin
    for name, perm_code, parent_code, sort in _BUTTONS:
        if not _perm_exists(conn, perm_code):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_permission (name, type, perm_code, parent_id, sort)"
                    " VALUES (:name, 3, :code,"
                    " (SELECT id FROM sys_permission WHERE perm_code = :parent), :sort)"
                ),
                {"name": name, "code": perm_code, "parent": parent_code, "sort": sort},
            )
        _grant(conn, perm_code)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 反向：先撤销授权并删除权限项，再删除列与索引
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

    item_columns = {col["name"] for col in inspector.get_columns("pur_order_item")}
    if "apply_item_id" in item_columns:
        if conn.dialect.name != "sqlite":
            op.drop_constraint("fk_pur_order_item_apply_item", "pur_order_item", type_="foreignkey")
        op.drop_index("idx_pur_order_item_apply_item", table_name="pur_order_item")
        op.drop_column("pur_order_item", "apply_item_id")

    order_columns = {col["name"] for col in inspector.get_columns("pur_order")}
    if "source_apply_id" in order_columns:
        if conn.dialect.name != "sqlite":
            op.drop_constraint("fk_pur_order_source_apply", "pur_order", type_="foreignkey")
        op.drop_index("idx_pur_order_source_apply", table_name="pur_order")
        op.drop_column("pur_order", "source_apply_id")
