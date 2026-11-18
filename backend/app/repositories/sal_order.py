"""销售订单数据访问（SAL-02/04~09）：取号锁、单号查重、列表筛选、明细与出库进度批量查询。

约定（与 PurOrderRepository / SalQuotationRepository 保持一致）：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - SalOrder 未定义 relationship，明细用 list_items 批量 IN 查询，避免 N+1；
  - 出库进度来自 sal_outbound_item 聚合（已确认出库单），一次 GROUP BY 查完。
"""

from decimal import Decimal

from sqlalchemy import Select, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdCustomer
from app.models.sal import SalOrder, SalOrderItem, SalOutbound, SalOutboundItem
from app.repositories.base import BaseRepository
from app.schemas.sal_order import SalOrderQuery


class SalOrderRepository(BaseRepository[SalOrder]):
    """封装 sal_order / sal_order_item 读写与出库进度聚合。"""

    model = SalOrder

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数直接跳过，
        由 uq_sal_order_no 唯一索引兜底。
        """
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("sal_order"))))

    async def get_with_lock(self, order_id: int, *, for_update: bool = False) -> SalOrder | None:
        """按主键取订单；for_update=True 时加行锁，用于状态机动作与草稿修改/删除。

        populate_existing=True 强制从库中取最新值，避免会话缓存导致读到旧状态。
        """
        stmt = (
            select(SalOrder)
            .where(SalOrder.id == order_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_no(self, order_no: str) -> SalOrder | None:
        """按单号查重（含软删记录，与唯一索引口径一致，软删单号不复用）。"""
        stmt = select(SalOrder).where(SalOrder.order_no == order_no)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    def build_query(self, query: SalOrderQuery) -> Select:
        """组合状态/客户/下单日期区间/关键字（订单号/备注）筛选；租户过滤由分页方法追加。

        客户名称关键字用 EXISTS 子查询匹配，不 join 主查询，
        避免影响 paginate 的 count 子查询与实体列提取。
        """
        stmt = select(SalOrder).where(SalOrder.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(SalOrder.status == query.status)
        if query.customer_id is not None:
            stmt = stmt.where(SalOrder.customer_id == query.customer_id)
        if query.order_date_start is not None:
            stmt = stmt.where(SalOrder.order_date >= query.order_date_start)
        if query.order_date_end is not None:
            stmt = stmt.where(SalOrder.order_date <= query.order_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            customer_exists = (
                select(BdCustomer.id)
                .where(
                    BdCustomer.id == SalOrder.customer_id,
                    BdCustomer.name.ilike(like),
                )
                .exists()
            )
            stmt = stmt.where(or_(SalOrder.order_no.ilike(like), exists(customer_exists)))
        # 单号倒序：最新订单排在最前，符合业务人员查看习惯
        return stmt.order_by(SalOrder.order_no.desc(), SalOrder.id.desc())

    async def list_items(self, order_ids: list[int]) -> dict[int, list[SalOrderItem]]:
        """按订单 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not order_ids:
            return {}
        # 显式 select_from：TenantMixin 会反向引用 sal_order，不指定 FROM 会触发笛卡尔积告警
        stmt = (
            select(SalOrderItem)
            .select_from(SalOrderItem)
            .where(SalOrderItem.order_id.in_(order_ids))
            .order_by(SalOrderItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免引入主表
            stmt = stmt.where(SalOrderItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[SalOrderItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.order_id), []).append(item)
        return grouped

    async def outbound_totals(self, order_ids: list[int]) -> dict[int, Decimal]:
        """按订单聚合已确认出库数量合计（SAL-09 出库进度）。

        出库明细没有 order_id，经 order_item_id 关联 sal_order_item 定位所属订单；
        只统计已确认（status=1）且未软删的出库单明细，没有出库记录的订单不在返回值里。
        草稿出库单不算进度（未确认不回写订单），与出库模块回写口径一致。
        """
        if not order_ids:
            return {}
        stmt = (
            select(SalOrderItem.order_id, func.sum(SalOutboundItem.qty))
            .select_from(SalOutboundItem)
            .join(SalOrderItem, SalOrderItem.id == SalOutboundItem.order_item_id)
            .join(SalOutbound, SalOutbound.id == SalOutboundItem.outbound_id)
            .where(
                SalOrderItem.order_id.in_(order_ids),
                SalOutbound.status == 1,
                SalOutbound.is_deleted.is_(False),
            )
            .group_by(SalOrderItem.order_id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(SalOutboundItem.tenant_id == self.tenant_id)
        return {int(row[0]): Decimal(row[1] or 0) for row in (await self.db.execute(stmt)).all()}

    async def shipped_amount(self, order_id: int) -> Decimal:
        """按订单聚合已确认出库金额合计（SAL-15 信用占用按未出库部分计算）。

        出库确认后金额即切换为应收占用，这里只取「已审未出库部分」，
        避免「应收 + 订单全额」重复计入同一笔占用。
        """
        stmt = (
            select(func.sum(SalOutbound.total_amount))
            .select_from(SalOutbound)
            .where(
                SalOutbound.order_id == order_id,
                SalOutbound.status == 1,
                SalOutbound.is_deleted.is_(False),
            )
        )
        if self.tenant_id is not None:
            stmt = stmt.where(SalOutbound.tenant_id == self.tenant_id)
        value = await self.db.scalar(stmt)
        return Decimal(value or 0)

    async def soft_delete_items(self, order_id: int, operator_id: int) -> None:
        """明细整体替换/删除单据前的软删：UPDATE is_deleted，保留历史行便于追溯。

        表名显式指定，避免 UPDATE 语句里出现隐式 FROM 产生笛卡尔积。
        """
        await self.db.execute(
            update(SalOrderItem)
            .where(
                SalOrderItem.order_id == order_id,
                SalOrderItem.is_deleted.is_(False),
            )
            .values(is_deleted=True, updated_by=operator_id)
        )
