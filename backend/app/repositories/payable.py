"""应付数据访问（F-02）：只读查询，不写业务数据。"""

from datetime import date

from sqlalchemy import Select, or_, select

from app.models.base_data import BdSupplier
from app.models.finance import FinPayable
from app.repositories.base import BaseRepository
from app.schemas.payable import PayableQuery


class PayableRepository(BaseRepository[FinPayable]):
    """封装应付列表筛选与供应商名称批量查询。"""

    model = FinPayable

    def build_query(self, query: PayableQuery, *, today: date) -> Select:
        """组合供应商/状态/来源单号/单据日期/逾期/关键字筛选；租户与数据范围由 Service 追加。"""
        stmt = select(FinPayable).where(FinPayable.is_deleted.is_(False))
        if query.supplier_id is not None:
            stmt = stmt.where(FinPayable.supplier_id == query.supplier_id)
        if query.status is not None:
            stmt = stmt.where(FinPayable.status == query.status)
        if query.source_no:
            stmt = stmt.where(FinPayable.source_no.ilike(f"%{query.source_no}%"))
        if query.bill_date_start is not None:
            stmt = stmt.where(FinPayable.bill_date >= query.bill_date_start)
        if query.bill_date_end is not None:
            stmt = stmt.where(FinPayable.bill_date <= query.bill_date_end)
        if query.overdue is True:
            stmt = stmt.where(FinPayable.unpaid_amount > 0, FinPayable.due_date < today)
        elif query.overdue is False:
            stmt = stmt.where(
                or_(
                    FinPayable.unpaid_amount <= 0,
                    FinPayable.due_date.is_(None),
                    FinPayable.due_date >= today,
                )
            )
        if query.unsettled is True:
            stmt = stmt.where(FinPayable.unpaid_amount > 0)
        elif query.unsettled is False:
            stmt = stmt.where(FinPayable.unpaid_amount <= 0)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    FinPayable.source_no.ilike(like),
                    FinPayable.remark.ilike(like),
                )
            )
        return stmt.order_by(FinPayable.bill_date.desc(), FinPayable.id.desc())

    async def supplier_names(self, ids: list[int]) -> dict[int, str]:
        """批量取供应商名称，避免逐行查询。"""
        if not ids:
            return {}
        stmt = select(BdSupplier.id, BdSupplier.name).where(
            BdSupplier.id.in_(ids), BdSupplier.is_deleted.is_(False)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(BdSupplier.tenant_id == self.tenant_id)
        return {int(row[0]): str(row[1]) for row in (await self.db.execute(stmt)).all()}
