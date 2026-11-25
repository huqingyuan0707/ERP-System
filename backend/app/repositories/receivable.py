"""应收数据访问（F-01）：只读查询，不写业务数据。"""

from datetime import date

from sqlalchemy import Select, or_, select

from app.models.base_data import BdCustomer
from app.models.finance import FinReceivable
from app.repositories.base import BaseRepository
from app.schemas.receivable import ReceivableQuery


class ReceivableRepository(BaseRepository[FinReceivable]):
    """封装应收列表筛选与客户名称批量查询。"""

    model = FinReceivable

    def build_query(self, query: ReceivableQuery, *, today: date) -> Select:
        """组合客户/状态/来源单号/单据日期/逾期/关键字筛选；租户与数据范围由 Service 追加。"""
        stmt = select(FinReceivable).where(FinReceivable.is_deleted.is_(False))
        if query.customer_id is not None:
            stmt = stmt.where(FinReceivable.customer_id == query.customer_id)
        if query.status is not None:
            stmt = stmt.where(FinReceivable.status == query.status)
        if query.source_no:
            stmt = stmt.where(FinReceivable.source_no.ilike(f"%{query.source_no}%"))
        if query.bill_date_start is not None:
            stmt = stmt.where(FinReceivable.bill_date >= query.bill_date_start)
        if query.bill_date_end is not None:
            stmt = stmt.where(FinReceivable.bill_date <= query.bill_date_end)
        if query.overdue is True:
            stmt = stmt.where(FinReceivable.unreceived_amount > 0, FinReceivable.due_date < today)
        elif query.overdue is False:
            stmt = stmt.where(
                or_(
                    FinReceivable.unreceived_amount <= 0,
                    FinReceivable.due_date.is_(None),
                    FinReceivable.due_date >= today,
                )
            )
        if query.unsettled is True:
            stmt = stmt.where(FinReceivable.unreceived_amount > 0)
        elif query.unsettled is False:
            stmt = stmt.where(FinReceivable.unreceived_amount <= 0)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(
                    FinReceivable.source_no.ilike(like),
                    FinReceivable.remark.ilike(like),
                )
            )
        return stmt.order_by(FinReceivable.bill_date.desc(), FinReceivable.id.desc())

    async def customer_names(self, ids: list[int]) -> dict[int, str]:
        """批量取客户名称，避免逐行查询。"""
        if not ids:
            return {}
        stmt = select(BdCustomer.id, BdCustomer.name).where(
            BdCustomer.id.in_(ids), BdCustomer.is_deleted.is_(False)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(BdCustomer.tenant_id == self.tenant_id)
        return {int(row[0]): str(row[1]) for row in (await self.db.execute(stmt)).all()}
