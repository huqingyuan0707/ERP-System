"""采购入库数据访问（P-03）：取号锁、单号查重、列表筛选、明细批量查询。

约定（与 PurOrderRepository 保持一致）：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - PurInbound 未定义 relationship，明细用 list_items 批量 IN 查询，避免 N+1。
"""

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pur import PurInbound, PurInboundItem
from app.repositories.base import BaseRepository
from app.schemas.purchase_inbound import PurInboundQuery


def _pg_only(db: AsyncSession) -> bool:
    """当前连接是否为 PostgreSQL（advisory 锁仅 PG 支持，SQLite 测试库跳过）。"""
    return db.get_bind().dialect.name == "postgresql"


class PurInboundRepository(BaseRepository[PurInbound]):
    """封装 pur_inbound / pur_inbound_item 读写。"""

    model = PurInbound

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数直接跳过，
        由 uq_pur_inbound_no 唯一索引兜底。
        """
        if _pg_only(self.db):
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("pur_inbound"))))

    async def get_with_lock(
        self, inbound_id: int, *, for_update: bool = False
    ) -> PurInbound | None:
        """按主键取入库单；for_update=True 时加行锁，用于草稿修改/删除/确认的状态判断。

        populate_existing=True 强制从库中取最新值，避免会话缓存导致读到旧状态。
        """
        stmt = (
            select(PurInbound)
            .where(PurInbound.id == inbound_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_no(self, inbound_no: str, *, for_update: bool = False) -> PurInbound | None:
        """按入库单号查询（含软删记录口径与唯一索引一致）；退货单按 source_no 定位原单时使用。

        for_update=True 时加行锁：退货确认事务用它串行化同一原单的并发确认，
        保证「累计可退量校验 + 应付冲减」不被并发穿插。
        """
        stmt = select(PurInbound).where(PurInbound.inbound_no == inbound_no)
        if for_update:
            stmt = stmt.with_for_update()
        stmt = stmt.execution_options(populate_existing=True)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, inbound_ids: list[int]) -> dict[int, list[PurInboundItem]]:
        """按入库单 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not inbound_ids:
            return {}
        # 显式 select_from：TenantMixin 会反向引用 pur_inbound，不指定 FROM 会触发笛卡尔积告警
        stmt = (
            select(PurInboundItem)
            .select_from(PurInboundItem)
            .where(PurInboundItem.inbound_id.in_(inbound_ids))
            .order_by(PurInboundItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免引入主表
            stmt = stmt.where(PurInboundItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[PurInboundItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.inbound_id), []).append(item)
        return grouped

    def build_query(self, query: PurInboundQuery) -> Select:
        """组合状态/订单/供应商/仓库/日期区间/关键字筛选；租户过滤由分页方法追加。"""
        stmt = select(PurInbound).where(PurInbound.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(PurInbound.status == query.status)
        if query.order_id is not None:
            stmt = stmt.where(PurInbound.order_id == query.order_id)
        if query.supplier_id is not None:
            stmt = stmt.where(PurInbound.supplier_id == query.supplier_id)
        if query.warehouse_id is not None:
            stmt = stmt.where(PurInbound.warehouse_id == query.warehouse_id)
        if query.inbound_date_start is not None:
            stmt = stmt.where(PurInbound.inbound_date >= query.inbound_date_start)
        if query.inbound_date_end is not None:
            stmt = stmt.where(PurInbound.inbound_date <= query.inbound_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(PurInbound.inbound_no.ilike(like), PurInbound.remark.ilike(like))
            )
        # 单号倒序：最新入库单排在最前
        return stmt.order_by(PurInbound.inbound_no.desc(), PurInbound.id.desc())

    async def soft_delete_items(self, inbound_id: int, operator_id: int) -> None:
        """明细整单替换前的软删：UPDATE is_deleted，保留历史行便于追溯（不改历史数据）。"""
        await self.db.execute(
            update(PurInboundItem)
            .where(PurInboundItem.inbound_id == inbound_id, PurInboundItem.is_deleted.is_(False))
            .values(is_deleted=True, updated_by=operator_id)
        )
