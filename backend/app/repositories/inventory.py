"""库存数据访问层（含行级锁与只读查询）。

本文件同时承担两类职责：
  1. 写路径：带 SELECT ... FOR UPDATE 的行锁查询、库存行创建、流水追加；
  2. 读路径：库存列表、库存汇总、流水列表的一次性 join 查询（规避 N+1）。

注意：inv_stock_flow 是只增不删的留痕表，没有 is_deleted 字段，
因此流水查询不能也不应附加软删过滤条件。
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Numeric, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdGoods, BdUnit, BdWarehouse
from app.models.inventory import InvStock, InvStockFlow
from app.repositories.base import BaseRepository
from app.schemas.inventory import FlowQuery, StockQuery


# 库存金额与流水金额的展示精度：金额统一 2 位小数（与库内 Numeric(18,2) 口径一致）
AMOUNT_SCALE = 2
# 库存下限判断：只有商品配置了下限（stock_lower > 0）才参与比较，否则视为不限
NO_LOWER_LIMIT = 0


class InventoryRepository(BaseRepository[InvStock]):
    """库存数据访问（不含业务规则，仅做带锁查询、写入与只读查询）。

    所有查询均按 tenant_id 做行级数据隔离，防止跨租户越权读取库存。
    """

    # 泛型父类的 CRUD / paginate 使用此模型（inv_stock）
    model = InvStock

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        # 复用父类的会话与租户过滤能力；父类属性名为 db，子类保留 _db 兼容既有调用
        super().__init__(db, tenant_id=tenant_id)
        self._db = db

    async def get_warehouse_for_update(self, warehouse_id: int) -> BdWarehouse | None:
        return await self._db.scalar(
            select(BdWarehouse)
            .where(
                BdWarehouse.id == warehouse_id,
                BdWarehouse.tenant_id == self.tenant_id,
                BdWarehouse.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_goods_for_update(self, goods_id: int) -> BdGoods | None:
        return await self._db.scalar(
            select(BdGoods)
            .where(
                BdGoods.id == goods_id,
                BdGoods.tenant_id == self.tenant_id,
                BdGoods.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_unit_for_update(self, unit_id: int) -> BdUnit | None:
        return await self._db.scalar(
            select(BdUnit)
            .where(
                BdUnit.id == unit_id,
                BdUnit.tenant_id == self.tenant_id,
                BdUnit.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_stock_for_update(self, warehouse_id: int, goods_id: int) -> InvStock | None:
        """按仓库+商品查询库存行并加行级锁（SELECT FOR UPDATE）。

        事务内执行，锁定该行直到 commit/rollback，防止并发超卖。
        仅返回当前租户的库存记录，实现行级数据隔离。
        """
        stmt = (
            select(InvStock)
            .where(
                InvStock.warehouse_id == warehouse_id,
                InvStock.goods_id == goods_id,
                InvStock.tenant_id == self.tenant_id,  # 租户隔离：只锁定当前租户数据
                InvStock.is_deleted.is_(False),
            )
            .with_for_update()  # PostgreSQL 行级锁
            .execution_options(populate_existing=True)  # 获锁后读取最新数量，避免会话旧值。
        )
        return await self._db.scalar(stmt)

    async def create_stock(self, warehouse_id: int, goods_id: int) -> InvStock:
        """若库存行不存在则创建（初始全为 0）。"""
        entity = InvStock(
            tenant_id=self.tenant_id,  # 写入当前租户 ID，确保多租户隔离
            warehouse_id=warehouse_id,
            goods_id=goods_id,
            qty=Decimal("0"),
            locked_qty=Decimal("0"),
            available_qty=Decimal("0"),
            avg_cost=Decimal("0"),
        )
        self._db.add(entity)
        await self._db.flush()  # 先 flush 得到 id，不提交事务
        return entity

    async def add_flow(
        self,
        warehouse_id: int,
        goods_id: int,
        flow_type: str,
        direction: int,
        qty: Decimal,
        before_qty: Decimal,
        after_qty: Decimal,
        unit_cost: Decimal,
        source_type: str | None,
        source_no: str | None,
        biz_time: str,
        remark: str | None,
    ) -> None:
        """写入一条库存流水（自动带上当前租户 ID）。"""
        self._db.add(
            InvStockFlow(
                tenant_id=self.tenant_id,  # 租户隔离：流水只可见于当前租户
                warehouse_id=warehouse_id,
                goods_id=goods_id,
                flow_type=flow_type,
                direction=direction,
                qty=qty,
                before_qty=before_qty,
                after_qty=after_qty,
                unit_cost=unit_cost,
                source_type=source_type,
                source_no=source_no,
                biz_time=biz_time,
                remark=remark,
            )
        )

    # ---------- 只读查询：库存列表 / 汇总 / 流水列表 ----------

    def build_stock_query(self, query: StockQuery) -> Select:
        """构建库存列表查询：一次 join 带出商品、仓库、单位，避免逐行回查（N+1）。

        join 条件里同时带上 tenant_id 与 is_deleted，防止跨租户或已删档案串入结果；
        商品与仓库为内连接（库存行必然有对应档案），单位允许为空所以用外连接。
        """
        # 库存金额 = ROUND(qty * avg_cost, 2)。qty 为 18,4、avg_cost 为 18,2，
        # 直接相乘会得到 6 位小数，这里统一按金额口径四舍五入到 2 位（仅展示，不回写）。
        # 注意：SQLite 的 ROUND 返回浮点，PG 的 ROUND(numeric) 返回 numeric，
        # 因此这里显式 CAST 成 Numeric(18,2)，保证两个方言下都是 Decimal，
        # 避免 pydantic 把 float 转成 Decimal 时带出 250.00000000000003 之类误差。
        stock_amount = func.cast(
            func.round(InvStock.qty * InvStock.avg_cost, AMOUNT_SCALE), Numeric(18, AMOUNT_SCALE)
        ).label("stock_amount")
        # 是否低于下限：仅当商品配置了下限（> 0）且账面数量小于下限时成立
        is_below_lower = (
            (BdGoods.stock_lower > NO_LOWER_LIMIT) & (InvStock.qty < BdGoods.stock_lower)
        ).label("is_below_lower")

        stmt = (
            select(
                InvStock.id,
                InvStock.warehouse_id,
                BdWarehouse.code.label("warehouse_code"),
                BdWarehouse.name.label("warehouse_name"),
                InvStock.goods_id,
                BdGoods.code.label("goods_code"),
                BdGoods.name.label("goods_name"),
                BdGoods.spec.label("goods_spec"),
                BdUnit.name.label("unit_name"),
                InvStock.qty,
                InvStock.locked_qty,
                InvStock.available_qty,
                InvStock.avg_cost,
                stock_amount,
                BdGoods.stock_lower,
                BdGoods.stock_upper,
                is_below_lower,
                InvStock.updated_at,
            )
            .join(
                BdGoods,
                (InvStock.goods_id == BdGoods.id)
                & (BdGoods.tenant_id == self.tenant_id)
                & (BdGoods.is_deleted.is_(False)),
            )
            .join(
                BdWarehouse,
                (InvStock.warehouse_id == BdWarehouse.id)
                & (BdWarehouse.tenant_id == self.tenant_id)
                & (BdWarehouse.is_deleted.is_(False)),
            )
            .outerjoin(
                BdUnit,
                (BdGoods.unit_id == BdUnit.id)
                & (BdUnit.tenant_id == self.tenant_id)
                & (BdUnit.is_deleted.is_(False)),
            )
            # 库存行本身也必须有软删过滤：软删的库存行不参与展示
            .where(InvStock.tenant_id == self.tenant_id, InvStock.is_deleted.is_(False))
        )

        # 精确筛选
        if query.warehouse_id is not None:
            stmt = stmt.where(InvStock.warehouse_id == query.warehouse_id)
        if query.goods_id is not None:
            stmt = stmt.where(InvStock.goods_id == query.goods_id)
        # 关键字：商品编码 / 名称 / 条码模糊匹配（与商品列表口径一致）
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    BdGoods.code.ilike(like),
                    BdGoods.name.ilike(like),
                    BdGoods.barcode.ilike(like),
                )
            )
        # 拣货场景：只看可用量大于 0
        if query.only_available:
            stmt = stmt.where(InvStock.available_qty > 0)
        # 补货场景：只看跌破下限的行（未配置下限的商品不参与）
        if query.below_lower:
            stmt = stmt.where(
                BdGoods.stock_lower > NO_LOWER_LIMIT,
                InvStock.qty < BdGoods.stock_lower,
            )

        # 默认按库存行 ID 倒序：新产生的库存行优先展示，同时保证分页结果稳定
        return stmt.order_by(InvStock.id.desc())

    async def list_stocks(self, query: StockQuery) -> tuple[list, int]:
        """分页查询库存列表，返回 (行列表, 总数)。

        走父类 paginate_rows：其 _apply_tenant_filter 追加的是 inv_stock.tenant_id，
        与本次查询主表一致，不会引入歧义。
        """
        return await self.paginate_rows(self.build_stock_query(query), query.page, query.page_size)

    async def stock_summary(self, query: StockQuery) -> dict:
        """按与列表完全一致的过滤条件做合计，保证“底部汇总”与“列表数据”口径一致。"""
        inner = self.build_stock_query(query).order_by(None).subquery()
        stmt = select(
            func.coalesce(func.sum(inner.c.qty), 0),
            func.coalesce(func.sum(inner.c.locked_qty), 0),
            func.coalesce(func.sum(inner.c.available_qty), 0),
            func.coalesce(func.sum(inner.c.stock_amount), 0),
            func.count(),
        )
        row = (await self._db.execute(stmt)).one()
        return {
            "total_qty": row[0],
            "total_locked_qty": row[1],
            "total_available_qty": row[2],
            "total_stock_amount": row[3],
            "line_count": int(row[4]),
        }

    def build_flow_query(self, query: FlowQuery) -> Select:
        """构建库存流水查询：一次 join 带出商品与仓库编码名称。

        inv_stock_flow 无 is_deleted 字段（只增不删），因此这里不加软删过滤；
        商品/仓库可能存在已被软删的历史档案，此时仍应展示流水，故用外连接。
        """
        # 流水金额 = ROUND(qty * unit_cost, 2)，仅展示口径；
        # 同样显式 CAST 为 Numeric，保证 SQLite 与 PG 下都返回 Decimal
        amount = func.cast(
            func.round(InvStockFlow.qty * InvStockFlow.unit_cost, AMOUNT_SCALE),
            Numeric(18, AMOUNT_SCALE),
        ).label("amount")
        stmt = (
            select(
                InvStockFlow.id,
                InvStockFlow.warehouse_id,
                BdWarehouse.code.label("warehouse_code"),
                BdWarehouse.name.label("warehouse_name"),
                InvStockFlow.goods_id,
                BdGoods.code.label("goods_code"),
                BdGoods.name.label("goods_name"),
                BdGoods.spec.label("goods_spec"),
                InvStockFlow.flow_type,
                InvStockFlow.direction,
                InvStockFlow.qty,
                InvStockFlow.before_qty,
                InvStockFlow.after_qty,
                InvStockFlow.unit_cost,
                amount,
                InvStockFlow.source_type,
                InvStockFlow.source_no,
                InvStockFlow.biz_time,
                InvStockFlow.operator_id,
                InvStockFlow.remark,
                InvStockFlow.created_at,
            )
            .outerjoin(
                BdGoods,
                (InvStockFlow.goods_id == BdGoods.id) & (BdGoods.tenant_id == self.tenant_id),
            )
            .outerjoin(
                BdWarehouse,
                (InvStockFlow.warehouse_id == BdWarehouse.id)
                & (BdWarehouse.tenant_id == self.tenant_id),
            )
            .where(InvStockFlow.tenant_id == self.tenant_id)
        )

        # 精确筛选
        if query.warehouse_id is not None:
            stmt = stmt.where(InvStockFlow.warehouse_id == query.warehouse_id)
        if query.goods_id is not None:
            stmt = stmt.where(InvStockFlow.goods_id == query.goods_id)
        if query.flow_type is not None:
            stmt = stmt.where(InvStockFlow.flow_type == query.flow_type)
        if query.direction is not None:
            stmt = stmt.where(InvStockFlow.direction == query.direction)
        if query.source_no:
            stmt = stmt.where(InvStockFlow.source_no == query.source_no)
        # 业务时间区间：按 biz_time 过滤，起止均含当日全天
        stmt = apply_biz_time_range(stmt, query.start_date, query.end_date)
        # 关键字：命中商品编码 / 名称，便于按商品追溯
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(or_(BdGoods.code.ilike(like), BdGoods.name.ilike(like)))

        # 按业务时间倒序 + ID 倒序：同一时刻多行时以主键兜底，保证分页稳定
        return stmt.order_by(InvStockFlow.biz_time.desc(), InvStockFlow.id.desc())

    async def list_flows(self, query: FlowQuery) -> tuple[list, int]:
        """分页查询库存流水，返回 (行列表, 总数)。

        注意：不能复用父类 paginate_rows —— 它会对 self.model（inv_stock）追加
        tenant_id 过滤，而本查询主表是 inv_stock_flow，两者无关联会造成笛卡尔积。
        因此这里显式自行分页，租户条件已在 build_flow_query 的 WHERE 中给出。
        """
        stmt = self.build_flow_query(query)
        # 总数：以相同条件语句作为子查询计数，避免手拼 SQL
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int((await self._db.execute(count_stmt)).scalar_one())
        # 分页：offset 由 page/pageSize 推导，page >= 1 已由 Schema 保证
        offset = (query.page - 1) * query.page_size
        rows = list((await self._db.execute(stmt.limit(query.page_size).offset(offset))).all())
        return rows, total


def apply_biz_time_range(
    stmt: Select,
    start_date: date | None,
    end_date: date | None,
) -> Select:
    """为流水查询追加业务时间区间条件。

    采用“左闭右开”写法：biz_time >= start_date 00:00:00 且 < end_date+1 天 00:00:00，
    避免使用 `<= end_date` 时漏掉当日 00:00 之后的记录（date 与 timestamptz 比较会截断时间）。
    """
    if start_date is not None:
        stmt = stmt.where(InvStockFlow.biz_time >= start_date)
    if end_date is not None:
        # 加 1 天实现“包含结束当日全天”，与 biz_time（timestamptz）比较时左闭右开
        stmt = stmt.where(InvStockFlow.biz_time < end_date + timedelta(days=1))
    return stmt


