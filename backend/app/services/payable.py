"""应付管理服务（F-02）：列表/汇总/账龄/详情，全部只读。

口径来自需求条目拆分.md F-02：
  - 退货已由 P-04 直接冲减原应付 amount/unpaid_amount，这里只做查询展示；
  - 未付为负表示供应商应退/预付，负数不进账龄分桶，单列「应退/预付」；
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
from app.models.finance import FinPayable, FinPaymentItem
from app.models.pur import PurReturn
from app.models.sys import SysUser
from app.repositories.payable import PayableRepository
from app.schemas.common import PageResult
from app.schemas.payable import (
    ZERO,
    AgingBucketOut,
    PayableDetailOut,
    PayableOut,
    PayableQuery,
    PayableSummary,
    ReturnRecord,
    WriteoffRecord,
)
from app.utils.pagination import paginate_result

# 账龄分桶：按到期日起算的逾期天数，仅正数未结项参与
AGING_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 30, "1-30天"),
    (31, 60, "31-60天"),
    (61, 90, "61-90天"),
    (91, 180, "91-180天"),
)
OVER_180 = "180天以上"
NOT_DUE = "未到期"
CREDIT = "应退/预付"


def _aging_days(due_date: date | None, today: date) -> int:
    """未到期或无到期日为 0；否则为逾期天数。"""
    if due_date is None or due_date >= today:
        return 0
    return (today - due_date).days


def _aging_bucket(days: int) -> str:
    for lower, upper, label in AGING_BUCKETS:
        if lower <= days <= upper:
            return label
    return OVER_180


class PayableService:
    """应付查询服务：不写业务数据，不修改 fin_payable。"""

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PayableRepository(db, tenant_id=tenant_id)

    async def _visible_rows(self, user: SysUser, query: PayableQuery) -> list[FinPayable]:
        """按筛选与数据范围取全部可见应付（汇总/账龄用，不受分页影响）。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query, today=date.today())
        stmt = await apply_scope(self._db, stmt, FinPayable, ctx)
        return list((await self._db.execute(stmt)).scalars().all())

    def _to_out(self, row: FinPayable, supplier_name: str | None) -> PayableOut:
        unpaid = row.unpaid_amount or ZERO
        days = _aging_days(row.due_date, date.today())
        if unpaid < 0:
            bucket = CREDIT
        elif days == 0:
            bucket = NOT_DUE
        else:
            bucket = _aging_bucket(days)
        return PayableOut(
            id=row.id,
            supplier_id=row.supplier_id,
            supplier_name=supplier_name,
            source_type=row.source_type,
            source_no=row.source_no,
            bill_date=row.bill_date,
            due_date=row.due_date,
            amount=row.amount,
            paid_amount=row.paid_amount,
            unpaid_amount=unpaid,
            credit_amount=-unpaid if unpaid < 0 else ZERO,
            status=row.status,
            aging_days=days,
            aging_bucket=bucket,
            remark=row.remark,
            created_at=row.created_at,
        )

    async def list_payables(self, query: PayableQuery, user: SysUser) -> PageResult[PayableOut]:
        """应付分页列表：筛选、租户、数据范围与汇总口径一致。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query, today=date.today())
        stmt = await apply_scope(self._db, stmt, FinPayable, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        names = await self._repo.supplier_names([int(row.supplier_id) for row in rows])
        items = [self._to_out(row, names.get(int(row.supplier_id))) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def get_summary(self, query: PayableQuery, user: SysUser) -> PayableSummary:
        """汇总卡片：金额合计、应退/预付与逾期未付。"""
        rows = await self._visible_rows(user, query)
        today = date.today()
        return PayableSummary(
            total_amount=sum((row.amount for row in rows), ZERO),
            total_paid=sum((row.paid_amount for row in rows), ZERO),
            total_unpaid=sum((row.unpaid_amount for row in rows), ZERO),
            credit_amount=sum((-row.unpaid_amount for row in rows if row.unpaid_amount < 0), ZERO),
            overdue_amount=sum(
                (
                    row.unpaid_amount
                    for row in rows
                    if row.unpaid_amount > 0 and row.due_date is not None and row.due_date < today
                ),
                ZERO,
            ),
            count=len(rows),
        )

    async def get_aging(self, query: PayableQuery, user: SysUser) -> list[AgingBucketOut]:
        """账龄分桶：只统计正数未结项，负数（应退/预付）单独列示。"""
        rows = await self._visible_rows(user, query)
        grouped: dict[str, list[Decimal]] = defaultdict(list)
        today = date.today()
        for row in rows:
            unpaid = row.unpaid_amount or ZERO
            if unpaid < 0:
                grouped[CREDIT].append(-unpaid)
            elif unpaid > 0:
                days = _aging_days(row.due_date, today)
                grouped[NOT_DUE if days == 0 else _aging_bucket(days)].append(unpaid)
        ordered = [NOT_DUE, *(label for _, _, label in AGING_BUCKETS), OVER_180, CREDIT]
        return [
            AgingBucketOut(bucket=name, count=len(grouped[name]), amount=sum(grouped[name], ZERO))
            for name in ordered
            if name in grouped
        ]

    async def get_detail(self, payable_id: int, user: SysUser) -> PayableDetailOut:
        """详情：主行 + 付款核销记录 + 冲减该应付的退货记录；越权 403，不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        row = await self._repo.get(payable_id)
        if row is None:
            raise AppException(ErrorCode.NOT_FOUND, "应付单不存在")
        if not await allows(self._db, ctx, created_by=row.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该应付单")

        names = await self._repo.supplier_names([int(row.supplier_id)])
        base = self._to_out(row, names.get(int(row.supplier_id)))
        writeoff_rows = (
            (
                await self._db.execute(
                    select(FinPaymentItem)
                    .where(
                        FinPaymentItem.payable_id == payable_id,
                        FinPaymentItem.tenant_id == self._tenant_id,
                    )
                    .order_by(FinPaymentItem.writeoff_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return_rows = (
            (
                await self._db.execute(
                    select(PurReturn)
                    .where(
                        PurReturn.source_no == row.source_no,
                        PurReturn.status == 1,
                        PurReturn.is_deleted.is_(False),
                        PurReturn.tenant_id == self._tenant_id,
                    )
                    .order_by(PurReturn.return_date.desc())
                )
            )
            .scalars()
            .all()
        )
        return PayableDetailOut(
            **base.model_dump(),
            writeoffs=[
                WriteoffRecord(
                    payment_id=int(item.payment_id),
                    writeoff_amount=item.writeoff_amount,
                    writeoff_at=item.writeoff_at,
                )
                for item in writeoff_rows
            ],
            returns=[
                ReturnRecord(
                    id=int(item.id),
                    return_no=item.return_no,
                    return_date=item.return_date,
                    total_amount=item.total_amount,
                )
                for item in return_rows
            ],
        )
