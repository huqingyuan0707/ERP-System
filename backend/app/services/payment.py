"""付款管理业务逻辑（F-04）：付款单 CRUD、付款核销事务与操作日志。

数据闭环：
  采购入库确认 → fin_payable 生成（P-03）→ 新建付款单（fin_payment，待核销，不动应付）
  → POST /finance/payments/{id}/writeoff（同一事务）：锁定付款单与应付行 → 校验供应商一致、
    单条核销 ≤ 应付未付、核销总额 ≤ 付款单未分配余额 → 写 fin_payment_item（记录核销时间）
    → 应付 paid_amount/unpaid_amount/status 重算 → 付款单 allocated_amount 累加 → 提交。
  任一步失败整体回滚；可分次核销，直到金额用完。

敏感点（事务 / 锁 / 状态机）：
  1. 核销前对付款单行加锁（get_with_lock for_update），同一付款单的并发核销在此串行化；
  2. 每条应付行也加锁（get_payable_for_update）后读取并冲减，避免丢失更新；
     CHECK 约束 ck_payable_paid_le_amount 兜底，但 422/409 必须在写库前判定；
  3. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定（与 P-02/P-04 一致）；
  4. fin_payment 无状态列，核销进度（待核销/部分核销/已核销）由 allocated_amount 推导。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdSupplier
from app.models.finance import FinPayable, FinPayment, FinPaymentItem
from app.models.sys import SysUser
from app.repositories.payment import PaymentRepository
from app.schemas.payment import (
    PaymentCreate,
    PaymentItemOut,
    PaymentOut,
    PaymentQuery,
    PaymentSummary,
    PaymentUpdate,
    WriteoffCreate,
    derive_payment_status,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result


# 金额统一精度：ROUND_HALF_EVEN（银行家舍入），禁止改为 ROUND_HALF_UP
CENT = Decimal("0.01")
ZERO = Decimal("0")


class PaymentService:
    """付款单：查询 / 草稿 CRUD / 核销事务。"""

    # 单号规则键：sys_config.billno.fin_payment = "FK,4"（迁移 a5c9d3e7f1b2 已种子化）
    PAYMENT_BIZ_KEY = "fin_payment"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "财务管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PaymentRepository(db, tenant_id=tenant_id)

    # ---------- 查询 ----------

    async def _visible_rows(self, user: SysUser, query: PaymentQuery) -> list[FinPayment]:
        """按筛选与数据范围取全部可见付款单（汇总用，不受分页影响）。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, FinPayment, ctx)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_payments(self, query: PaymentQuery, user: SysUser) -> Any:
        """分页查询付款单：先叠加数据范围，再按供应商/核销进度/日期/关键字筛选。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        # fin_payment 无 dept_id 列，apply_scope 按创建人所属部门子树过滤
        stmt = await apply_scope(self._db, stmt, FinPayment, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_summary(self, query: PaymentQuery, user: SysUser) -> PaymentSummary:
        """汇总卡片：付款总额、已核销、未分配与笔数（与列表同筛选、同数据范围）。"""
        rows = await self._visible_rows(user, query)
        total_amount = sum((Decimal(str(row.amount)) for row in rows), ZERO)
        total_allocated = sum((Decimal(str(row.allocated_amount)) for row in rows), ZERO)
        return PaymentSummary(
            total_amount=total_amount,
            total_allocated=total_allocated,
            total_unallocated=total_amount - total_allocated,
            count=len(rows),
        )

    async def get_detail(self, payment_id: int, user: SysUser) -> PaymentOut:
        """付款单详情（含核销明细）：必须在当前用户数据范围内，否则 403；不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        payment = await self._get_visible(payment_id, ctx, for_update=False)
        return (await self._to_out_list([payment], with_items=True))[0]

    # ---------- 付款单新增 / 修改 / 删除 ----------

    async def create_payment(
        self, payload: PaymentCreate, user: SysUser, *, commit: bool = True
    ) -> PaymentOut:
        """新建付款单：单号自动生成，核销进度初始为待核销，不改变应付。"""
        try:
            await self._assert_supplier(payload.supplier_id)
            amount = Decimal(str(payload.amount)).quantize(CENT)
            payment = FinPayment(
                payment_no=await generate_bill_no(self._db, self.PAYMENT_BIZ_KEY),
                supplier_id=payload.supplier_id,
                payment_date=payload.payment_date,
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
            await self._repo.add(payment)
            await self._write_log(user.id, "新增付款", payment.id, payment.payment_no)
            created = await self._repo.get_with_lock(int(payment.id))
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "付款单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_payment(
        self,
        payment_id: int,
        payload: PaymentUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PaymentOut:
        """修改付款单：仅未核销可改（已核销/部分核销 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            payment = await self._get_visible(payment_id, ctx, for_update=True)
            if Decimal(str(payment.allocated_amount)) > 0:
                raise AppException(ErrorCode.CONFLICT, "已核销的付款单不能修改")
            data = payload.model_dump(exclude_unset=True)
            if data.get("supplier_id") is not None:
                await self._assert_supplier(int(data["supplier_id"]))
                payment.supplier_id = int(data["supplier_id"])
            # 先抢占版本号再动数据：并发修改时后到者在写库之前就被拒绝
            await self._bump_version(payment, payload.version, user.id)
            if data.get("payment_date") is not None:
                payment.payment_date = data["payment_date"]
            if data.get("amount") is not None:
                payment.amount = Decimal(str(data["amount"])).quantize(CENT)
            if "method" in data:
                payment.method = data["method"]
            if "account" in data:
                payment.account = data["account"]
            if "remark" in data:
                payment.remark = data["remark"]
            # 未分配 = amount - allocated，付款单未核销时 allocated 恒为 0
            payment.unallocated_amount = (
                Decimal(str(payment.amount)) - Decimal(str(payment.allocated_amount))
            ).quantize(CENT)
            payment.updated_by = user.id
            await self._write_log(user.id, "修改付款", payment.id, payment.payment_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(int(payment.id))
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "付款单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_payment(self, payment_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除付款单：仅未核销可删（软删）；已产生核销明细的 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            payment = await self._get_visible(payment_id, ctx, for_update=True)
            if Decimal(str(payment.allocated_amount)) > 0:
                raise AppException(ErrorCode.CONFLICT, "已核销的付款单不能删除")
            await self._bump_version(payment, None, user.id)
            payment.is_deleted = True
            payment.updated_by = user.id
            await self._write_log(user.id, "删除付款", payment.id, payment.payment_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 核销事务 ----------

    async def writeoff_payment(
        self,
        payment_id: int,
        payload: WriteoffCreate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PaymentOut:
        """付款核销：写核销明细 + 冲减应付 + 累加已核销，任一步失败整体回滚。

        事务步骤（全部在同一数据库事务内）：
          1. 锁定付款单行，校验未分配余额 > 0（已核销完成 409）；
          2. 逐条锁定应付行并校验：供应商一致（跨供应商 409）、
             单条核销 ≤ 应付未付（超未付或应付已结清 422）；
          3. 核销总额 ≤ 付款单未分配余额（超出 422）；
          4. 写 fin_payment_item（记录核销时间）；
          5. 应付 paid_amount += 核销、unpaid_amount -= 核销并重算状态（与 P-04 冲减同规则）；
          6. 付款单 allocated_amount 累加、unallocated 重算，抢占版本号，写操作日志。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            payment = await self._get_visible(payment_id, ctx, for_update=True)
            amount = Decimal(str(payment.amount))
            allocated = Decimal(str(payment.allocated_amount))
            remaining = (amount - allocated).quantize(CENT)
            if remaining <= 0:
                raise AppException(ErrorCode.CONFLICT, "该付款单已核销完成，不能重复核销")

            total = ZERO
            payable_updates: list[tuple[FinPayable, Decimal]] = []
            for line in payload.items:
                payable = await self._repo.get_payable_for_update(line.payable_id)
                if payable is None:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"应付单不存在: {line.payable_id}"
                    )
                if int(payable.supplier_id) != int(payment.supplier_id):
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"应付 {payable.source_no} 与付款单供应商不一致，不能核销",
                    )
                unpaid = Decimal(str(payable.unpaid_amount))
                writeoff_amount = Decimal(str(line.writeoff_amount)).quantize(CENT)
                if unpaid <= 0 or writeoff_amount > unpaid:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR,
                        f"核销金额 {writeoff_amount} 超过应付 {payable.source_no}"
                        f" 未付金额 {unpaid}",
                    )
                total = (total + writeoff_amount).quantize(CENT)
                payable_updates.append((payable, writeoff_amount))
            if total > remaining:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    f"核销总额 {total} 超过付款单未分配余额 {remaining}",
                )

            writeoff_at = datetime.now()
            for payable, writeoff_amount in payable_updates:
                self._db.add(
                    FinPaymentItem(
                        payment_id=int(payment.id),
                        payable_id=int(payable.id),
                        writeoff_amount=writeoff_amount,
                        writeoff_at=writeoff_at,
                        tenant_id=self._tenant_id,
                        created_by=user.id,
                        updated_by=user.id,
                    )
                )
                await self._settle_payable(payable, writeoff_amount)

            payment.allocated_amount = (allocated + total).quantize(CENT)
            payment.unallocated_amount = (amount - payment.allocated_amount).quantize(CENT)
            payment.updated_by = user.id
            await self._bump_version(payment, None, user.id)
            await self._write_log(
                user.id,
                "付款核销",
                payment.id,
                payment.payment_no,
                extra={
                    "payableCount": len(payable_updates),
                    "amount": str(total),
                    "unallocated": str(payment.unallocated_amount),
                },
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(int(payment.id))
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "付款单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _settle_payable(self, payable: FinPayable, writeoff_amount: Decimal) -> None:
        """核销冲减应付：paid_amount += 核销、unpaid_amount -= 核销，并重算状态。

        应付行已在核销事务内加锁，此处直接改写不会丢失更新；
        状态规则与采购退货冲减（purchase_return._reduce_payable）完全一致：
        unpaid <= 0 → 2 已付 / paid > 0 → 1 部分付 / 其余 0 未付。
        """
        payable.paid_amount = Decimal(str(payable.paid_amount)) + writeoff_amount
        payable.unpaid_amount = Decimal(str(payable.unpaid_amount)) - writeoff_amount
        paid = Decimal(str(payable.paid_amount))
        unpaid = Decimal(str(payable.unpaid_amount))
        if unpaid <= 0:
            payable.status = 2
        elif paid > 0:
            payable.status = 1
        else:
            payable.status = 0
        await self._db.flush()

    async def _assert_supplier(self, supplier_id: int) -> None:
        """校验付款供应商存在且启用。"""
        supplier = await self._db.scalar(
            select(BdSupplier).where(
                BdSupplier.id == supplier_id,
                BdSupplier.tenant_id == self._tenant_id,
                BdSupplier.is_deleted.is_(False),
            )
        )
        if supplier is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "供应商不存在")
        if supplier.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "供应商已停用，不能付款")

    async def _get_visible(
        self, payment_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> FinPayment:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。"""
        payment = await self._repo.get_with_lock(payment_id, for_update=for_update)
        if payment is None or payment.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "付款单不存在")
        if not await allows(self._db, ctx, created_by=payment.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该付款单")
        return payment

    async def _bump_version(
        self, payment: FinPayment, expected: int | None, user_id: int
    ) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409「数据已被他人修改」。

        SQLite 无 SELECT ... FOR UPDATE，只比较内存 version 拦不住并发；
        synchronize_session=False 避免 ORM 对象被同步成新版本后回写覆盖。
        """
        current = int(payment.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(FinPayment)
            .where(
                FinPayment.id == payment.id,
                FinPayment.tenant_id == self._tenant_id,
                FinPayment.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _to_out_list(
        self, rows: list[FinPayment], *, with_items: bool
    ) -> list[PaymentOut]:
        """批量转换出参：供应商名称、核销明细与被核销应付单号一次查完，避免 N+1。"""
        if not rows:
            return []
        payment_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(payment_ids) if with_items else {}

        names = await self._repo.supplier_names(list({int(row.supplier_id) for row in rows}))
        payable_source_nos = await self._repo.map_payable_source_nos(
            {
                int(item.payable_id)
                for items in items_map.values()
                for item in items
            }
        )

        result: list[PaymentOut] = []
        for row in rows:
            item_outs: list[PaymentItemOut] = []
            if with_items:
                item_outs = [
                    PaymentItemOut(
                        id=int(item.id),
                        payable_id=int(item.payable_id),
                        payable_source_no=payable_source_nos.get(int(item.payable_id)),
                        writeoff_amount=item.writeoff_amount,
                        writeoff_at=item.writeoff_at,
                    )
                    for item in items_map.get(int(row.id), [])
                ]
            result.append(
                PaymentOut.model_validate(row).model_copy(
                    update={
                        "supplier_name": names.get(int(row.supplier_id)),
                        "status": derive_payment_status(row.allocated_amount, row.amount),
                        "items": item_outs,
                    }
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        payment_id: int,
        payment_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。"""
        params: dict[str, Any] = {"paymentId": payment_id, "paymentNo": payment_no}
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
