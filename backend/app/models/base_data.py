"""基础资料模型（表名前缀 bd_）。"""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import (
    BIGINT_PK,
    SoftDeleteMixin,
    TimestampMixin,
    UserMixin,
    VersionMixin,
)


class BdGoodsCategory(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """商品分类。"""

    __tablename__ = "bd_goods_category"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("bd_goods_category.id"))
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class BdUnit(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """计量单位。"""

    __tablename__ = "bd_unit"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    precision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class BdGoods(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """商品。"""

    __tablename__ = "bd_goods"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    category_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("bd_goods_category.id"))
    spec: Mapped[str | None] = mapped_column(String(128))
    unit_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("bd_unit.id"))
    barcode: Mapped[str | None] = mapped_column(String(64))
    cost_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    stock_lower: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    stock_upper: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class BdWarehouse(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """仓库。"""

    __tablename__ = "bd_warehouse"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    manager_id: Mapped[int | None] = mapped_column(BIGINT_PK, ForeignKey("sys_user.id"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class BdSupplier(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """供应商。"""

    __tablename__ = "bd_supplier"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))
    tax_no: Mapped[str | None] = mapped_column(String(64))
    bank: Mapped[str | None] = mapped_column(String(128))
    account: Mapped[str | None] = mapped_column(String(64))
    credit_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))


class BdCustomer(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, Base):
    """客户。"""

    __tablename__ = "bd_customer"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))
    tax_no: Mapped[str | None] = mapped_column(String(64))
    bank: Mapped[str | None] = mapped_column(String(128))
    account: Mapped[str | None] = mapped_column(String(64))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    credit_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))
