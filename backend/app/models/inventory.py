"""库存模块模型（表名前缀 inv_）。

本文件定义库存模块的全部 7 张表，对应《数据库表设计.md》第 8 节：
  - InvStock：实时库存（qty / locked_qty / available_qty 三量 + 移动加权平均成本）
  - InvStockFlow：出入库流水（只写不删，每次库存变动一条）
  - InvTransfer / InvTransferItem：库存调拨主表与明细（跨仓两行库存 + 2 条流水）
  - InvCheck / InvCheckItem：库存盘点主表与明细（确认时重算差异）
  - InvAlert：库存预警（低于下限 / 高于上限 / 呆滞）

状态枚举：inv_transfer / inv_check.status 0 草稿 / 1 已确认；
inv_alert.alert_type 1 低于下限 / 2 高于上限 / 3 呆滞。

所有库存变动统一走 InventoryService：同一事务写 inv_stock 与 inv_stock_flow，
并对 inv_stock 行加 SELECT ... FOR UPDATE 行锁；负库存一律禁止。
"""

from datetime import date, datetime  # date：DATE 字段；datetime：TIMESTAMPTZ 字段
from decimal import Decimal  # NUMERIC 字段（数量、成本）的类型标注

from sqlalchemy import (  # SQLAlchemy 列类型与约束构件
    Boolean,  # BOOLEAN 列类型，预警处理标记
    CheckConstraint,  # CHECK 约束，数据库层兜底校验数量/成本
    Date,  # DATE 列类型
    DateTime,  # TIMESTAMPTZ 列类型（带时区时间戳）
    ForeignKey,  # 外键声明
    Index,  # 自定义索引
    Numeric,  # NUMERIC 精确小数，数量/成本专用
    SmallInteger,  # SMALLINT，方向/类型类小整数
    String,  # 变长字符串
    text,  # 原样 SQL 片段，用于部分索引的 WHERE 条件
)
from sqlalchemy.orm import Mapped, mapped_column  # SQLAlchemy 2.0 类型化映射写法

from app.db.base import Base  # 所有模型的声明基类
from app.models.mixins import (  # 公共字段 Mixin 与跨库主键类型
    BIGINT_PK,  # PostgreSQL BIGINT / SQLite INTEGER 的主键类型变体
    SoftDeleteMixin,  # is_deleted 逻辑删除
    TenantMixin,  # tenant_id 多租户隔离
    TimestampMixin,  # created_at / updated_at
    UserMixin,  # created_by / updated_by
    VersionMixin,  # version 乐观锁
)


# 明细表公共继承链：有审计与软删，但无乐观锁 version（设计文档第 2 节例外规则）
_ITEM_MIXINS = (TimestampMixin, UserMixin, SoftDeleteMixin, TenantMixin)


# 库存表：inv_stock，同一商品在同一仓库只有一行（软删范围内唯一）
class InvStock(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """库存表：inv_stock。

    同一商品在同一仓库只有一行，通过 qty / locked_qty / available_qty 三个字段
    维护账面、锁定、可用数量；avg_cost 为移动加权平均成本。
    """

    __tablename__ = "inv_stock"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BIGINT_PK, nullable=False, index=True, comment="租户 ID")
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False, index=True
    )
    goods_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False, index=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)  # 账面数量
    locked_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=0, nullable=False
    )  # 锁定数量
    available_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        default=lambda context: (
            context.get_current_parameters().get("qty", Decimal("0"))
            - context.get_current_parameters().get("locked_qty", Decimal("0"))
        ),
        nullable=False,
    )  # 可用数量 = qty - locked_qty
    avg_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )  # 移动加权平均成本，金额精度 18,2（与全局金额口径一致）
    # 首次入库日期：呆滞预警在无出库记录时的计算基准，入库确认事务内置空则写
    first_in_date: Mapped[date | None] = mapped_column(Date)
    # 最近一次出库日期：呆滞预警的计算基准，出库确认事务内刷新
    last_out_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("qty >= 0", name="ck_inv_stock_qty_nonnegative"),
        CheckConstraint("locked_qty >= 0", name="ck_inv_stock_locked_qty_nonnegative"),
        CheckConstraint("available_qty >= 0", name="ck_inv_stock_available_qty_nonnegative"),
        CheckConstraint(
            "available_qty = qty - locked_qty",
            name="ck_inv_stock_available_qty_consistent",
        ),
        CheckConstraint("avg_cost >= 0", name="ck_inv_stock_avg_cost_nonnegative"),
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


