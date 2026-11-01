"""财务业务服务：记账凭证与应收应付对账。

核心职责：
  1. 创建记账凭证，在单个事务内写入凭头 + 分录，并校验借贷平衡；
  2. 创建应收应付记录，与采购/销售单据关联；
  3. 对账：将应收应付记录与总账分录匹配，更新对账状态。
"""

from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import AppException, ErrorCode
from app.models.finance import ArApRecord, FinAccount, FinEntry, FinVoucher
from app.repositories.finance import FinanceRepository


class FinanceService:
    """财务核心业务逻辑。"""

    def __init__(self, db, tenant_id: int) -> None:
        self._db = db
        self.tenant_id = tenant_id
        self._repo = FinanceRepository(db)

    # ---------- 科目 ----------

    async def get_or_create_account(
        self, code: str, name: str, type_: int, direction: int
    ) -> FinAccount:
        """按租户+科目编码获取或创建科目。"""
        account = await self._repo.get_account_by_code(self.tenant_id, code)
        if account is None:
            account = FinAccount(
                tenant_id=self.tenant_id,
                code=code,
                name=name,
                type=type_,
                direction=direction,
                is_leaf=True,
            )
            account = await self._repo.add_account(account)
        return account

    # ---------- 凭证记账 ----------

    async def post_voucher(
        self,
        *,
        voucher_no: str,
        voucher_date: str,
        summary: str | None,
        entries: list[dict],
        source_type: str | None = None,
        source_no: str | None = None,
        remark: str | None = None,
    ) -> FinVoucher:
        """创建记账凭证（单事务）。

        entries: 列表项为 {account_code, account_name, account_type, account_direction,
                       debit_amount, credit_amount, summary}
        要求：总借方 = 总贷方，否则拒绝记账。
        """
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        # 预计算借贷总额
        for e in entries:
            debit = Decimal(str(e.get("debit_amount", 0)))
            credit = Decimal(str(e.get("credit_amount", 0)))
            total_debit += debit
            total_credit += credit

        if total_debit != total_credit:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"凭证借贷不平衡：借方 {total_debit}，贷方 {total_credit}",
            )

        voucher = FinVoucher(
            tenant_id=self.tenant_id,
            voucher_no=voucher_no,
            voucher_date=voucher_date,
            summary=summary,
            status=3,  # 已记账
            total_debit=total_debit,
            total_credit=total_credit,
            source_type=source_type,
            source_no=source_no,
            remark=remark,
        )
        voucher = await self._repo.add_voucher(voucher)

        for e in entries:
            account = await self.get_or_create_account(
                code=e["account_code"],
                name=e.get("account_name", ""),
                type_=e.get("account_type", 5),
                direction=e.get("account_direction", 2),
            )
            entry = FinEntry(
                tenant_id=self.tenant_id,
                voucher_id=voucher.id,
                account_id=account.id,
                debit_amount=Decimal(str(e.get("debit_amount", 0))),
                credit_amount=Decimal(str(e.get("credit_amount", 0))),
                summary=e.get("summary"),
                source_type=source_type,
                source_no=source_no,
            )
            await self._repo.add_entry(entry)

            # 更新科目余额
            if e.get("account_direction") == 1:  # 借方增加
                account.balance += entry.debit_amount - entry.credit_amount
            else:  # 贷方增加
                account.balance += entry.credit_amount - entry.debit_amount

        await self._db.flush()
        return voucher

    # ---------- 应收应付 ----------

    async def create_ar_ap(
        self,
        *,
        partner_type: int,  # 1=供应商, 2=客户
        partner_id: int,
        record_type: int,  # 1=应付, 2=应收
        source_type: str,
        source_no: str,
        amount: Decimal,
        biz_date: str,
        due_date: str | None = None,
        remark: str | None = None,
    ) -> ArApRecord:
        """创建应收应付记录，同一来源单号幂等（唯一约束）。"""
        existing = await self._repo.get_ar_ap_by_source(self.tenant_id, source_type, source_no)
        if existing is not None:
            return existing

        record = ArApRecord(
            tenant_id=self.tenant_id,
            partner_type=partner_type,
            partner_id=partner_id,
            record_type=record_type,
            source_type=source_type,
            source_no=source_no,
            amount=amount,
            status=1,  # 未对账
            biz_date=biz_date,
            due_date=due_date,
            remark=remark,
        )
        return await self._repo.add_ar_ap(record)

    # ---------- 对账 ----------

    async def reconcile_ar_ap(self, record_id: int, matched_amount: Decimal) -> ArApRecord:
        """对账：将应收应付记录与已匹配的金额进行对账。

        matched_amount：本次对账金额，累加至已对账金额。
        达到总金额时标记为已对账。
        """
        record = (await self._db.execute(
            select(ArApRecord).where(ArApRecord.id == record_id)
        )).scalar_one_or_none()
        if record is None:
            raise AppException(ErrorCode.NOT_FOUND, "应收应付记录不存在")

        await self._repo.update_reconciled(record, matched_amount)
        await self._db.flush()
        return record

    async def list_unreconciled(self) -> list[ArApRecord]:
        """列出当前租户未完全对账的应收应付记录。"""
        return await self._repo.list_unreconciled(self.tenant_id)
