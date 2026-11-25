"""对账单服务（F-06）：客户侧（应收）与供应商侧（应付）共用快照与明细。

口径（需求条目拆分.md:654-666）：
  - 期初 = 期间开始前未结余额，按业务日期重算，不得用当前 amount 倒推；
  - 本期 = 期间内来源单增加（入库/出库原始金额）- 付款/收款核销 - 退货冲减；
    未分配收付款不计入；
  - 期末 = 期初 + 本期；明细行余额滚动；快照不可改，同对象+期间重复生成 409。
  - 客户侧：出库=借方（应收增加），收款核销/退货=贷方；
    供应商侧：入库=借方（应付增加），付款核销/退货=贷方。
"""

from datetime import timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer, BdSupplier
from app.models.finance import (
    FinPayable,
    FinPayment,
    FinPaymentItem,
    FinReceipt,
    FinReceiptItem,
    FinStatement,
    FinStatementItem,
)
from app.models.pur import PurInbound, PurReturn
from app.models.sal import SalOutbound, SalReturn
from app.models.sys import SysUser
from app.schemas.common import PageResult
from app.schemas.statement import (
    StatementBuildOut,
    StatementCreate,
    StatementDetailOut,
    StatementOut,
    StatementQuery,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no

ZERO = Decimal("0")
PARTY_CUSTOMER = 1
PARTY_SUPPLIER = 2


class StatementService:

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def _party_name(self, party_type: int, party_id: int) -> str | None:
        model = BdCustomer if party_type == PARTY_CUSTOMER else BdSupplier
        return await self._db.scalar(
            select(model.name).where(
                model.id == party_id,
                model.tenant_id == self._tenant_id,
                model.is_deleted.is_(False),
            )
        )

    async def _events(self, user: SysUser, party_type: int, party_id: int) -> list[dict[str, Any]]:
        """按往来单位类型分流业务事件，均按当前用户数据范围过滤。"""
        if party_type == PARTY_CUSTOMER:
            return await self._customer_events(user, party_id)
        return await self._supplier_events(user, party_id)

    async def _customer_events(self, user: SysUser, customer_id: int) -> list[dict[str, Any]]:
        """客户侧事件：销售出库增加应收、收款核销与销售退货减少应收。"""
        ctx = await resolve_scope(self._db, user)
        events: list[dict[str, Any]] = []

        outbound_stmt = await apply_scope(
            self._db,
            select(SalOutbound).where(
                SalOutbound.tenant_id == self._tenant_id,
                SalOutbound.customer_id == customer_id,
                SalOutbound.is_deleted.is_(False),
                SalOutbound.status == 1,
            ),
            SalOutbound,
            ctx,
        )
        for row in (await self._db.execute(outbound_stmt)).scalars().all():
            events.append(
                {
                    "date": row.outbound_date,
                    "source_no": row.outbound_no,
                    "summary": "销售出库应收增加",
                    "debit": Decimal(str(row.total_amount)),
                    "credit": ZERO,
                    "seq": 0,
                }
            )

        receipt_stmt = await apply_scope(
            self._db,
            select(FinReceiptItem, FinReceipt)
            .join(FinReceipt, FinReceipt.id == FinReceiptItem.receipt_id)
            .where(
                FinReceipt.tenant_id == self._tenant_id,
                FinReceipt.customer_id == customer_id,
                FinReceipt.is_deleted.is_(False),
                FinReceiptItem.is_deleted.is_(False),
            ),
            FinReceipt,
            ctx,
        )
        for item, receipt in (await self._db.execute(receipt_stmt)).all():
            at = item.writeoff_at
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            events.append(
                {
                    "date": at.astimezone(timezone.utc).date(),
                    "source_no": receipt.receipt_no,
                    "summary": f"收款核销（{item.writeoff_amount}）",
                    "debit": ZERO,
                    "credit": Decimal(str(item.writeoff_amount)),
                    "seq": 1,
                }
            )

        return_stmt = await apply_scope(
            self._db,
            select(SalReturn).where(
                SalReturn.tenant_id == self._tenant_id,
                SalReturn.customer_id == customer_id,
                SalReturn.is_deleted.is_(False),
                SalReturn.status == 1,
            ),
            SalReturn,
            ctx,
        )
        for row in (await self._db.execute(return_stmt)).scalars().all():
            events.append(
                {
                    "date": row.return_date,
                    "source_no": row.return_no,
                    "summary": f"销售退货冲减（原单 {row.source_no}）",
                    "debit": ZERO,
                    "credit": Decimal(str(row.total_amount)),
                    "seq": 2,
                }
            )
        return events

    async def _supplier_events(self, user: SysUser, supplier_id: int) -> list[dict[str, Any]]:
        """供应商侧事件：采购入库增加应付、付款核销与采购退货减少应付。"""
        ctx = await resolve_scope(self._db, user)
        events: list[dict[str, Any]] = []

        inbound_stmt = await apply_scope(
            self._db,
            select(PurInbound).where(
                PurInbound.tenant_id == self._tenant_id,
                PurInbound.supplier_id == supplier_id,
                PurInbound.is_deleted.is_(False),
                PurInbound.status == 1,
            ),
            PurInbound,
            ctx,
        )
        for row in (await self._db.execute(inbound_stmt)).scalars().all():
            events.append(
                {
                    "date": row.inbound_date,
                    "source_no": row.inbound_no,
                    "summary": "采购入库应付增加",
                    "debit": Decimal(str(row.total_amount)),
                    "credit": ZERO,
                    "seq": 0,
                }
            )

        payment_stmt = await apply_scope(
            self._db,
            select(FinPaymentItem, FinPayment)
            .join(FinPayment, FinPayment.id == FinPaymentItem.payment_id)
            .join(FinPayable, FinPayable.id == FinPaymentItem.payable_id)
            .where(
                FinPayment.tenant_id == self._tenant_id,
                FinPayment.supplier_id == supplier_id,
                FinPayment.is_deleted.is_(False),
                FinPayable.is_deleted.is_(False),
                FinPaymentItem.is_deleted.is_(False),
            ),
            FinPayment,
            ctx,
        )
        for item, payment in (await self._db.execute(payment_stmt)).all():
            # 核销时间按数据库时区口径取日期；无 tzinfo 时按 UTC 处理
            at = item.writeoff_at
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            events.append(
                {
                    "date": at.astimezone(timezone.utc).date(),
                    "source_no": payment.payment_no,
                    "summary": f"付款核销（{item.writeoff_amount}）",
                    "debit": ZERO,
                    "credit": Decimal(str(item.writeoff_amount)),
                    "seq": 1,
                }
            )

        return_stmt = await apply_scope(
            self._db,
            select(PurReturn).where(
                PurReturn.tenant_id == self._tenant_id,
                PurReturn.supplier_id == supplier_id,
                PurReturn.is_deleted.is_(False),
                PurReturn.status == 1,
            ),
            PurReturn,
            ctx,
        )
        for row in (await self._db.execute(return_stmt)).scalars().all():
            events.append(
                {
                    "date": row.return_date,
                    "source_no": row.return_no,
                    "summary": f"采购退货冲减（原单 {row.source_no}）",
                    "debit": ZERO,
                    "credit": Decimal(str(row.total_amount)),
                    "seq": 2,
                }
            )
        return events

    async def build(self, user: SysUser, payload: StatementCreate) -> StatementBuildOut:
        """生成前预览：期初/本期/期末按业务日期重算，不落库。"""
        if payload.period_end < payload.period_start:
            raise AppException(ErrorCode.VALIDATION_ERROR, "对账期间结束日期不能早于开始日期")
        party_model = BdCustomer if payload.party_type == PARTY_CUSTOMER else BdSupplier
        party_label = "客户" if payload.party_type == PARTY_CUSTOMER else "供应商"
        if (
            await self._db.scalar(
                select(party_model.id).where(
                    party_model.id == payload.party_id,
                    party_model.tenant_id == self._tenant_id,
                    party_model.is_deleted.is_(False),
                )
            )
            is None
        ):
            raise AppException(ErrorCode.NOT_FOUND, f"{party_label}不存在")

        events = await self._events(user, payload.party_type, payload.party_id)
        begin = ZERO
        current = ZERO
        items: list[dict[str, Any]] = []
        for event in events:
            net = event["debit"] - event["credit"]
            if event["date"] < payload.period_start:
                begin += net
            elif event["date"] <= payload.period_end:
                current += net
                items.append(event)
        items.sort(key=lambda e: (e["date"], e["seq"], str(e["source_no"] or "")))
        balance = begin
        for item in items:
            balance += item["debit"] - item["credit"]
            item["balance"] = balance
        return StatementBuildOut(
            party_type=payload.party_type,
            party_id=payload.party_id,
            party_name=await self._party_name(payload.party_type, payload.party_id),
            period_start=payload.period_start,
            period_end=payload.period_end,
            begin_amount=begin,
            current_amount=current,
            end_amount=begin + current,
            items=[
                {
                    "bizDate": item["date"],
                    "sourceNo": item["source_no"],
                    "summary": item["summary"],
                    "debitAmount": item["debit"],
                    "creditAmount": item["credit"],
                    "balance": item["balance"],
                }
                for item in items
            ],
        )

    async def create(self, user: SysUser, payload: StatementCreate) -> StatementDetailOut:
        """落库快照：同一往来单位+期间已存在则 409；快照明细不可改。"""
        built = await self.build(user, payload)
        exists = await self._db.scalar(
            select(FinStatement.id).where(
                FinStatement.tenant_id == self._tenant_id,
                FinStatement.party_type == payload.party_type,
                FinStatement.party_id == payload.party_id,
                FinStatement.period_start == payload.period_start,
                FinStatement.period_end == payload.period_end,
                FinStatement.is_deleted.is_(False),
            )
        )
        if exists is not None:
            party_label = "客户" if payload.party_type == PARTY_CUSTOMER else "供应商"
            raise AppException(
                ErrorCode.CONFLICT, f"该{party_label}与期间已生成对账单，请查看已有快照"
            )

        statement = FinStatement(
            tenant_id=self._tenant_id,
            statement_no=await generate_bill_no(self._db, "fin_statement"),
            party_type=payload.party_type,
            party_id=payload.party_id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            begin_amount=built.begin_amount,
            current_amount=built.current_amount,
            end_amount=built.end_amount,
            status=1,
            remark=payload.remark,
            created_by=user.id,
            updated_by=user.id,
        )
        self._db.add(statement)
        await self._db.flush()
        for item in built.items:
            self._db.add(
                FinStatementItem(
                    tenant_id=self._tenant_id,
                    statement_id=statement.id,
                    biz_date=item.biz_date,
                    source_no=item.source_no,
                    summary=item.summary,
                    debit_amount=item.debit_amount,
                    credit_amount=item.credit_amount,
                    balance=item.balance,
                )
            )
        await write_oper_log(
            self._db,
            tenant_id=self._tenant_id,
            operator_id=user.id,
            action="生成对账单",
            module="财务管理",
            params={
                "statementNo": statement.statement_no,
                "partyType": payload.party_type,
                "partyId": payload.party_id,
            },
        )
        await self._db.commit()
        return await self.get(statement.id, user)

    async def _visible_statement(self, statement_id: int, user: SysUser) -> FinStatement:
        ctx = await resolve_scope(self._db, user)
        stmt = await apply_scope(
            self._db,
            select(FinStatement).where(
                FinStatement.id == statement_id,
                FinStatement.tenant_id == self._tenant_id,
                FinStatement.is_deleted.is_(False),
            ),
            FinStatement,
            ctx,
        )
        statement = (await self._db.execute(stmt)).scalar_one_or_none()
        if statement is None:
            raise AppException(ErrorCode.NOT_FOUND, "对账单不存在")
        return statement

    @staticmethod
    def _out(statement: FinStatement, party_name: str | None) -> StatementOut:
        return StatementOut(
            id=statement.id,
            statement_no=statement.statement_no,
            party_type=statement.party_type,
            party_id=statement.party_id,
            party_name=party_name,
            period_start=statement.period_start,
            period_end=statement.period_end,
            begin_amount=statement.begin_amount,
            current_amount=statement.current_amount,
            end_amount=statement.end_amount,
            status=statement.status,
            remark=statement.remark,
            created_at=statement.created_at.isoformat() if statement.created_at else None,
        )

    async def get(self, statement_id: int, user: SysUser) -> StatementDetailOut:
        statement = await self._visible_statement(statement_id, user)
        rows = (
            (
                await self._db.execute(
                    select(FinStatementItem)
                    .where(
                        FinStatementItem.statement_id == statement.id,
                        FinStatementItem.is_deleted.is_(False),
                    )
                    .order_by(FinStatementItem.biz_date, FinStatementItem.id)
                )
            )
            .scalars()
            .all()
        )
        out = self._out(statement, await self._party_name(statement.party_type, statement.party_id))
        return StatementDetailOut(
            **out.model_dump(),
            items=[
                {
                    "bizDate": row.biz_date,
                    "sourceNo": row.source_no,
                    "summary": row.summary,
                    "debitAmount": row.debit_amount,
                    "creditAmount": row.credit_amount,
                    "balance": row.balance,
                }
                for row in rows
            ],
        )

    async def list(self, query: StatementQuery, user: SysUser) -> PageResult[StatementOut]:
        ctx = await resolve_scope(self._db, user)
        stmt = await apply_scope(
            self._db,
            select(FinStatement).where(
                FinStatement.tenant_id == self._tenant_id,
                FinStatement.is_deleted.is_(False),
            ),
            FinStatement,
            ctx,
        )
        if query.party_type is not None:
            stmt = stmt.where(FinStatement.party_type == query.party_type)
        if query.party_id is not None:
            stmt = stmt.where(FinStatement.party_id == query.party_id)
        if query.period_start is not None:
            stmt = stmt.where(FinStatement.period_end >= query.period_start)
        if query.period_end is not None:
            stmt = stmt.where(FinStatement.period_start <= query.period_end)
        if query.start_date is not None:
            stmt = stmt.where(FinStatement.period_end >= query.start_date)
        if query.end_date is not None:
            stmt = stmt.where(FinStatement.period_start <= query.end_date)
        if query.statement_no:
            stmt = stmt.where(FinStatement.statement_no == query.statement_no)
        stmt = stmt.order_by(FinStatement.created_at.desc(), FinStatement.id.desc())

        total = len((await self._db.execute(stmt)).scalars().all())
        rows = (
            (
                await self._db.execute(
                    stmt.offset((query.page - 1) * query.page_size).limit(query.page_size)
                )
            )
            .scalars()
            .all()
        )
        supplier_ids = {int(row.party_id) for row in rows if row.party_type == PARTY_SUPPLIER}
        customer_ids = {int(row.party_id) for row in rows if row.party_type == PARTY_CUSTOMER}
        names: dict[int, str] = {}
        if supplier_ids:
            names.update(
                {
                    int(sid): name
                    for sid, name in (
                        await self._db.execute(
                            select(BdSupplier.id, BdSupplier.name).where(
                                BdSupplier.id.in_(supplier_ids)
                            )
                        )
                    ).all()
                }
            )
        if customer_ids:
            names.update(
                {
                    int(cid): name
                    for cid, name in (
                        await self._db.execute(
                            select(BdCustomer.id, BdCustomer.name).where(
                                BdCustomer.id.in_(customer_ids)
                            )
                        )
                    ).all()
                }
            )
        return PageResult(
            total=total,
            page=query.page,
            page_size=query.page_size,
            items=[self._out(row, names.get(int(row.party_id))) for row in rows],
        )

    async def export_rows(self, statement_id: int, user: SysUser) -> list[dict[str, Any]]:
        """供 C-07 导出任务读取快照明细。"""
        detail = await self.get(statement_id, user)
        return [item.model_dump(by_alias=True) for item in detail.items]
