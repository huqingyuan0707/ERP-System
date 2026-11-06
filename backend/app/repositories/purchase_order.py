"""采购订单数据访问（P-02）：取号锁、单号查重、列表筛选、明细批量查询。

约定（与 PurApplyRepository 保持一致）：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - PurOrder 未定义 relationship，明细用 list_items 批量 IN 查询，避免 N+1。
"""

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pur import PurOrder, PurOrderItem
from app.repositories.base import BaseRepository
from app.schemas.purchase_order import PurOrderQuery


class PurOrderRepository(BaseRepository[PurOrder]):
    """封装 pur_order / pur_order_item 读写。"""

    model = PurOrder

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数直接跳过，
        由 uq_pur_order_no 唯一索引兜底。
        """
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                select(func.pg_advisory_xact_lock(func.hashtext("pur_order")))
            )

    async def get_with_lock(self, order_id: int, *, for_update: bool = False) -> PurOrder | None:
        """按主键取订单；for_update=True 时加行锁，用于草稿修改/删除的状态判断。

        populate_existing=True 强制从库中取最新值，避免会话缓存导致读到旧状态。
        """
        stmt = (
            select(PurOrder)
            .where(PurOrder.id == order_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_no(self, order_no: str) -> PurOrder | None:
        """按单号查重（含软删记录，与唯一索引口径一致，软删单号不复用）。"""
        stmt = select(PurOrder).where(PurOrder.order_no == order_no)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, order_ids: list[int]) -> dict[int, list[PurOrderItem]]:
        """按订单 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not order_ids:
            return {}
        # 显式 select_from：TenantMixin 会反向引用 pur_order，不指定 FROM 会触发笛卡尔积告警
        stmt = (
            select(PurOrderItem)
            .select_from(PurOrderItem)
            .where(PurOrderItem.order_id.in_(order_ids))
            .order_by(PurOrderItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免引入主表
            stmt = stmt.where(PurOrderItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[PurOrderItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.order_id), []).append(item)
        return grouped

    def build_query(self, query: PurOrderQuery) -> Select:
        """组合状态/供应商/下单日期区间/来源申请单/关键字筛选；租户过滤由分页方法追加。"""
        stmt = select(PurOrder).where(PurOrder.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(PurOrder.status == query.status)
        if query.supplier_id is not None:
            stmt = stmt.where(PurOrder.supplier_id == query.supplier_id)
        if query.order_date_start is not None:
            stmt = stmt.where(PurOrder.order_date >= query.order_date_start)
        if query.order_date_end is not None:
            stmt = stmt.where(PurOrder.order_date <= query.order_date_end)
        if query.source_apply_id is not None:
            # 转单溯源：按来源申请单反查订单
            stmt = stmt.where(PurOrder.source_apply_id == query.source_apply_id)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(or_(PurOrder.order_no.ilike(like), PurOrder.remark.ilike(like)))
        # 单号倒序：最新订单排在最前
        return stmt.order_by(PurOrder.order_no.desc(), PurOrder.id.desc())

    async def soft_delete_items(self, order_id: int, operator_id: int) -> None:
        """明细整单替换前的软删：UPDATE is_deleted，保留历史行便于追溯（不改历史数据）。"""
        await self.db.execute(
            update(PurOrderItem)
            .where(PurOrderItem.order_id == order_id, PurOrderItem.is_deleted.is_(False))
            .values(is_deleted=True, updated_by=operator_id)
        )

    async def count_by_apply(self, apply_id: int) -> int:
        """统计某张申请单已生成的未删除订单数，供转单前提示「已建单」（仅提示，不拦截）。"""
        stmt = (
            select(func.count())
            .select_from(PurOrder)
            .where(PurOrder.source_apply_id == apply_id, PurOrder.is_deleted.is_(False))
        )
        stmt = self._apply_tenant_filter(stmt)
        return int((await self.db.execute(stmt)).scalar_one())

    async def purge_soft_deleted_items(self, order_id: int) -> None:
        """彻底清理已软删的明细行（仅维护场景使用，正常业务不调用）。"""
        await self.db.execute(
            delete(PurOrderItem).where(
                PurOrderItem.order_id == order_id, PurOrderItem.is_deleted.is_(True)
            )
        )