# 出入库流水表：inv_stock_flow，每次库存变动写一条，只写不删
class InvStockFlow(TimestampMixin, TenantMixin, Base):
    """库存流水表：inv_stock_flow。

    每次库存变动写一条流水，记录变动前/后数量与单位成本，方便追溯与对账。
    只写不删，不带 is_deleted / version / updated_by（设计文档第 2 节例外规则）。
    """

    __tablename__ = "inv_stock_flow"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(BIGINT_PK, nullable=False, index=True, comment="租户 ID")
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
    direction: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 1=入库, -1=出库
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 变动数量（绝对值）
    before_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 变动前账面数量
    after_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 变动后账面数量
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), default=0, nullable=False
    )  # 该次结转单位成本，金额精度 18,2
    source_type: Mapped[str | None] = mapped_column(String(32))  # 来源单据类型（pur_inbound 等）
    source_no: Mapped[str | None] = mapped_column(String(64))  # 来源单号，命名索引见 __table_args__
    # 业务时间 TIMESTAMPTZ：流水倒序展示、按日汇总与报表归集均以此为基准
    biz_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operator_id: Mapped[int | None] = mapped_column(BIGINT_PK)  # 操作人用户 ID，只写不更新
    remark: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        # 按商品 + 业务时间查流水：出入库流水页与销售/采购成本报表的高频路径
        Index("idx_flow_goods_time", "goods_id", "biz_time"),
        # 按仓库 + 业务时间查流水：仓库维度流水与库存报表
        Index("idx_flow_warehouse_time", "warehouse_id", "biz_time"),
        # 按来源单号查流水：单据详情跳转与退货追溯
        Index("idx_flow_source_no", "source_no"),
    )


# 库存调拨主表：inv_transfer，确认时按 id 顺序锁两仓行，调出仓转出、调入仓重新加权
class InvTransfer(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """库存调拨表：inv_transfer。

    字段含义：
      - transfer_no：调拨单号（V1 固定前缀 + 序列生成，保证唯一）
      - from_warehouse_id / to_warehouse_id：调出 / 调入仓库，两者不得相同（Service 校验 422）
      - transfer_date：调拨日期
      - status：0 草稿 / 1 已确认；确认后不可改
    """

    __tablename__ = "inv_transfer"
    __table_args__ = (
        Index("uq_inv_transfer_no", "transfer_no", unique=True),  # 调拨单号唯一
        Index("idx_inv_transfer_from_wh", "from_warehouse_id"),  # 按调出仓筛选
        Index("idx_inv_transfer_to_wh", "to_warehouse_id"),  # 按调入仓筛选
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    transfer_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 调拨单号，唯一
    from_warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 调出仓库
    to_warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 调入仓库，不得与调出仓相同
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)  # 调拨日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 库存调拨明细表：inv_transfer_item，cost 按调出仓 avg_cost 结转
class InvTransferItem(*_ITEM_MIXINS, Base):
    """库存调拨明细表：inv_transfer_item。

    字段含义：
      - qty：调拨数量，>0；确认时校验调出仓可用量
      - cost：结转成本（按调出仓 avg_cost），调入仓以它重新加权，Service 维护
    """

    __tablename__ = "inv_transfer_item"
    __table_args__ = (
        Index("idx_inv_transfer_item_transfer", "transfer_id"),  # 按调拨单查明细
        CheckConstraint("qty > 0", name="ck_inv_transfer_item_qty_positive"),  # 调拨数量必须为正
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    transfer_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("inv_transfer.id", ondelete="CASCADE"), nullable=False
    )  # 所属调拨单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 调拨数量
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 结转单位成本


# 库存盘点主表：inv_check，确认时按当前 qty 重算差异并调整，qty 不得小于 locked_qty
class InvCheck(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """库存盘点表：inv_check。

    字段含义：
      - check_no：盘点单号（V1 固定前缀 + 序列生成，保证唯一）
      - adjust_by：盘点调整人
      - total_diff_qty / total_diff_amount：差异合计数量 / 金额，确认时由 Service 汇总
      - status：0 草稿 / 1 已确认；确认后不可改
    """

    __tablename__ = "inv_check"
    __table_args__ = (
        Index("uq_inv_check_no", "check_no", unique=True),  # 盘点单号唯一
        Index("idx_inv_check_warehouse", "warehouse_id"),  # 按仓库筛选盘点单
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    check_no: Mapped[str] = mapped_column(String(32), nullable=False)  # 盘点单号，唯一
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 盘点仓库
    check_date: Mapped[date] = mapped_column(Date, nullable=False)  # 盘点日期
    status: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)  # 0 草稿/1 已确认
    adjust_by: Mapped[int | None] = mapped_column(BIGINT_PK)  # 盘点调整人用户 ID
    # 差异数量
    total_diff_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    # 差异合计金额
    total_diff_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注


# 库存盘点明细表：inv_check_item，book_qty 为草稿快照，确认时按当前 qty 重算
class InvCheckItem(*_ITEM_MIXINS, Base):
    """库存盘点明细表：inv_check_item。

    字段含义：
      - book_qty：账面数量快照（草稿保存时），确认时按当前 qty 重算，不信任草稿值
      - real_qty：实盘数量，≥0
      - diff_qty：差异 = real_qty - 确认时账面 qty，确认时由 Service 重算
      - cost：调整成本（按当前 avg_cost），差异金额 = diff_qty × cost
    """

    __tablename__ = "inv_check_item"
    __table_args__ = (
        Index("idx_inv_check_item_check", "check_id"),  # 按盘点单查明细
        CheckConstraint("real_qty >= 0", name="ck_inv_check_item_real_nonnegative"),  # 实盘数量非负
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    check_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("inv_check.id", ondelete="CASCADE"), nullable=False
    )  # 所属盘点单，级联删除
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    book_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)  # 账面快照
    real_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 实盘数量
    # 差异，确认时算
    diff_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0, nullable=False)  # 调整单位成本


