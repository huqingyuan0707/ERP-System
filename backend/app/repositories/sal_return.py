"""销售退货数据访问（SA-04）：单据锁、原出库单定位、明细查询与列表筛选。"""

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sal import SalReturn, SalReturnItem
from app.repositories.base import BaseRepository
from app.schemas.sal_return import SalReturnQuery


def _pg_only(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "postgresql"


class SalReturnRepository(BaseRepository[SalReturn]):
    model = SalReturn

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        if _pg_only(self.db):
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("sal_return"))))

    async def get_with_lock(self, return_id: int, *, for_update: bool = False) -> SalReturn | None:
        stmt = (
            select(SalReturn)
            .where(SalReturn.id == return_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, return_ids: list[int]) -> dict[int, list[SalReturnItem]]:
        if not return_ids:
            return {}
        stmt = (
            select(SalReturnItem)
            .select_from(SalReturnItem)
            .where(SalReturnItem.return_id.in_(return_ids))
            .order_by(SalReturnItem.id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(SalReturnItem.tenant_id == self.tenant_id)
        result: dict[int, list[SalReturnItem]] = {}
        for row in (await self.db.execute(stmt)).scalars().all():
            result.setdefault(int(row.return_id), []).append(row)
        return result

    async def soft_delete_items(self, return_id: int, user_id: int) -> None:
        await self.db.execute(
            update(SalReturnItem)
            .where(SalReturnItem.return_id == return_id, SalReturnItem.is_deleted.is_(False))
            .values(is_deleted=True, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )

    def build_query(self, query: SalReturnQuery) -> Select:
        stmt = select(SalReturn).where(SalReturn.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(SalReturn.status == query.status)
        if query.customer_id is not None:
            stmt = stmt.where(SalReturn.customer_id == query.customer_id)
        if query.warehouse_id is not None:
            stmt = stmt.where(SalReturn.warehouse_id == query.warehouse_id)
        if query.source_no is not None:
            stmt = stmt.where(SalReturn.source_no == query.source_no)
        if query.start_date is not None:
            stmt = stmt.where(SalReturn.return_date >= query.start_date)
        if query.end_date is not None:
            stmt = stmt.where(SalReturn.return_date <= query.end_date)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(or_(SalReturn.return_no.like(like), SalReturn.remark.like(like)))
        return stmt.order_by(SalReturn.return_date.desc(), SalReturn.id.desc())
