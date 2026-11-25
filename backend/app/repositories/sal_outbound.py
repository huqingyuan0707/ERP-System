"""销售出库数据访问（SA-03）：取号锁、单据锁、明细批量查询与列表筛选。"""

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sal import SalOutbound, SalOutboundItem
from app.repositories.base import BaseRepository
from app.schemas.sal_outbound import SalOutboundQuery


def _pg_only(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "postgresql"


class SalOutboundRepository(BaseRepository[SalOutbound]):
    """封装 sal_outbound / sal_outbound_item 读写。"""

    model = SalOutbound

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用事务级锁；SQLite 由唯一索引兜底。"""
        if _pg_only(self.db):
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("sal_outbound"))))

    async def get_with_lock(
        self, outbound_id: int, *, for_update: bool = False
    ) -> SalOutbound | None:
        stmt = (
            select(SalOutbound)
            .where(SalOutbound.id == outbound_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_no(self, outbound_no: str, *, for_update: bool = False) -> SalOutbound | None:
        """按出库单号查询；退货确认用 for_update 串行化同一原单。"""
        stmt = select(SalOutbound).where(SalOutbound.outbound_no == outbound_no)
        if for_update:
            stmt = stmt.with_for_update()
        stmt = stmt.execution_options(populate_existing=True)
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, outbound_ids: list[int]) -> dict[int, list[SalOutboundItem]]:
        if not outbound_ids:
            return {}
        stmt = (
            select(SalOutboundItem)
            .select_from(SalOutboundItem)
            .where(SalOutboundItem.outbound_id.in_(outbound_ids))
            .order_by(SalOutboundItem.id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(SalOutboundItem.tenant_id == self.tenant_id)
        result: dict[int, list[SalOutboundItem]] = {}
        for row in (await self.db.execute(stmt)).scalars().all():
            result.setdefault(int(row.outbound_id), []).append(row)
        return result

    async def soft_delete_items(self, outbound_id: int, user_id: int) -> None:
        await self.db.execute(
            update(SalOutboundItem)
            .where(
                SalOutboundItem.outbound_id == outbound_id,
                SalOutboundItem.is_deleted.is_(False),
            )
            .values(is_deleted=True, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )

    def build_query(self, query: SalOutboundQuery) -> Select:
        """列表筛选；调用方负责 apply_scope 叠加数据范围。"""
        stmt = select(SalOutbound).where(SalOutbound.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(SalOutbound.status == query.status)
        if query.order_id is not None:
            stmt = stmt.where(SalOutbound.order_id == query.order_id)
        if query.customer_id is not None:
            stmt = stmt.where(SalOutbound.customer_id == query.customer_id)
        if query.warehouse_id is not None:
            stmt = stmt.where(SalOutbound.warehouse_id == query.warehouse_id)
        if query.start_date is not None:
            stmt = stmt.where(SalOutbound.outbound_date >= query.start_date)
        if query.end_date is not None:
            stmt = stmt.where(SalOutbound.outbound_date <= query.end_date)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    SalOutbound.outbound_no.like(like),
                    SalOutbound.remark.like(like),
                )
            )
        return stmt.order_by(SalOutbound.outbound_date.desc(), SalOutbound.id.desc())
