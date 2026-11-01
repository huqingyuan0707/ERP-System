"""库存模型（表名前缀 inv_）。"""

from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import (
    BIGINT_PK,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UserMixin,
    VersionMixin,
)


class InvStock(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """库存表：inv_stock。

    同一商品在同一仓库只有一行，通过 qty / locked_qty / available_qty 三个字段
    维护账面、锁定、可用数量。
    """

    __tablename__ = "inv_stock"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BIGINT_PK, nullable=False, index=True, comment="租户 ID"
    )
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False, index=True
    )
    goods_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False, index=True
    )
    qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 账面数量
    locked_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 锁定数量
    available_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 可用数量 = qty - locked_qty
    avg_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 移动加权平均成本

    __table_args__ = (
        # 同一租户下同一商品+仓库唯一（仅未删除行生效）
        Index(
            "uq_inv_stock_warehouse_goods_tenant",
            "warehouse_id",
            "goods_id",
            "tenant_id",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
            postgresql_where=text("is_deleted = false"),
        ),
    )


class InvStockFlow(TimestampMixin, TenantMixin, Base):
    """库存流水表：inv_stock_flow。

    每次库存变动写一条流水，记录变动前/后数量与单位成本，方便追溯与对账。
    """

    __tablename__ = "inv_stock_flow"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        BIGINT_PK, nullable=False, index=True, comment="租户 ID"
    )
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False, index=True
    )
    goods_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False, index=True
    )
    # 流水类型取值范围：
    # purchase_in/purchase_return/sale_out/sale_return/
    # transfer_in/transfer_out/check_gain/check_loss
    flow_type: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=入库, -1=出库
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 变动数量
    before_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    after_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 该次结转单位成本
    source_type: Mapped[str | None] = mapped_column(String(32))  # 来源单据类型
    source_no: Mapped[str | None] = mapped_column(String(64), index=True)  # 来源单号
    # 业务日期，格式 YYYYMMDD，用于按日汇总
    biz_time: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remark: Mapped[str | None] = mapped_column(String(255))
