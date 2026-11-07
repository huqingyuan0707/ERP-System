"""采购入库业务逻辑（P-03）：草稿 CRUD、按订单预填、确认事务与操作日志。

数据闭环：
  已审/部分入库采购订单 → 预填接口带出剩余数量 → POST /purchase/inbounds 生成草稿
  → 确认（同一事务）：写 inv_stock/inv_stock_flow（purchase_in, direction=1）
    → 调用 PurOrderService.apply_inbound 回写 pur_order_item.inbound_qty 并重算订单状态
    → 生成 fin_payable（金额=入库单合计，到期日=入库日+供应商 credit_days）→ 提交。
  任一步失败整体回滚，保证库存、订单、应付三账一致。

敏感点（事务 / 锁 / 状态机）：
  1. 确认前按数据范围取单据并对主表加行锁；重复确认返回 409。
  2. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定（与 P-02 一致）。
  3. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False。
  4. 库存写入统一走 InventoryService.change_stock（行锁 + 移动加权平均 + 流水同事务）；
     订单回写统一走 PurOrderService.apply_inbound（默认 commit=False，由本事务统一提交）。
  5. 应付生成拆成独立方法 _generate_payable，便于测试模拟「应付写入失败」验证回滚。
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdSupplier, BdUnit, BdWarehouse
from app.models.finance import FinPayable
from app.models.pur import PurInbound, PurInboundItem, PurOrder, PurOrderItem
from app.models.sys import SysUser
from app.repositories.purchase_inbound import PurInboundRepository
from app.schemas.common import PageResult
from app.schemas.inventory import ChangeStockIn
from app.schemas.purchase_inbound import (
    PurInboundCreate,
    PurInboundFromOrderItem,
    PurInboundFromOrderOut,
    PurInboundItemIn,
    PurInboundItemOut,
    PurInboundOut,
    PurInboundQuery,
    PurInboundStatus,
    PurInboundUpdate,
)
from app.schemas.purchase_order import (
    INBOUNDABLE_STATUSES,
    PurOrderInboundApply,
    PurOrderInboundLine,
)
from app.services.audit import write_oper_log
from app.services.inventory import InventoryService
from app.services.purchase_order import PurchaseOrderService
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result


# 金额与数量统一精度：ROUND_HALF_EVEN（银行家舍入），禁止改为 ROUND_HALF_UP
CENT = Decimal("0.01")
ZERO = Decimal("0")


class PurchaseInboundService:
    """采购入库：查询 / 按订单预填 / 草稿 CRUD / 确认事务。"""

    # 单号规则键：sys_config.billno.pur_inbound = "PI,4"
    INBOUND_BIZ_KEY = "pur_inbound"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "采购管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PurInboundRepository(db, tenant_id=tenant_id)
        self._order_service = PurchaseOrderService(db, tenant_id)
        self._inv_service = InventoryService(db, tenant_id)

    # ---------- 按订单预填（只读） ----------

    async def build_from_order(self, order_id: int, user: SysUser) -> PurInboundFromOrderOut:
        """按已审/部分入库订单组装入库预填数据，供前端填充新建入库单表单。

        校验链：订单存在且未软删（404）→ 数据范围允许（403）→ 状态可入库（409）。
        只读：不修改订单、不生成入库单、不落库；剩余数量为 0 的明细行不带出。
        """
        ctx = await resolve_scope(self._db, user)
        order = await self._order_service._repo.get_with_lock(order_id, for_update=False)
        if order is None or order.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
        if not await allows(self._db, ctx, created_by=order.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购订单")
        if order.status not in INBOUNDABLE_STATUSES:
            raise AppException(ErrorCode.CONFLICT, "只有已审或部分入库的采购订单可以生成入库单")

        items_map = await self._order_service._repo.list_items([int(order.id)])
        active_items = [item for item in items_map.get(int(order.id), []) if not item.is_deleted]
        goods_map = await self._load_goods({int(item.goods_id) for item in active_items})
        unit_names = await self._load_unit_names(
            {int(item.unit_id) for item in active_items}
        )

        from_rows: list[PurInboundFromOrderItem] = []
        for item in active_items:
            remaining = Decimal(str(item.qty)) - Decimal(str(item.inbound_qty))
            if remaining <= 0:
                # 已入满的明细行不再带出，避免采购员误操作
                continue
            goods = goods_map.get(int(item.goods_id))
            from_rows.append(
                PurInboundFromOrderItem(
                    order_item_id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=unit_names.get(int(item.unit_id)),
                    qty=item.qty,
                    inbound_qty=item.inbound_qty,
                    remaining_qty=remaining,
                    price=item.price,
                    tax_rate=item.tax_rate,
                )
            )

        supplier_name = await self._load_supplier_name(order.supplier_id)
        return PurInboundFromOrderOut(
            order_id=order.id,
            order_no=order.order_no,
            supplier_id=order.supplier_id,
            supplier_name=supplier_name,
            inbound_date=date.today(),
            remark=f"由采购订单 {order.order_no} 生成入库单",
            items=from_rows,
        )

    # ---------- 查询 ----------

    async def list_inbounds(
        self, query: PurInboundQuery, user: SysUser
    ) -> PageResult[PurInboundOut]:
        """分页查询入库单：先叠加数据范围，再按状态/订单/供应商/仓库/日期筛选。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        # pur_inbound 无 dept_id 列，apply_scope 按创建人所属部门子树过滤
        stmt = await apply_scope(self._db, stmt, PurInbound, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_inbound(self, inbound_id: int, user: SysUser) -> PurInboundOut:
        """入库单详情（含明细）：必须在当前用户数据范围内，否则 403；不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        inbound = await self._get_visible(inbound_id, ctx, for_update=False)
        return (await self._to_out_list([inbound], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_inbound(
        self, payload: PurInboundCreate, user: SysUser, *, commit: bool = True
    ) -> PurInboundOut:
        """新建采购入库（落库即草稿）：单号自动生成，单价/金额按来源订单明细重算。"""
        try:
            order = await self._assert_order(payload.order_id)
            await self._assert_warehouse(payload.warehouse_id)
            lines = await self._resolve_lines(order, payload.items, check_remaining=True)
            # 取号与单号查重共用事务锁，避免并发生成重复单号
            await self._repo.lock_for_write()
            inbound_no = await generate_bill_no(self._db, self.INBOUND_BIZ_KEY)
            total_amount, tax_amount = self._calc_totals(lines)
            inbound = PurInbound(
                inbound_no=inbound_no,
                supplier_id=int(order.supplier_id),
                warehouse_id=payload.warehouse_id,
                order_id=int(order.id),
                inbound_date=payload.inbound_date,
                status=PurInboundStatus.DRAFT,
                total_amount=total_amount,
                tax_amount=tax_amount,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(inbound)
            await self._replace_items(inbound.id, lines, user.id)
            await self._write_log(user.id, "新增采购入库", inbound.id, inbound.inbound_no)
            created = await self._repo.get_with_lock(inbound.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "采购入库单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_inbound(
        self,
        inbound_id: int,
        payload: PurInboundUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurInboundOut:
        """修改草稿：仅草稿可改（其余状态 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            inbound = await self._get_visible(inbound_id, ctx, for_update=True)
            if inbound.status != PurInboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购入库单可以修改")
            data = payload.model_dump(exclude_unset=True)
            lines: list[tuple[PurInboundItemIn, PurOrderItem]] | None = None
            if data.get("warehouse_id") is not None:
                await self._assert_warehouse(data["warehouse_id"])
            if payload.items is not None:
                order = await self._assert_order(int(inbound.order_id))
                lines = await self._resolve_lines(order, payload.items, check_remaining=True)
            # 先抢占版本号再动数据：并发修改时后到者在改明细之前就被拒绝
            await self._bump_version(inbound, payload.version, user.id)
            if data.get("warehouse_id") is not None:
                inbound.warehouse_id = data["warehouse_id"]
            if data.get("inbound_date") is not None:
                inbound.inbound_date = data["inbound_date"]
            if "remark" in data:
                inbound.remark = data["remark"]
            if lines is not None:
                await self._replace_items(inbound.id, lines, user.id)
                # 明细变了必须重算合计，避免主表金额与明细不一致
                inbound.total_amount, inbound.tax_amount = self._calc_totals(lines)
            inbound.updated_by = user.id
            await self._write_log(user.id, "修改采购入库", inbound.id, inbound.inbound_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(inbound.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购入库单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_inbound(self, inbound_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除草稿：仅草稿可删，软删主表并同步软删明细，历史流水保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            inbound = await self._get_visible(inbound_id, ctx, for_update=True)
            if inbound.status != PurInboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购入库单可以删除")
            await self._bump_version(inbound, None, user.id)
            inbound.is_deleted = True
            inbound.updated_by = user.id
            await self._repo.soft_delete_items(inbound.id, user.id)
            await self._write_log(user.id, "删除采购入库", inbound.id, inbound.inbound_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 确认事务 ----------

    async def confirm_inbound(
        self, inbound_id: int, user: SysUser, *, commit: bool = True
    ) -> PurInboundOut:
        """确认入库：库存增加 + 流水 + 订单回写 + 生成应付，任一步失败整体回滚。

        事务步骤（全部在同一数据库事务内）：
          1. 锁定入库单行，校验状态为草稿（重复确认 409）并抢占版本号；
          2. 预校验来源订单可入库、各明细行数量 ≤ 剩余未入库数量（409，先于任何写入）；
          3. 逐行调用 InventoryService.change_stock：行锁 inv_stock → 增加 qty →
             移动加权平均重算 avg_cost → 写 inv_stock_flow（purchase_in, direction=1）；
          4. 调用 PurOrderService.apply_inbound：累加 inbound_qty、重算订单状态；
          5. _generate_payable：按入库单合计生成 fin_payable（幂等唯一索引兜底）；
          6. 入库单置为已确认，写操作日志。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            inbound = await self._get_visible(inbound_id, ctx, for_update=True)
            if inbound.status != PurInboundStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "该采购入库单已确认，不能重复确认")
            await self._bump_version(inbound, None, user.id)

            order = await self._assert_order(int(inbound.order_id))
            items_map = await self._repo.list_items([int(inbound.id)])
            active_items = [
                item for item in items_map.get(int(inbound.id), []) if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "入库单没有明细行，不能确认")
            # 预校验数量 ≤ 剩余（权威校验在 apply_inbound 内再次执行，双保险）
            order_items = await self._load_order_items(int(order.id))
            for line in active_items:
                order_item = order_items.get(int(line.order_item_id))
                if order_item is None:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"来源订单明细不存在: {line.order_item_id}"
                    )
                remaining = Decimal(str(order_item.qty)) - Decimal(str(order_item.inbound_qty))
                qty = Decimal(str(line.qty))
                if qty > remaining:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"入库数量 {qty} 超过剩余未入库数量 {remaining}"
                        f"（商品 ID {order_item.goods_id}）",
                    )

            # 库存写入：行锁 + 移动加权平均 + 流水；任一行失败抛异常，整体回滚
            biz_time = datetime.combine(inbound.inbound_date, datetime.now().time())
            for line in active_items:
                order_item = order_items[int(line.order_item_id)]
                await self._inv_service.change_stock(
                    ChangeStockIn(
                        warehouse_id=int(inbound.warehouse_id),
                        goods_id=int(line.goods_id),
                        direction=1,
                        qty=Decimal(str(line.qty)),
                        unit_cost=Decimal(str(order_item.price)),
                        biz_type="purchase_in",
                        source_no=inbound.inbound_no,
                        biz_time=biz_time,
                        remark=f"采购入库 {inbound.inbound_no}",
                    ),
                    commit=False,
                )

            # 订单回写：累加 inbound_qty、重算订单状态（内部行锁订单行）
            await self._order_service.apply_inbound(
                int(order.id),
                PurOrderInboundApply(
                    lines=[
                        PurOrderInboundLine(
                            order_item_id=int(line.order_item_id), qty=Decimal(str(line.qty))
                        )
                        for line in active_items
                    ]
                ),
                user,
                commit=False,
            )

            # 生成应付（独立方法便于测试模拟失败验证回滚）
            await self._generate_payable(inbound)

            inbound.status = PurInboundStatus.CONFIRMED
            inbound.updated_by = user.id
            await self._write_log(user.id, "确认采购入库", inbound.id, inbound.inbound_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(inbound.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购入库单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _generate_payable(self, inbound: PurInbound) -> None:
        """按入库单合计生成应付：到期日 = 入库日 + 供应商 credit_days。

        独立成方法：测试通过 monkeypatch 让它抛异常，验证库存与订单回写一并回滚。
        幂等由 uq_payable_source（tenant + source_type + source_no，未删行）兜底，
        并发/重试确认不会生成重复应付。
        """
        supplier = await self._db.scalar(
            select(BdSupplier).where(
                BdSupplier.id == inbound.supplier_id,
                BdSupplier.tenant_id == self._tenant_id,
                BdSupplier.is_deleted.is_(False),
            )
        )
        if supplier is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "供应商不存在，无法生成应付")
        credit_days = int(supplier.credit_days or 0)
        amount = Decimal(str(inbound.total_amount))
        paid = ZERO
        unpaid = amount - paid
        self._db.add(
            FinPayable(
                supplier_id=int(inbound.supplier_id),
                source_type="purchase_inbound",
                source_no=inbound.inbound_no,
                bill_date=inbound.inbound_date,
                due_date=inbound.inbound_date + timedelta(days=credit_days),
                amount=amount,
                paid_amount=paid,
                unpaid_amount=unpaid,
                # 0 未付 / 1 部分付 / 2 已付：入库生成时未付款；零金额视为无需支付
                status=2 if unpaid <= 0 else 0,
                remark=None,
                tenant_id=self._tenant_id,
                created_by=inbound.created_by,
                updated_by=inbound.created_by,
            )
        )
        await self._db.flush()

    async def _get_visible(
        self, inbound_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> PurInbound:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。"""
        inbound = await self._repo.get_with_lock(inbound_id, for_update=for_update)
        if inbound is None or inbound.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购入库单不存在")
        if not await allows(self._db, ctx, created_by=inbound.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购入库单")
        return inbound

    async def _bump_version(
        self, inbound: PurInbound, expected: int | None, user_id: int
    ) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409「数据已被他人修改」。

        SQLite 无 SELECT ... FOR UPDATE，只比较内存 version 拦不住并发；
        synchronize_session=False 避免 ORM 对象被同步成新版本后回写覆盖。
        """
        current = int(inbound.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(PurInbound)
            .where(
                PurInbound.id == inbound.id,
                PurInbound.tenant_id == self._tenant_id,
                PurInbound.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _assert_order(self, order_id: int) -> PurOrder:
        """校验来源订单：存在、未删除、状态可入库（已审/部分入库）。"""
        order = await self._db.scalar(
            select(PurOrder).where(
                PurOrder.id == order_id,
                PurOrder.tenant_id == self._tenant_id,
                PurOrder.is_deleted.is_(False),
            )
        )
        if order is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "来源采购订单不存在")
        if order.status not in INBOUNDABLE_STATUSES:
            raise AppException(
                ErrorCode.CONFLICT, "采购订单当前状态不允许入库（需已审或部分入库）"
            )
        return order

    async def _assert_warehouse(self, warehouse_id: int) -> None:
        """校验仓库存在且启用，避免把库存写到停用仓库。"""
        warehouse = await self._db.scalar(
            select(BdWarehouse).where(
                BdWarehouse.id == warehouse_id,
                BdWarehouse.tenant_id == self._tenant_id,
                BdWarehouse.is_deleted.is_(False),
            )
        )
        if warehouse is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库不存在")
        if warehouse.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库已停用，不能入库")

    async def _load_order_items(self, order_id: int) -> dict[int, PurOrderItem]:
        """加载来源订单的有效明细行（排除软删），按 ID 建索引。"""
        rows = (
            await self._db.execute(
                select(PurOrderItem).where(
                    PurOrderItem.order_id == order_id,
                    PurOrderItem.tenant_id == self._tenant_id,
                    PurOrderItem.is_deleted.is_(False),
                )
            )
        ).scalars()
        return {int(item.id): item for item in rows.all()}

    async def _resolve_lines(
        self, order: PurOrder, items: list[PurInboundItemIn], *, check_remaining: bool
    ) -> list[tuple[PurInboundItemIn, PurOrderItem]]:
        """校验并解析明细：订单明细行必须属于来源订单，数量 ≤ 剩余可入库。"""
        order_items = await self._load_order_items(int(order.id))
        lines: list[tuple[PurInboundItemIn, PurOrderItem]] = []
        for item in items:
            order_item = order_items.get(item.order_item_id)
            if order_item is None:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    f"订单明细不存在或不属于该订单: {item.order_item_id}",
                )
            if check_remaining:
                remaining = Decimal(str(order_item.qty)) - Decimal(str(order_item.inbound_qty))
                if item.qty > remaining:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"入库数量 {item.qty} 超过剩余未入库数量 {remaining}"
                        f"（商品 ID {order_item.goods_id}）",
                    )
            lines.append((item, order_item))
        return lines

    @staticmethod
    def _calc_totals(lines: list[tuple[PurInboundItemIn, PurOrderItem]]) -> tuple[Decimal, Decimal]:
        """按含税口径重算合计与税额：行金额 round(qty*price,2)，税额 Σ(amount-amount/(1+rate))。

        单价取来源订单明细的含税单价；口径与 P-02 采购订单完全一致。
        """
        total = ZERO
        tax = ZERO
        for line, order_item in lines:
            price = Decimal(str(order_item.price))
            tax_rate = Decimal(str(order_item.tax_rate))
            amount = (line.qty * price).quantize(CENT)
            total += amount
            if tax_rate > 0:
                tax += amount - (amount / (Decimal("1") + tax_rate)).quantize(CENT)
        return total, tax

    async def _replace_items(
        self, inbound_id: int, lines: list[tuple[PurInboundItemIn, PurOrderItem]], operator_id: int
    ) -> None:
        """明细整单替换：先软删旧行（保留流水），再批量插入新行（单价取订单明细）。"""
        await self._repo.soft_delete_items(inbound_id, operator_id)
        for line, order_item in lines:
            self._db.add(
                PurInboundItem(
                    inbound_id=inbound_id,
                    order_item_id=int(order_item.id),
                    goods_id=int(order_item.goods_id),
                    qty=line.qty,
                    price=Decimal(str(order_item.price)),
                    amount=(line.qty * Decimal(str(order_item.price))).quantize(CENT),
                    tax_rate=Decimal(str(order_item.tax_rate)),
                    batch_no=line.batch_no,
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _load_goods(self, goods_ids: set[int]) -> dict[int, BdGoods]:
        """批量加载商品（一次 IN 查询），供预填与详情展示使用。"""
        if not goods_ids:
            return {}
        rows = (
            await self._db.execute(
                select(BdGoods).where(
                    BdGoods.id.in_(goods_ids), BdGoods.tenant_id == self._tenant_id
                )
            )
        ).scalars()
        return {int(goods.id): goods for goods in rows.all()}

    async def _load_unit_names(self, unit_ids: set[int]) -> dict[int, str]:
        """批量加载单位名称（一次 IN 查询）。"""
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

    async def _load_supplier_name(self, supplier_id: int) -> str | None:
        """取供应商名称（单条查询）。"""
        name = await self._db.scalar(
            select(BdSupplier.name).where(
                BdSupplier.id == supplier_id, BdSupplier.tenant_id == self._tenant_id
            )
        )
        return str(name) if name else None

    async def _to_out_list(
        self, rows: list[PurInbound], *, with_items: bool
    ) -> list[PurInboundOut]:
        """批量转换出参：订单号、供应商、仓库名称与明细一次查完，避免 N+1。"""
        if not rows:
            return []
        inbound_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(inbound_ids)

        supplier_ids = {row.supplier_id for row in rows}
        supplier_names: dict[int, str] = {}
        if supplier_ids:
            supplier_rows = (
                await self._db.execute(
                    select(BdSupplier.id, BdSupplier.name).where(
                        BdSupplier.id.in_(supplier_ids), BdSupplier.tenant_id == self._tenant_id
                    )
                )
            ).all()
            supplier_names = {int(row[0]): str(row[1]) for row in supplier_rows}

        warehouse_ids = {row.warehouse_id for row in rows}
        warehouse_names: dict[int, str] = {}
        if warehouse_ids:
            warehouse_rows = (
                await self._db.execute(
                    select(BdWarehouse.id, BdWarehouse.name).where(
                        BdWarehouse.id.in_(warehouse_ids), BdWarehouse.tenant_id == self._tenant_id
                    )
                )
            ).all()
            warehouse_names = {int(row[0]): str(row[1]) for row in warehouse_rows}

        order_ids = {row.order_id for row in rows}
        order_nos: dict[int, str] = {}
        if order_ids:
            order_rows = (
                await self._db.execute(
                    select(PurOrder.id, PurOrder.order_no).where(
                        PurOrder.id.in_(order_ids), PurOrder.tenant_id == self._tenant_id
                    )
                )
            ).all()
            order_nos = {int(row[0]): str(row[1]) for row in order_rows}

        goods_map: dict[int, BdGoods] = {}
        unit_names: dict[int, str] = {}
        if with_items:
            goods_ids = {
                int(item.goods_id)
                for items in items_map.values()
                for item in items
                if not item.is_deleted
            }
            goods_map = await self._load_goods(goods_ids)
            unit_names = await self._load_unit_names(
                {int(goods.unit_id) for goods in goods_map.values() if goods.unit_id}
            )

        result: list[PurInboundOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            item_outs = (
                self._build_item_outs(active_items, goods_map, unit_names)
                if with_items
                else []
            )
            result.append(
                PurInboundOut.model_validate(row).model_copy(
                    update={
                        "supplier_name": supplier_names.get(row.supplier_id),
                        "warehouse_name": warehouse_names.get(row.warehouse_id),
                        "order_no": order_nos.get(row.order_id),
                        "items_count": len(active_items),
                        "items": item_outs,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[PurInboundItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
    ) -> list[PurInboundItemOut]:
        """把明细行转换成出参；商品与单位名称由外部批量加载。"""
        result: list[PurInboundItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            unit_name = (
                unit_names.get(int(goods.unit_id)) if goods is not None and goods.unit_id else None
            )
            result.append(
                PurInboundItemOut(
                    id=item.id,
                    order_item_id=item.order_item_id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=unit_name,
                    qty=item.qty,
                    price=item.price,
                    amount=item.amount,
                    tax_rate=item.tax_rate,
                    batch_no=item.batch_no,
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        inbound_id: int,
        inbound_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。"""
        params: dict[str, Any] = {"inboundId": inbound_id, "inboundNo": inbound_no}
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
