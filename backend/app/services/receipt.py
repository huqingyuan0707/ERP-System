"""收款管理业务逻辑（F-03）：收款单 CRUD、收款核销事务与操作日志。

数据闭环：
  销售出库确认 → fin_receivable 生成（SA-03）→ 新建收款单（fin_receipt，待核销，不动应收）
  → POST /finance/receipts/{id}/writeoff（同一事务）：锁定收款单与应收行 → 校验客户一致、
    单条核销 ≤ 应收未收、核销总额 ≤ 收款单未分配余额 → 写 fin_receipt_item（记录核销时间）
    → 应收 received_amount/unreceived_amount/status 重算 → 收款单 allocated_amount 累加 → 提交。
  任一步失败整体回滚；可分次核销，直到金额用完。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer
from app.models.finance import FinReceivable, FinReceipt, FinReceiptItem
from app.models.sys import SysUser
from app.repositories.receipt import ReceiptRepository
from app.schemas.common import PageResult
from app.schemas.receipt import (
    ZERO,
    ReceiptCreate,
    ReceiptItemOut,
    ReceiptOut,
    ReceiptQuery,
    ReceiptSummary,
    ReceiptUpdate,
    WriteoffCreate,
    derive_receipt_status,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result

CENT = Decimal("0.01")


class ReceiptService:
    """收款单：查询 / CRUD / 核销事务。"""

    RECEIPT_BIZ_KEY = "fin_receipt"
    OPER_MODULE = "财务管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = ReceiptRepository(db, tenant_id=tenant_id)

    async def _visible_rows(self, user: SysUser, query: ReceiptQuery) -> list[FinReceipt]:
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, FinReceipt, ctx)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_receipts(self, query: ReceiptQuery, user: SysUser) -> PageResult[ReceiptOut]:
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, FinReceipt, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_summary(self, query: ReceiptQuery, user: SysUser) -> ReceiptSummary:
        rows = await self._visible_rows(user, query)
        total_amount = sum((Decimal(str(row.amount)) for row in rows), ZERO)
        total_allocated = sum((Decimal(str(row.allocated_amount)) for row in rows), ZERO)
        return ReceiptSummary(
            total_amount=total_amount,
            total_allocated=total_allocated,
            total_unallocated=total_amount - total_allocated,
            count=len(rows),
        )

    async def get_detail(self, receipt_id: int, user: SysUser) -> ReceiptOut:
        ctx = await resolve_scope(self._db, user)
        receipt = await self._get_visible(receipt_id, ctx, for_update=False)
        return (await self._to_out_list([receipt], with_items=True))[0]

    # ---------- 收款单 CRUD ----------

    async def create_receipt(
        self, payload: ReceiptCreate, user: SysUser, *, commit: bool = True
    ) -> ReceiptOut:
        try:
            await self._assert_customer(payload.customer_id)
            amount = Decimal(str(payload.amount)).quantize(CENT)
            receipt = FinReceipt(
                receipt_no=await generate_bill_no(self._db, self.RECEIPT_BIZ_KEY),
                customer_id=payload.customer_id,
                receipt_date=payload.receipt_date,
                amount=amount,
                method=payload.method,
                account=payload.account,
                operator_id=user.id,
                allocated_amount=ZERO,
                unallocated_amount=amount,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(receipt)
            await self._write_log(user.id, "新增收款", receipt.id, receipt.receipt_no)
            created = await self._repo.get_with_lock(int(receipt.id))
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "收款单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_receipt(
        self,
        receipt_id: int,
        payload: ReceiptUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> ReceiptOut:
        """修改收款单：仅未核销可改；版本号不一致 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            receipt = await self._get_visible(receipt_id, ctx, for_update=True)
            if Decimal(str(receipt.allocated_amount)) > 0:
                raise AppException(ErrorCode.CONFLICT, "已核销的收款单不能修改")
            data = payload.model_dump(exclude_unset=True)
            if data.get("customer_id") is not None:
                await self._assert_customer(int(data["customer_id"]))
                receipt.customer_id = int(data["customer_id"])
            await self._bump_version(receipt, payload.version, user.id)
            if data.get("receipt_date") is not None:
                receipt.receipt_date = data["receipt_date"]
            if data.get("amount") is not None:
                receipt.amount = Decimal(str(data["amount"])).quantize(CENT)
            if "method" in data:
                receipt.method = data["method"]
            if "account" in data:
                receipt.account = data["account"]
            if "remark" in data:
                receipt.remark = data["remark"]
            receipt.unallocated_amount = (
                Decimal(str(receipt.amount)) - Decimal(str(receipt.allocated_amount))
            ).quantize(CENT)
            receipt.updated_by = user.id
            await self._write_log(user.id, "修改收款", receipt.id, receipt.receipt_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(int(receipt.id))
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "收款单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_receipt(self, receipt_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除收款单：仅未核销可删。"""
        try:
            ctx = await resolve_scope(self._db, user)
            receipt = await self._get_visible(receipt_id, ctx, for_update=True)
            if Decimal(str(receipt.allocated_amount)) > 0:
                raise AppException(ErrorCode.CONFLICT, "已核销的收款单不能删除")
            await self._bump_version(receipt, None, user.id)
            receipt.is_deleted = True
            receipt.updated_by = user.id
            await self._write_log(user.id, "删除收款", receipt.id, receipt.receipt_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 核销事务 ----------

    async def writeoff_receipt(
        self,
        receipt_id: int,
        payload: WriteoffCreate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> ReceiptOut:
        """收款核销：写核销明细 + 冲减应收 + 累加已核销，任一步失败整体回滚。"""
        try:
            ctx = await resolve_scope(self._db, user)
            receipt = await self._get_visible(receipt_id, ctx, for_update=True)
            amount = Decimal(str(receipt.amount))
            allocated = Decimal(str(receipt.allocated_amount))
            remaining = (amount - allocated).quantize(CENT)
            if remaining <= 0:
                raise AppException(ErrorCode.CONFLICT, "该收款单已核销完成，不能重复核销")

            total = ZERO
            receivable_updates: list[tuple[FinReceivable, Decimal]] = []
            for line in payload.items:
                receivable = await self._repo.get_receivable_for_update(line.receivable_id)
                if receivable is None:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"应收单不存在: {line.receivable_id}"
                    )
                if int(receivable.customer_id) != int(receipt.customer_id):
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"应收 {receivable.source_no} 与收款单客户不一致，不能核销",
                    )
                unreceived = Decimal(str(receivable.unreceived_amount))
                writeoff_amount = Decimal(str(line.writeoff_amount)).quantize(CENT)
                if unreceived <= 0 or writeoff_amount > unreceived:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR,
                        f"核销金额 {writeoff_amount} 超过应收 {receivable.source_no}"
                        f" 未收金额 {unreceived}",
                    )
                total = (total + writeoff_amount).quantize(CENT)
                receivable_updates.append((receivable, writeoff_amount))
            if total > remaining:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    f"核销总额 {total} 超过收款单未分配余额 {remaining}",
                )

            writeoff_at = datetime.now()
            for receivable, writeoff_amount in receivable_updates:
                self._db.add(
                    FinReceiptItem(
                        receipt_id=int(receipt.id),
                        receivable_id=int(receivable.id),
                        writeoff_amount=writeoff_amount,
                        writeoff_at=writeoff_at,
                        tenant_id=self._tenant_id,
                        created_by=user.id,
                        updated_by=user.id,
                    )
                )
                await self._settle_receivable(receivable, writeoff_amount)

            receipt.allocated_amount = (allocated + total).quantize(CENT)
            receipt.unallocated_amount = (amount - receipt.allocated_amount).quantize(CENT)
            receipt.updated_by = user.id
            await self._bump_version(receipt, None, user.id)
            await self._write_log(
                user.id,
                "收款核销",
                receipt.id,
                receipt.receipt_no,
                extra={
                    "receivableCount": len(receivable_updates),
                    "amount": str(total),
                    "unallocated": str(receipt.unallocated_amount),
                },
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(int(receipt.id))
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "收款单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _settle_receivable(self, receivable: FinReceivable, writeoff_amount: Decimal) -> None:
        """核销冲减应收：received += 核销、unreceived -= 核销，并重算状态。"""
        receivable.received_amount = Decimal(str(receivable.received_amount)) + writeoff_amount
        receivable.unreceived_amount = Decimal(str(receivable.unreceived_amount)) - writeoff_amount
        paid = Decimal(str(receivable.received_amount))
        unpaid = Decimal(str(receivable.unreceived_amount))
        if unpaid <= 0:
            receivable.status = 2
        elif paid > 0:
            receivable.status = 1
        else:
            receivable.status = 0
        await self._db.flush()

    async def _assert_customer(self, customer_id: int) -> None:
        customer = await self._db.scalar(
            select(BdCustomer).where(
                BdCustomer.id == customer_id,
                BdCustomer.tenant_id == self._tenant_id,
                BdCustomer.is_deleted.is_(False),
            )
        )
        if customer is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "客户不存在")
        if customer.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "客户已停用，不能收款")

    async def _get_visible(
        self, receipt_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> FinReceipt:
        receipt = await self._repo.get_with_lock(receipt_id, for_update=for_update)
        if receipt is None or receipt.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "收款单不存在")
        if not await allows(self._db, ctx, created_by=receipt.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该收款单")
        return receipt

    async def _bump_version(self, receipt: FinReceipt, expected: int | None, user_id: int) -> None:
        current = int(receipt.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(FinReceipt)
            .where(
                FinReceipt.id == receipt.id,
                FinReceipt.tenant_id == self._tenant_id,
                FinReceipt.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _to_out_list(self, rows: list[FinReceipt], *, with_items: bool) -> list[ReceiptOut]:
        if not rows:
            return []
        receipt_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(receipt_ids) if with_items else {}
        names = await self._repo.customer_names(list({int(row.customer_id) for row in rows}))
        source_nos = await self._repo.map_receivable_source_nos(
            {int(item.receivable_id) for items in items_map.values() for item in items}
        )
        result: list[ReceiptOut] = []
        for row in rows:
            item_outs: list[ReceiptItemOut] = []
            if with_items:
                item_outs = [
                    ReceiptItemOut(
                        id=int(item.id),
                        receivable_id=int(item.receivable_id),
                        receivable_source_no=source_nos.get(int(item.receivable_id)),
                        writeoff_amount=item.writeoff_amount,
                        writeoff_at=item.writeoff_at,
                    )
                    for item in items_map.get(int(row.id), [])
                ]
            result.append(
                ReceiptOut.model_validate(row).model_copy(
                    update={
                        "customer_name": names.get(int(row.customer_id)),
                        "status": derive_receipt_status(row.allocated_amount, row.amount),
                        "items": item_outs,
                    }
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        receipt_id: int,
        receipt_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        params: dict[str, Any] = {"receiptId": receipt_id, "receiptNo": receipt_no}
        if extra:
            params.update(extra)
        await write_oper_log(
            self._db,
            tenant_id=self._tenant_id,
            operator_id=operator_id,
            action=action,
            params=params,
            module=self.OPER_MODULE,
        )
