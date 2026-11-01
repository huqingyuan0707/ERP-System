"""财务数据访问层。"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import ArApRecord, FinAccount, FinEntry, FinVoucher


class FinanceRepository:
    """财务核心表数据访问：科目、凭证、分录、应收应付。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---------- 科目 ----------

    async def get_account(self, account_id: int) -> FinAccount | None:
        return (await self._db.execute(select(FinAccount).where(FinAccount.id == account_id))).scalar_one_or_none()

    async def get_account_by_code(self, tenant_id: int, code: str) -> FinAccount | None:
        return (
            await self._db.execute(
                select(FinAccount).where(FinAccount.tenant_id == tenant_id, FinAccount.code == code)
            )
        ).scalar_one_or_none()

    async def add_account(self, account: FinAccount) -> FinAccount:
        self._db.add(account)
        await self._db.flush()
        await self._db.refresh(account)
        return account

    # ---------- 凭证 ----------

    async def add_voucher(self, voucher: FinVoucher) -> FinVoucher:
        self._db.add(voucher)
        await self._db.flush()
        await self._db.refresh(voucher)
        return voucher

    async def add_entry(self, entry: FinEntry) -> FinEntry:
        self._db.add(entry)
        await self._db.flush()
        await self._db.refresh(entry)
        return entry

    async def get_voucher(self, voucher_id: int) -> FinVoucher | None:
        return (await self._db.execute(select(FinVoucher).where(FinVoucher.id == voucher_id))).scalar_one_or_none()

    async def list_entries_by_voucher(self, voucher_id: int) -> list[FinEntry]:
        stmt = select(FinEntry).where(FinEntry.voucher_id == voucher_id)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_voucher_balance(self, voucher_id: int) -> tuple[Decimal, Decimal]:
        """返回 (总借方, 总贷方)。"""
        stmt = select(FinEntry).where(FinEntry.voucher_id == voucher_id)
        rows = (await self._db.execute(stmt)).scalars().all()
        total_debit = sum((r.debit_amount for r in rows), Decimal("0"))
        total_credit = sum((r.credit_amount for r in rows), Decimal("0"))
        return total_debit, total_credit

    # ---------- 应收应付 ----------

    async def add_ar_ap(self, record: ArApRecord) -> ArApRecord:
        self._db.add(record)
        await self._db.flush()
        await self._db.refresh(record)
        return record

    async def get_ar_ap_by_source(self, tenant_id: int, source_type: str, source_no: str) -> ArApRecord | None:
        return (
            await self._db.execute(
                select(ArApRecord).where(
                    ArApRecord.tenant_id == tenant_id,
                    ArApRecord.source_type == source_type,
                    ArApRecord.source_no == source_no,
                )
            )
        ).scalar_one_or_none()

    async def list_unreconciled(self, tenant_id: int) -> list[ArApRecord]:
        stmt = select(ArApRecord).where(
            ArApRecord.tenant_id == tenant_id,
            ArApRecord.status.in_([1, 2]),  # 未对账或部分对账
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def update_reconciled(self, record: ArApRecord, amount: Decimal) -> None:
        record.reconciled_amount += amount
        if record.reconciled_amount >= record.amount:
            record.status = 3  # 已对账
        elif record.reconciled_amount > 0:
            record.status = 2  # 部分对账
        await self._db.flush()