# 库存预警表：inv_alert，同一仓库+商品+类型未处理只允许一条（部分唯一索引）
class InvAlert(TimestampMixin, UserMixin, SoftDeleteMixin, VersionMixin, TenantMixin, Base):
    """库存预警表：inv_alert。

    字段含义：
      - alert_type：1 低于下限 / 2 高于上限 / 3 呆滞
      - current_qty：触发时的数量快照
      - threshold：触发阈值（下限 / 上限 / 呆滞天数换算的基准值）
      - handled：是否已处理；handled_by / handled_at 为处理人 / 处理时间
      - trigger_time：触发时间（TIMESTAMPTZ）

    唯一约束：同一仓库 + 商品 + 类型在「未处理」状态下唯一，避免定时任务重复生成；
    已处理（handled=true）的行不占用唯一键，可再次触发。
    """

    __tablename__ = "inv_alert"
    __table_args__ = (
        # 未处理预警唯一：部分索引，仅 handled = false 的行参与唯一约束
        Index(
            "uq_inv_alert_wh_goods_type",
            "warehouse_id",
            "goods_id",
            "alert_type",
            unique=True,
            sqlite_where=text("handled = 0"),  # SQLite 部分索引条件
            postgresql_where=text("NOT handled"),  # PostgreSQL 部分索引条件
        ),
        CheckConstraint("alert_type IN (1, 2, 3)", name="ck_inv_alert_type"),  # 预警类型限定三种
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)  # 主键
    warehouse_id: Mapped[int] = mapped_column(
        BIGINT_PK, ForeignKey("bd_warehouse.id"), nullable=False
    )  # 仓库
    # 商品
    goods_id: Mapped[int] = mapped_column(BIGINT_PK, ForeignKey("bd_goods.id"), nullable=False)
    # 1 低于下限/2 高于上限/3 呆滞
    alert_type: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 触发时数量
    current_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    # 触发阈值
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    handled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 是否已处理
    handled_by: Mapped[int | None] = mapped_column(BIGINT_PK)  # 处理人用户 ID
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 处理时间
    # 触发时间
    trigger_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255))  # 备注
