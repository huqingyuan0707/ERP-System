"""dict config billno and role dept tables

Revision ID: a5c9d3e7f1b2
Revises: c3a7f1d2e4b5
Create Date: 2026-10-30 14:00:00.000000

C-05 字典/参数/单号运行时 + C-04 数据范围关联表：
新建 sys_dict_type、sys_dict_item、sys_config、sys_bill_no_seq、sys_role_dept，
并补充字典维护/参数维护按钮权限、常用字典与系统参数种子数据。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a5c9d3e7f1b2"
down_revision: str | None = "c3a7f1d2e4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BIGINT_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")

_DICT_TYPES: list[tuple[str, str]] = [
    ("yes_no", "是否"),
    ("invoice_type", "发票类型"),
    ("settlement_type", "结算方式"),
]

_DICT_ITEMS: list[tuple[str, str, str, int]] = [
    ("yes_no", "是", "1", 1),
    ("yes_no", "否", "0", 2),
    ("invoice_type", "增值税专用发票", "special", 1),
    ("invoice_type", "增值税普通发票", "normal", 2),
    ("invoice_type", "不开票", "none", 3),
    ("settlement_type", "现结", "cash", 1),
    ("settlement_type", "月结30天", "net30", 2),
    ("settlement_type", "月结60天", "net60", 3),
    ("settlement_type", "预付", "prepaid", 4),
]

_CONFIGS: list[tuple[str, str, str]] = [
    ("company.name", "某某科技有限公司", "公司名称（打印/导出抬头）"),
    ("company.address", "某某省某某市某某区某某路 88 号", "公司地址"),
    ("company.phone", "400-000-0000", "公司电话"),
    ("sales.credit.mode", "block", "信用额度校验模式 block/warn/off"),
    ("billno.pur_apply", "PA,4", "采购申请单号规则"),
    ("billno.pur_order", "PO,4", "采购订单单号规则"),
    ("billno.pur_inbound", "PI,4", "采购入库单号规则"),
    ("billno.pur_return", "PR,4", "采购退货单号规则"),
    ("billno.sal_order", "SO,4", "销售订单单号规则"),
    ("billno.sal_outbound", "SI,4", "销售出库单号规则"),
    ("billno.sal_return", "SR,4", "销售退货单号规则"),
    ("billno.inv_transfer", "IT,4", "库存调拨单号规则"),
    ("billno.inv_check", "IC,4", "库存盘点单号规则"),
    ("billno.fin_receipt", "SK,4", "收款单号规则"),
    ("billno.fin_payment", "FK,4", "付款单号规则"),
    ("billno.fin_expense", "FY,4", "费用单号规则"),
]

_BUTTONS: list[tuple[str, str]] = [
    ("新增字典", "base:dict:add"),
    ("修改字典", "base:dict:edit"),
    ("删除字典", "base:dict:delete"),
    ("保存参数", "sys:config:edit"),
]


def _exists(conn: sa.Connection, sql: str, params: dict[str, object]) -> bool:
    return conn.execute(sa.text(sql), params).first() is not None


def _timestamp_column(name: str) -> sa.Column:
    """created_at/updated_at 公共列定义。"""
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    conn = op.get_bind()

    op.create_table(
        "sys_role_dept",
        sa.Column(
            "role_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_role.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "dept_id",
            _BIGINT_PK,
            sa.ForeignKey("sys_dept.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "sys_dict_type",
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("status", sa.Integer, nullable=False, server_default="1"),
        sa.Column("remark", sa.String(255)),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at"),
    )
    op.create_index(
        "uq_dict_type_code", "sys_dict_type", ["code"], unique=True,
        sqlite_where=sa.text("is_deleted = 0"), postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "sys_dict_item",
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("type_code", sa.String(64), nullable=False, index=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Integer, nullable=False, server_default="1"),
        sa.Column("remark", sa.String(255)),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at"),
    )
    op.create_index(
        "uq_dict_item", "sys_dict_item", ["type_code", "value"], unique=True,
        sqlite_where=sa.text("is_deleted = 0"), postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "sys_config",
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(128), nullable=False, index=True),
        sa.Column("value", sa.Text),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("remark", sa.String(255)),
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        _timestamp_column("created_at"),
        _timestamp_column("updated_at"),
    )
    op.create_index(
        "uq_config_key", "sys_config", ["key"], unique=True,
        sqlite_where=sa.text("is_deleted = 0"), postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "sys_bill_no_seq",
        sa.Column("id", _BIGINT_PK, primary_key=True, autoincrement=True),
        sa.Column("biz_key", sa.String(64), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("current_seq", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("biz_key", "period", name="uq_bill_no_seq"),
    )

    # 种子数据：字典类型与字典项
    for code, name in _DICT_TYPES:
        if not _exists(conn, "SELECT 1 FROM sys_dict_type WHERE code = :code", {"code": code}):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_dict_type (code, name, status, is_deleted)"
                    " VALUES (:code, :name, 1, 0)"
                ),
                {"code": code, "name": name},
            )
    for type_code, label, value, sort in _DICT_ITEMS:
        if not _exists(
            conn,
            "SELECT 1 FROM sys_dict_item WHERE type_code = :tc AND value = :v",
            {"tc": type_code, "v": value},
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_dict_item (type_code, label, value, sort, status, is_deleted)"
                    " VALUES (:tc, :label, :v, :sort, 1, 0)"
                ),
                {"tc": type_code, "label": label, "v": value, "sort": sort},
            )

    # 种子数据：系统参数
    for key, value, name in _CONFIGS:
        if not _exists(conn, "SELECT 1 FROM sys_config WHERE key = :key", {"key": key}):
            conn.execute(
                sa.text(
                    "INSERT INTO sys_config (key, value, name, is_deleted)"
                    " VALUES (:key, :v, :name, 0)"
                ),
                {"key": key, "v": value, "name": name},
            )

    # 种子数据：字典/参数维护按钮权限（挂到对应菜单），并授予 admin
    for name, perm_code in _BUTTONS:
        if not _perm_exists(conn, perm_code):
            parent = "base:dict:list" if perm_code.startswith("base:dict") else "sys:config:list"
            conn.execute(
                sa.text(
                    "INSERT INTO sys_permission"
                    " (name, type, perm_code, parent_id, sort, is_deleted)"
                    " VALUES (:name, 3, :code,"
                    " (SELECT id FROM sys_permission WHERE perm_code = :parent), 9, 0)"
                ),
                {"name": name, "code": perm_code, "parent": parent},
            )
        _grant(conn, perm_code)


def _perm_exists(conn: sa.Connection, perm_code: str) -> bool:
    return _exists(
        conn, "SELECT 1 FROM sys_permission WHERE perm_code = :code", {"code": perm_code}
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


def downgrade() -> None:
    conn = op.get_bind()
    for perm_code in ("base:dict:add", "base:dict:edit", "base:dict:delete", "sys:config:edit"):
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
    op.drop_table("sys_bill_no_seq")
    op.drop_table("sys_config")
    op.drop_table("sys_dict_item")
    op.drop_table("sys_dict_type")
    op.drop_table("sys_role_dept")
