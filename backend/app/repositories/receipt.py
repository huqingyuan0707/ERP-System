"""收款数据访问（F-03）：列表筛选、行锁、核销明细批量查询与客户名称批量查询。"""

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdCustomer
from app.models.finance import FinReceivable, FinReceipt, FinReceiptItem
from app.repositories.base import BaseRepository
from app.schemas.receipt import ReceiptQuery, ReceiptStatus


class ReceiptRepository(BaseRepository[FinReceipt]):
    model = FinReceipt

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def get_with_lock(
        self, receipt_id: int, *, for_update: bool = False
    ) -> FinReceipt | None:
        stmt = (
            select(FinReceipt)
            .where(FinReceipt.id == receipt_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_receivable_for_update(self, receivable_id: int) -> FinReceivable | None:
        """按主键定位应收并加行锁，供核销前校验与扣减。"""
        stmt = (
            select(FinReceivable)
            .where(
                FinReceivable.id == receivable_id,
                FinReceivable.tenant_id == self.tenant_id,
                FinReceivable.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, receipt_ids: list[int]) -> dict[int, list[FinReceiptItem]]:
        if not receipt_ids:
            return {}
        stmt = (
            select(FinReceiptItem)
            .select_from(FinReceiptItem)
            .where(FinReceiptItem.receipt_id.in_(receipt_ids))
            .order_by(FinReceiptItem.id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(FinReceiptItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[FinReceiptItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.receipt_id), []).append(item)
        return grouped

    async def map_receivable_source_nos(self, receivable_ids: set[int]) -> dict[int, str]:
        if not receivable_ids:
            return {}
        stmt = select(FinReceivable.id, FinReceivable.source_no).where(
            FinReceivable.id.in_(receivable_ids),
            FinReceivable.tenant_id == self.tenant_id,
        )
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): str(row[1]) for row in rows}

    def build_query(self, query: ReceiptQuery) -> Select:
        stmt = select(FinReceipt).where(FinReceipt.is_deleted.is_(False))
        if query.customer_id is not None:
            stmt = stmt.where(FinReceipt.customer_id == query.customer_id)
        if query.status == ReceiptStatus.WAIT:
            stmt = stmt.where(FinReceipt.allocated_amount <= 0)
        elif query.status == ReceiptStatus.PARTIAL:
            stmt = stmt.where(
                FinReceipt.allocated_amount > 0,
                FinReceipt.allocated_amount < FinReceipt.amount,
            )
        elif query.status == ReceiptStatus.DONE:
            stmt = stmt.where(FinReceipt.allocated_amount >= FinReceipt.amount)
        if query.receipt_date_start is not None:
            stmt = stmt.where(FinReceipt.receipt_date >= query.receipt_date_start)
        if query.receipt_date_end is not None:
            stmt = stmt.where(FinReceipt.receipt_date <= query.receipt_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(or_(FinReceipt.receipt_no.ilike(like), FinReceipt.remark.ilike(like)))
        return stmt.order_by(FinReceipt.receipt_date.desc(), FinReceipt.id.desc())

    async def customer_names(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        stmt = select(BdCustomer.id, BdCustomer.name).where(
            BdCustomer.id.in_(ids), BdCustomer.is_deleted.is_(False)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(BdCustomer.tenant_id == self.tenant_id)
        return {int(row[0]): str(row[1]) for row in (await self.db.execute(stmt)).all()}
