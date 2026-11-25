"""销售出库业务逻辑（SA-03）：按订单预填、草稿锁库存、确认扣账+回写+应收。

数据闭环：
  已审/部分出库销售订单 → 预填剩余数量 → POST /sales/outbounds 生成草稿
  → 草稿保存即锁库存（INV-05）→ 确认（同一事务）：
    unlock 草稿锁定 → change_stock(sale_out, direction=-1) → apply_outbound 回写订单
    → 生成 fin_receivable（金额=出库单合计，到期日=出库日+客户 credit_days）→ 提交。
  草稿修改/删除同步调整锁定；任一步失败整体回滚。

敏感点：
  1. 确认前按数据范围取单据并加行锁；重复确认 409。
  2. 乐观锁用「WHERE version = 当前值」条件 UPDATE + rowcount 判定。
  3. 库存写入统一走 InventoryService（行锁 + 移动加权平均 + 流水同事务）。
  4. 应收生成独立方法，便于测试模拟失败验证回滚；唯一索引兜底幂等。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer, BdGoods, BdUnit, BdWarehouse
from app.models.finance import FinReceivable
from app.models.sal import SalOrder, SalOrderItem, SalOutbound, SalOutboundItem
from app.models.sys import SysUser
from app.repositories.sal_outbound import SalOutboundRepository
from app.schemas.common import PageResult
from app.schemas.inventory import ChangeStockIn, UnlockStockIn
from app.schemas.sal_order import (
    OUTBOUNDABLE_STATUSES,
    SalOrderOutboundApply,
    SalOrderOutboundLine,
)
from app.schemas.sal_outbound import (
    SalOutboundCreate,
    SalOutboundFromOrderItem,
    SalOutboundFromOrderOut,
    SalOutboundItemIn,
    SalOutboundItemOut,
    SalOutboundOut,
    SalOutboundQuery,
    SalOutboundStatus,
    SalOutboundUpdate,
)
from app.services.audit import write_oper_log
from app.services.inventory import InventoryService
from app.services.sal_order import SalOrderService
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result

CENT = Decimal("0.01")
ZERO = Decimal("0")


class SalOutboundService:
    """销售出库：查询 / 按订单预填 / 草稿 CRUD（锁库存）/ 确认事务。"""

    OUTBOUND_BIZ_KEY = "sal_outbound"
    OPER_MODULE = "销售管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = SalOutboundRepository(db, tenant_id=tenant_id)
        self._order_service = SalOrderService(db, tenant_id)
        self._inv_service = InventoryService(db, tenant_id)

    # ---------- 按订单预填（只读） ----------

    async def build_from_order(self, order_id: int, user: SysUser) -> SalOutboundFromOrderOut:
        """按已审/部分出库订单组装出库预填数据；只读，不锁库存、不落库。"""
        ctx = await resolve_scope(self._db, user)
        order = await self._order_service._repo.get_with_lock(order_id, for_update=False)
        if order is None or order.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
        if not await allows(self._db, ctx, created_by=order.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售订单")
        if order.status not in OUTBOUNDABLE_STATUSES:
            raise AppException(ErrorCode.CONFLICT, "只有已审或部分出库的销售订单可以生成出库单")

        items_map = await self._order_service._repo.list_items([int(order.id)])
        active_items = [item for item in items_map.get(int(order.id), []) if not item.is_deleted]
        goods_map = await self._load_goods({int(item.goods_id) for item in active_items})
        unit_names = await self._load_unit_names({int(item.unit_id) for item in active_items})

        rows: list[SalOutboundFromOrderItem] = []
        for item in active_items:
            remaining = Decimal(str(item.qty)) - Decimal(str(item.outbound_qty))
            if remaining <= 0:
                continue
            goods = goods_map.get(int(item.goods_id))
            rows.append(
                SalOutboundFromOrderItem(
                    order_item_id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=unit_names.get(int(item.unit_id)),
                    qty=item.qty,
                    outbound_qty=item.outbound_qty,
                    remaining_qty=remaining,
                    price=item.price,
                    tax_rate=item.tax_rate,
                )
            )
        customer_name = await self._load_customer_name(int(order.customer_id))
        return SalOutboundFromOrderOut(
            order_id=order.id,
            order_no=order.order_no,
            customer_id=order.customer_id,
            customer_name=customer_name,
            outbound_date=date.today(),
            remark=f"由销售订单 {order.order_no} 生成出库单",
            items=rows,
        )

    # ---------- 查询 ----------

    async def list_outbounds(
        self, query: SalOutboundQuery, user: SysUser
    ) -> PageResult[SalOutboundOut]:
        ctx = await resolve_scope(self._db, user)
        stmt = await apply_scope(self._db, self._repo.build_query(query), SalOutbound, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_outbound(self, outbound_id: int, user: SysUser) -> SalOutboundOut:
        ctx = await resolve_scope(self._db, user)
        outbound = await self._get_visible(outbound_id, ctx, for_update=False)
        return (await self._to_out_list([outbound], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除（锁库存） ----------

    async def create_outbound(
        self, payload: SalOutboundCreate, user: SysUser, *, commit: bool = True
    ) -> SalOutboundOut:
        """新建出库（草稿）：单据校验 + 锁定库存，任一失败整体回滚。"""
        try:
            order = await self._assert_order(payload.order_id)
            await self._assert_warehouse(payload.warehouse_id)
            lines = await self._resolve_lines(order, payload.items, check_remaining=True)
            await self._repo.lock_for_write()
            outbound_no = await generate_bill_no(self._db, self.OUTBOUND_BIZ_KEY)
            total_amount, tax_amount = self._calc_totals(lines)
            outbound = SalOutbound(
                outbound_no=outbound_no,
                customer_id=int(order.customer_id),
                warehouse_id=payload.warehouse_id,
                order_id=int(order.id),
                outbound_date=payload.outbound_date,
                status=SalOutboundStatus.DRAFT,
                total_amount=total_amount,
                tax_amount=tax_amount,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(outbound)
            await self._replace_items(outbound.id, lines, user.id)
            # 草稿保存即锁库存
            await self._lock_lines(outbound, lines, user.id)
            await self._write_log(user.id, "新增销售出库", outbound.id, outbound.outbound_no)
            created = await self._repo.get_with_lock(outbound.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "销售出库单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_outbound(
        self, outbound_id: int, payload: SalOutboundUpdate, user: SysUser, *, commit: bool = True
    ) -> SalOutboundOut:
        """修改草稿：仅草稿可改；明细/仓库变更时先解旧锁再上新锁。"""
        try:
            ctx = await resolve_scope(self._db, user)
            outbound = await self._get_visible(outbound_id, ctx, for_update=True)
            if outbound.status != SalOutboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售出库单可以修改")
            data = payload.model_dump(exclude_unset=True)
            old_lines = await self._active_outbound_items(int(outbound.id))
            new_lines: list[tuple[SalOutboundItemIn, SalOrderItem]] | None = None
            if data.get("warehouse_id") is not None:
                await self._assert_warehouse(data["warehouse_id"])
            if payload.items is not None:
                order = await self._assert_order(int(outbound.order_id))
                new_lines = await self._resolve_lines(order, payload.items, check_remaining=True)
            # 先抢占版本号，再调整库存（失败整体回滚）
            await self._bump_version(outbound, payload.version, user.id)

            # 释放旧锁：仓库或明细变化时按「旧仓库 + 旧明细」全量解锁
            if data.get("warehouse_id") is not None or new_lines is not None:
                await self._unlock_lines(outbound, old_lines, user.id)
            if data.get("warehouse_id") is not None:
                outbound.warehouse_id = data["warehouse_id"]
            if data.get("outbound_date") is not None:
                outbound.outbound_date = data["outbound_date"]
            if "remark" in data:
                outbound.remark = data["remark"]
            if new_lines is not None:
                await self._replace_items(outbound.id, new_lines, user.id)
                outbound.total_amount, outbound.tax_amount = self._calc_totals(new_lines)
                await self._lock_lines(outbound, new_lines, user.id)
            elif data.get("warehouse_id") is not None:
                # 仅换仓库：按新仓库重新锁定同样的商品与数量
                await self._lock_lines(outbound, old_lines, user.id)
            outbound.updated_by = user.id
            await self._write_log(user.id, "修改销售出库", outbound.id, outbound.outbound_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(outbound.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售出库单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_outbound(
        self, outbound_id: int, user: SysUser, *, commit: bool = True
    ) -> None:
        """删除草稿：释放锁定 + 软删单据与明细。"""
        try:
            ctx = await resolve_scope(self._db, user)
            outbound = await self._get_visible(outbound_id, ctx, for_update=True)
            if outbound.status != SalOutboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售出库单可以删除")
            await self._bump_version(outbound, None, user.id)
            old_lines = await self._active_outbound_items(int(outbound.id))
            await self._unlock_lines(outbound, old_lines, user.id)
            outbound.is_deleted = True
            outbound.updated_by = user.id
            await self._repo.soft_delete_items(outbound.id, user.id)
            await self._write_log(user.id, "删除销售出库", outbound.id, outbound.outbound_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 确认事务 ----------

    async def confirm_outbound(
        self, outbound_id: int, user: SysUser, *, commit: bool = True
    ) -> SalOutboundOut:
        """确认出库：释放锁定 + 扣账面库存 + 订单回写 + 生成应收，同一事务。"""
        try:
            ctx = await resolve_scope(self._db, user)
            outbound = await self._get_visible(outbound_id, ctx, for_update=True)
            if outbound.status != SalOutboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "该销售出库单已确认，不能重复确认")
            await self._bump_version(outbound, None, user.id)

            order = await self._assert_order(int(outbound.order_id))
            lines = await self._active_outbound_items(int(outbound.id))
            if not lines:
                raise AppException(ErrorCode.VALIDATION_ERROR, "出库单没有明细行，不能确认")

            # 预校验剩余可出库量（权威校验在 apply_outbound 内再次执行）
            order_items = await self._load_order_items(int(order.id))
            for line in lines:
                order_item = order_items.get(int(line.order_item_id))
                if order_item is None:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"来源订单明细不存在: {line.order_item_id}"
                    )
                remaining = Decimal(str(order_item.qty)) - Decimal(str(order_item.outbound_qty))
                qty = Decimal(str(line.qty))
                if qty > remaining:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"出库数量 {qty} 超过剩余未出库数量 {remaining}（商品 ID {order_item.goods_id}）",
                    )

            # 1) 释放草稿锁定；2) 扣账面库存（sale_out，-1，检查 available_qty）
            await self._unlock_lines(outbound, lines, user.id)
            biz_time = datetime.combine(outbound.outbound_date, datetime.now().time())
            for line in lines:
                order_item = order_items[int(line.order_item_id)]
                await self._inv_service.change_stock(
                    ChangeStockIn(
                        warehouse_id=int(outbound.warehouse_id),
                        goods_id=int(line.goods_id),
                        direction=-1,
                        qty=Decimal(str(line.qty)),
                        unit_cost=Decimal(str(order_item.price)),
                        biz_type="sale_out",
                        source_no=outbound.outbound_no,
                        biz_time=biz_time,
                        remark=f"销售出库 {outbound.outbound_no}",
                    ),
                    commit=False,
                )

            # 3) 订单回写；4) 生成应收
            await self._order_service.apply_outbound(
                int(order.id),
                SalOrderOutboundApply(
                    lines=[
                        SalOrderOutboundLine(
                            order_item_id=int(line.order_item_id), qty=Decimal(str(line.qty))
                        )
                        for line in lines
                    ]
                ),
                user,
                commit=False,
            )
            await self._generate_receivable(outbound)

            outbound.status = SalOutboundStatus.CONFIRMED
            outbound.updated_by = user.id
            await self._write_log(user.id, "确认销售出库", outbound.id, outbound.outbound_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(outbound.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售出库单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _generate_receivable(self, outbound: SalOutbound) -> None:
        """按出库单合计生成应收：到期日 = 出库日 + 客户 credit_days。"""
        customer = await self._db.scalar(
            select(BdCustomer).where(
                BdCustomer.id == outbound.customer_id,
                BdCustomer.tenant_id == self._tenant_id,
                BdCustomer.is_deleted.is_(False),
            )
        )
        if customer is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "客户不存在，无法生成应收")
        credit_days = int(customer.credit_days or 0)
        amount = Decimal(str(outbound.total_amount))
        unreceived = amount
        self._db.add(
            FinReceivable(
                customer_id=int(outbound.customer_id),
                source_type="sales_outbound",
                source_no=outbound.outbound_no,
                bill_date=outbound.outbound_date,
                due_date=outbound.outbound_date + timedelta(days=credit_days),
                amount=amount,
                received_amount=ZERO,
                unreceived_amount=unreceived,
                status=0 if unreceived > 0 else 2,
                remark=None,
                tenant_id=self._tenant_id,
                created_by=outbound.created_by,
                updated_by=outbound.created_by,
            )
        )
        await self._db.flush()

    async def _get_visible(
        self, outbound_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> SalOutbound:
        outbound = await self._repo.get_with_lock(outbound_id, for_update=for_update)
        if outbound is None or outbound.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售出库单不存在")
        if not await allows(self._db, ctx, created_by=outbound.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售出库单")
        return outbound

    async def _bump_version(
        self, outbound: SalOutbound, expected: int | None, user_id: int
    ) -> None:
        current = int(outbound.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(SalOutbound)
            .where(
                SalOutbound.id == outbound.id,
                SalOutbound.tenant_id == self._tenant_id,
                SalOutbound.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _assert_order(self, order_id: int) -> SalOrder:
        order = await self._db.scalar(
            select(SalOrder).where(
                SalOrder.id == order_id,
                SalOrder.tenant_id == self._tenant_id,
                SalOrder.is_deleted.is_(False),
            )
        )
        if order is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "来源销售订单不存在")
        if order.status not in OUTBOUNDABLE_STATUSES:
            raise AppException(ErrorCode.CONFLICT, "销售订单当前状态不允许出库（需已审或部分出库）")
        return order

    async def _assert_warehouse(self, warehouse_id: int) -> None:
        warehouse = await self._db.scalar(
            select(BdWarehouse.id).where(
                BdWarehouse.id == warehouse_id,
                BdWarehouse.tenant_id == self._tenant_id,
                BdWarehouse.is_deleted.is_(False),
                BdWarehouse.status == 1,
            )
        )
        if warehouse is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "出库仓库不存在或已停用")

    async def _load_order_items(self, order_id: int) -> dict[int, SalOrderItem]:
        items = await self._order_service._active_items(order_id)
        return {int(item.id): item for item in items}

    async def _resolve_lines(
        self,
        order: SalOrder,
        payload_items: list[SalOutboundItemIn],
        *,
        check_remaining: bool,
    ) -> list[tuple[SalOutboundItemIn, SalOrderItem]]:
        """按订单明细行解析单价/商品；可选校验剩余可出库量。"""
        order_items = await self._load_order_items(int(order.id))
        resolved: list[tuple[SalOutboundItemIn, SalOrderItem]] = []
        for row in payload_items:
            item = order_items.get(row.order_item_id)
            if item is None:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"来源订单明细不存在: {row.order_item_id}"
                )
            if check_remaining:
                remaining = Decimal(str(item.qty)) - Decimal(str(item.outbound_qty))
                if row.qty > remaining:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"出库数量 {row.qty} 超过剩余未出库数量 {remaining}（商品 ID {item.goods_id}）",
                    )
            resolved.append((row, item))
        return resolved

    @staticmethod
    def _calc_totals(
        lines: list[tuple[SalOutboundItemIn, SalOrderItem]],
    ) -> tuple[Decimal, Decimal]:
        total = ZERO
        tax = ZERO
        for row, item in lines:
            amount = (Decimal(str(row.qty)) * Decimal(str(item.price))).quantize(CENT)
            total += amount
            tax += (amount - amount / (1 + Decimal(str(item.tax_rate)))).quantize(CENT)
        return total.quantize(CENT), tax.quantize(CENT)

    async def _replace_items(
        self,
        outbound_id: int,
        lines: list[tuple[SalOutboundItemIn, SalOrderItem]],
        user_id: int,
    ) -> None:
        await self._repo.soft_delete_items(outbound_id, user_id)
        for row, item in lines:
            self._db.add(
                SalOutboundItem(
                    outbound_id=outbound_id,
                    order_item_id=int(item.id),
                    goods_id=int(item.goods_id),
                    qty=Decimal(str(row.qty)),
                    price=Decimal(str(item.price)),
                    amount=(Decimal(str(row.qty)) * Decimal(str(item.price))).quantize(CENT),
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
        await self._db.flush()

    async def _active_outbound_items(self, outbound_id: int) -> list[SalOutboundItem]:
        items_map = await self._repo.list_items([int(outbound_id)])
        return [item for item in items_map.get(int(outbound_id), []) if not item.is_deleted]

    async def _lock_lines(
        self,
        outbound: SalOutbound,
        lines: list[tuple[SalOutboundItemIn, SalOrderItem]] | list[SalOutboundItem],
        user_id: int,
    ) -> None:
        """按明细锁定库存；两种入参形态统一取 (goods_id, qty)。"""
        pairs: list[tuple[int, Decimal]] = []
        for obj in lines:
            if isinstance(obj, tuple):
                row, item = obj
                pairs.append((int(item.goods_id), Decimal(str(row.qty))))
            else:
                pairs.append((int(obj.goods_id), Decimal(str(obj.qty))))
        biz_time = datetime.combine(outbound.outbound_date, datetime.now().time())
        for goods_id, qty in pairs:
            from app.schemas.inventory import LockStockIn

            await self._inv_service.lock_stock(
                LockStockIn(
                    warehouse_id=int(outbound.warehouse_id),
                    goods_id=goods_id,
                    qty=qty,
                    source_type="sal_outbound",
                    source_no=outbound.outbound_no,
                    biz_time=biz_time,
                    remark=f"销售出库草稿锁定 {outbound.outbound_no}",
                ),
                commit=False,
            )

    async def _unlock_lines(
        self, outbound: SalOutbound, lines: list[SalOutboundItem], user_id: int
    ) -> None:
        """释放草稿锁定的库存（按旧仓库 / 旧明细）。"""
        biz_time = datetime.combine(outbound.outbound_date, datetime.now().time())
        for line in lines:
            await self._inv_service.unlock_stock(
                UnlockStockIn(
                    warehouse_id=int(outbound.warehouse_id),
                    goods_id=int(line.goods_id),
                    qty=Decimal(str(line.qty)),
                    source_type="sal_outbound",
                    source_no=outbound.outbound_no,
                    biz_time=biz_time,
                    remark=f"释放销售出库草稿锁定 {outbound.outbound_no}",
                ),
                commit=False,
            )

    async def _load_goods(self, goods_ids: set[int]) -> dict[int, BdGoods]:
        if not goods_ids:
            return {}
        rows = (
            (
                await self._db.execute(
                    select(BdGoods).where(
                        BdGoods.id.in_(goods_ids), BdGoods.tenant_id == self._tenant_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {int(row.id): row for row in rows}

    async def _load_unit_names(self, unit_ids: set[int]) -> dict[int, str]:
        if not unit_ids:
            return {}
        rows = (
            await self._db.execute(
                select(BdUnit.id, BdUnit.name).where(
                    BdUnit.id.in_(unit_ids), BdUnit.tenant_id == self._tenant_id
                )
            )
        ).all()
        return {int(row[0]): str(row[1]) for row in rows}

    async def _load_customer_name(self, customer_id: int) -> str | None:
        return await self._db.scalar(
            select(BdCustomer.name).where(
                BdCustomer.id == customer_id, BdCustomer.tenant_id == self._tenant_id
            )
        )

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        outbound_id: int,
        outbound_no: str,
    ) -> None:
        await write_oper_log(
            self._db,
            tenant_id=self._tenant_id,
            operator_id=operator_id,
            action=action,
            params={"outboundId": outbound_id, "outboundNo": outbound_no},
            module=self.OPER_MODULE,
        )

    async def _to_out_list(
        self, rows: list[SalOutbound], *, with_items: bool
    ) -> list[SalOutboundOut]:
        if not rows:
            return []
        ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(ids) if with_items else {}
        customer_ids = {int(row.customer_id) for row in rows}
        warehouse_ids = {int(row.warehouse_id) for row in rows}
        order_ids = {int(row.order_id) for row in rows}
        goods_ids: set[int] = set()
        unit_ids: set[int] = set()
        for items in items_map.values():
            for item in items:
                if item.is_deleted:
                    continue
                goods_ids.add(int(item.goods_id))
        goods_map = await self._load_goods(goods_ids)

        names: dict[str, dict[int, str]] = {}
        for label, model, ids_set in (
            ("customer", BdCustomer, customer_ids),
            ("warehouse", BdWarehouse, warehouse_ids),
        ):
            if ids_set:
                result = (
                    await self._db.execute(
                        select(model.id, model.name).where(
                            model.id.in_(ids_set), model.tenant_id == self._tenant_id
                        )
                    )
                ).all()
                names[label] = {int(r[0]): str(r[1]) for r in result}
        order_nos: dict[int, str] = {}
        if order_ids:
            result = (
                await self._db.execute(
                    select(SalOrder.id, SalOrder.order_no).where(
                        SalOrder.id.in_(order_ids),
                        SalOrder.tenant_id == self._tenant_id,
                    )
                )
            ).all()
            order_nos = {int(r[0]): str(r[1]) for r in result}

        result_out: list[SalOutboundOut] = []
        for row in rows:
            item_outs: list[SalOutboundItemOut] = []
            if with_items:
                for item in items_map.get(int(row.id), []):
                    if item.is_deleted:
                        continue
                    goods = goods_map.get(int(item.goods_id))
                    item_outs.append(
                        SalOutboundItemOut(
                            id=item.id,
                            order_item_id=item.order_item_id,
                            goods_id=item.goods_id,
                            goods_code=goods.code if goods else None,
                            goods_name=goods.name if goods else None,
                            spec=goods.spec if goods else None,
                            qty=item.qty,
                            price=item.price,
                            amount=item.amount,
                        )
                    )
            result_out.append(
                SalOutboundOut(
                    id=row.id,
                    outbound_no=row.outbound_no,
                    customer_id=row.customer_id,
                    customer_name=names.get("customer", {}).get(int(row.customer_id)),
                    warehouse_id=row.warehouse_id,
                    warehouse_name=names.get("warehouse", {}).get(int(row.warehouse_id)),
                    order_id=row.order_id,
                    order_no=order_nos.get(int(row.order_id)),
                    outbound_date=row.outbound_date,
                    status=row.status,
                    total_amount=row.total_amount,
                    tax_amount=row.tax_amount,
                    remark=row.remark,
                    version=row.version,
                    items=item_outs,
                )
            )
        return result_out
