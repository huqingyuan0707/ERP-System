"""库存数据访问层（含行级锁）。"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InvStock, InvStockFlow


class InventoryRepository:
    """库存数据访问（不含业务规则，仅做带锁查询与写入）。

    所有查询均按 tenant_id 做行级数据隔离，防止跨租户越权读取库存。
    """

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self.tenant_id = tenant_id

    async def get_stock_for_update(
        self, warehouse_id: int, goods_id: int
    ) -> InvStock | None:
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
        )
        return await self._db.scalar(stmt)

    async def create_stock(
        self, warehouse_id: int, goods_id: int
    ) -> InvStock:
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
