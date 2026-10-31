"""add column server defaults

Revision ID: c7e1a9b4d2f3
Revises: b8e2f5a3c6d1
Create Date: 2026-10-31 02:00:00.000000

为 version / is_deleted 两列补齐数据库层默认值（server_default）。

背景：模型 Mixin 早期只在 Python 侧给了 default，数据库列没有默认值；
init_db 的 create_all 建表后，直接写 SQL 的种子数据插入这些列会违反 NOT NULL。
本迁移把默认值补到已有表上，使「ORM 插入」与「原生 SQL 插入」行为一致。

注意：SQLite 不支持 ALTER COLUMN SET DEFAULT，因此该方言下直接跳过
（新建 SQLite 库由模型 server_default 保证正确）。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c7e1a9b4d2f3"
down_revision: str | None = "b8e2f5a3c6d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 列默认值：列名 → 默认值 SQL 表达式
_COLUMN_DEFAULTS: dict[str, str] = {
    "version": "1",
    "is_deleted": "false",
}


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        # SQLite 不支持 ALTER COLUMN SET DEFAULT，跳过
        return
    inspector = sa.inspect(conn)
    for table_name in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column_name, default_sql in _COLUMN_DEFAULTS.items():
            if column_name not in existing:
                continue
            op.alter_column(
                table_name,
                column_name,
                server_default=sa.text(default_sql),
                existing_type=sa.Integer() if column_name == "version" else sa.Boolean(),
            )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    inspector = sa.inspect(conn)
    for table_name in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column_name in _COLUMN_DEFAULTS:
            if column_name not in existing:
                continue
            op.alter_column(
                table_name,
                column_name,
                server_default=None,
                existing_type=sa.Integer() if column_name == "version" else sa.Boolean(),
            )
