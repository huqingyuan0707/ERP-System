"""应收管理服务（F-01）：列表/汇总/账龄/详情，全部只读。

口径来自需求条目拆分.md F-01：
  - 销售退货已由 SA-04 直接冲减原应收 amount/unreceived_amount，这里只做查询展示；
  - 未收为负表示客户应退/预收，负数不进账龄分桶，单列「应退/预收」；
  - 账龄按到期日（due_date）起算，只统计正数未结项；
  - 列表、汇总、账龄、详情共用 resolve_scope 数据范围。
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.finance import FinReceivable, FinReceiptItem
from app.models.sal import SalReturn
from app.models.sys import SysUser
from app.repositories.receivable import ReceivableRepository
from app.schemas.common import PageResult
from app.schemas.receivable import (
    ZERO,
    AgingBucketOut,
    ReceiptWriteoffRecord,
    ReceivableDetailOut,
    ReceivableOut,
    ReceivableQuery,
    ReceivableSummary,
    SalesReturnRecord,
)
from app.utils.pagination import paginate_result

AGING_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 30, "1-30天"),
    (31, 60, "31-60天"),
    (61, 90, "61-90天"),
    (91, 180, "91-180天"),
)
OVER_180 = "180天以上"
NOT_DUE = "未到期"
CREDIT = "应退/预收"


def _aging_days(due_date: date | None, today: date) -> int:
    if due_date is None or due_date >= today:
        return 0
    return (today - due_date).days


def _aging_bucket(days: int) -> str:
    for lower, upper, label in AGING_BUCKETS:
        if lower <= days <= upper:
            return label
    return OVER_180


class ReceivableService:
    """应收查询服务：不写业务数据，不修改 fin_receivable。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = ReceivableRepository(db, tenant_id=tenant_id)

    async def _visible_rows(self, user: SysUser, query: ReceivableQuery) -> list[FinReceivable]:
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query, today=date.today())
        stmt = await apply_scope(self._db, stmt, FinReceivable, ctx)
        return list((await self._db.execute(stmt)).scalars().all())

    def _to_out(self, row: FinReceivable, customer_name: str | None) -> ReceivableOut:
        unreceived = row.unreceived_amount or ZERO
        days = _aging_days(row.due_date, date.today())
        if unreceived < 0:
            bucket = CREDIT
        elif days == 0:
            bucket = NOT_DUE
        else:
            bucket = _aging_bucket(days)
        return ReceivableOut(
            id=row.id,
            customer_id=row.customer_id,
            customer_name=customer_name,
            source_type=row.source_type,
            source_no=row.source_no,
            bill_date=row.bill_date,
            due_date=row.due_date,
            amount=row.amount,
            received_amount=row.received_amount,
            unreceived_amount=unreceived,
            credit_amount=-unreceived if unreceived < 0 else ZERO,
            status=row.status,
            aging_days=days,
            aging_bucket=bucket,
            remark=row.remark,
            created_at=row.created_at,
        )

    async def list_receivables(
        self, query: ReceivableQuery, user: SysUser
    ) -> PageResult[ReceivableOut]:
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query, today=date.today())
        stmt = await apply_scope(self._db, stmt, FinReceivable, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        names = await self._repo.customer_names([int(row.customer_id) for row in rows])
        items = [self._to_out(row, names.get(int(row.customer_id))) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_summary(self, query: ReceivableQuery, user: SysUser) -> ReceivableSummary:
        rows = await self._visible_rows(user, query)
        today = date.today()
        return ReceivableSummary(
            total_amount=sum((row.amount for row in rows), ZERO),
            total_received=sum((row.received_amount for row in rows), ZERO),
            total_unreceived=sum((row.unreceived_amount for row in rows), ZERO),
            credit_amount=sum(
                (-row.unreceived_amount for row in rows if row.unreceived_amount < 0), ZERO
            ),
            overdue_amount=sum(
                (
                    row.unreceived_amount
                    for row in rows
                    if row.unreceived_amount > 0
                    and row.due_date is not None
                    and row.due_date < today
                ),
                ZERO,
            ),
            count=len(rows),
        )

    async def get_aging(self, query: ReceivableQuery, user: SysUser) -> list[AgingBucketOut]:
        rows = await self._visible_rows(user, query)
        grouped: dict[str, list[Decimal]] = defaultdict(list)
        today = date.today()
        for row in rows:
            unreceived = row.unreceived_amount or ZERO
            if unreceived < 0:
                grouped[CREDIT].append(-unreceived)
            elif unreceived > 0:
                days = _aging_days(row.due_date, today)
                grouped[NOT_DUE if days == 0 else _aging_bucket(days)].append(unreceived)
        ordered = [NOT_DUE, *(label for _, _, label in AGING_BUCKETS), OVER_180, CREDIT]
        return [
            AgingBucketOut(bucket=name, count=len(grouped[name]), amount=sum(grouped[name], ZERO))
            for name in ordered
            if name in grouped
        ]

    async def get_detail(self, receivable_id: int, user: SysUser) -> ReceivableDetailOut:
        """详情：主行 + 收款核销记录 + 冲减该应收的销售退货记录；越权 403，不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        row = await self._repo.get(receivable_id)
        if row is None:
            raise AppException(ErrorCode.NOT_FOUND, "应收单不存在")
        if not await allows(self._db, ctx, created_by=row.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该应收单")

        names = await self._repo.customer_names([int(row.customer_id)])
        base = self._to_out(row, names.get(int(row.customer_id)))
        writeoff_rows = (
            (
                await self._db.execute(
                    select(FinReceiptItem)
                    .where(
                        FinReceiptItem.receivable_id == receivable_id,
                        FinReceiptItem.tenant_id == self._tenant_id,
                    )
                    .order_by(FinReceiptItem.writeoff_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return_rows = (
            (
                await self._db.execute(
                    select(SalReturn)
                    .where(
                        SalReturn.source_no == row.source_no,
                        SalReturn.status == 1,
                        SalReturn.is_deleted.is_(False),
                        SalReturn.tenant_id == self._tenant_id,
                    )
                    .order_by(SalReturn.return_date.desc())
                )
            )
            .scalars()
            .all()
        )
        return ReceivableDetailOut(
            **base.model_dump(),
            writeoffs=[
                ReceiptWriteoffRecord(
                    receipt_id=int(item.receipt_id),
                    writeoff_amount=item.writeoff_amount,
                    writeoff_at=item.writeoff_at,
                )
                for item in writeoff_rows
            ],
            returns=[
                SalesReturnRecord(
                    id=int(item.id),
                    return_no=item.return_no,
                    return_date=item.return_date,
                    total_amount=item.total_amount,
                )
                for item in return_rows
            ],
        )
