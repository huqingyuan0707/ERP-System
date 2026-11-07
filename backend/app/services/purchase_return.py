"""采购退货业务逻辑（P-04）：草稿 CRUD、原单可退查询、确认事务与操作日志。

数据闭环：
  采购入库（已确认）→ 退货单以 source_no 定位原单 → POST /purchase/returns 生成草稿
  → 确认（同一事务）：校验可退数量/可用量/退货金额 → 行锁 inv_stock → 减少 qty
    → 重算 available_qty（avg_cost 不变）→ 写 inv_stock_flow（purchase_return, direction=-1）
    → 按 source_no 冲减原应付 fin_payable：amount -= 退货金额、unpaid_amount -= 退货金额，
      重算状态 → 提交。
  不修改原入库单；数量按「原单号 + 商品」可追溯；任一步失败整体回滚。

敏感点（事务 / 锁 / 状态机）：
  1. 确认前对原入库单行加锁（get_source_inbound_for_update），同一 source_no 的
     并发确认在此串行化，保证「累计可退量校验 + 应付冲减」不被穿插。
  2. 应付行也加锁（get_payable_for_update）后读取并冲减，避免丢失更新；
     冲减后 amount 保持非负（模型 CHECK 兜底），unpaid 可为负（表示供应商应退/预付）。
  3. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定（与 P-02 一致）。
  4. 库存扣减统一走 InventoryService.change_stock（direction=-1，结转当前 avg_cost，
     avg_cost 本身不变），与入库确认保持同一把锁获取顺序：仓库 → 商品 → 库存行。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdSupplier, BdUnit, BdWarehouse
from app.models.finance import FinPayable
from app.models.pur import PurInbound, PurInboundItem, PurReturn, PurReturnItem
from app.models.sys import SysUser
from app.repositories.inventory import InventoryRepository
from app.repositories.purchase_return import PurReturnRepository
from app.schemas.inventory import ChangeStockIn
from app.schemas.purchase_return import (
    PurReturnCreate,
    PurReturnItemIn,
    PurReturnItemOut,
    PurReturnOut,
    PurReturnQuery,
    PurReturnStatus,
    PurReturnUpdate,
    SourceInboundItem,
    SourceInboundOut,
    SourcePayableOut,
)
from app.services.audit import write_oper_log
from app.services.inventory import InventoryService
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result


# 金额与数量统一精度：ROUND_HALF_EVEN（银行家舍入），禁止改为 ROUND_HALF_UP
CENT = Decimal("0.01")
ZERO = Decimal("0")


class PurchaseReturnService:
    """采购退货：查询 / 原单可退查询 / 草稿 CRUD / 确认事务。"""

    # 单号规则键：sys_config.billno.pur_return = "PR,4"
    RETURN_BIZ_KEY = "pur_return"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "采购管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PurReturnRepository(db, tenant_id=tenant_id)
        self._inv_service = InventoryService(db, tenant_id)
        self._inv_repo = InventoryRepository(db, tenant_id=tenant_id)

    # ---------- 原单可退查询（只读） ----------

    async def get_source_inbound(self, source_no: str, user: SysUser) -> SourceInboundOut:
        """按原入库单号查询可退数据：头信息 + 按商品聚合的原入库/已退/可退数量 + 应付快照。

        校验链：入库单存在（404）→ 已确认（409，未确认无库存与应付可冲减）。
        只读：不改任何数据；已退数量只统计已确认退货单（草稿不占用可退量）。
        """
        ctx = await resolve_scope(self._db, user)
        inbound = await self._repo.get_source_inbound(source_no)
        if inbound is None or inbound.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "原入库单不存在")
        if not await allows(self._db, ctx, created_by=inbound.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该原入库单")
        if int(inbound.status) != PurReturnStatus.CONFIRMED:
            raise AppException(ErrorCode.CONFLICT, "原入库单尚未确认，不能退货")

        # 按商品聚合原入库数量（同一商品多行时合并）与原入库单价（取末行，作退货单价默认值）
        items = (
            await self._db.execute(
                select(PurInboundItem)
                .where(
                    PurInboundItem.inbound_id == int(inbound.id),
                    PurInboundItem.tenant_id == self._tenant_id,
                    PurInboundItem.is_deleted.is_(False),
                )
                .order_by(PurInboundItem.id)
            )
        ).scalars().all()
        original_qty: dict[int, Decimal] = {}
        last_price: dict[int, Decimal] = {}
        for item in items:
            goods_id = int(item.goods_id)
            original_qty[goods_id] = original_qty.get(goods_id, ZERO) + Decimal(str(item.qty))
            last_price[goods_id] = Decimal(str(item.price))
        returned_qty = await self._repo.sum_returned_qty(source_no)

        goods_ids = set(original_qty)
        goods_map = await self._load_goods(goods_ids)
        unit_names = await self._load_unit_names(
            {int(g.unit_id) for g in goods_map.values() if g.unit_id}
        )
        item_outs: list[SourceInboundItem] = []
        for goods_id, qty in original_qty.items():
            returned = returned_qty.get(goods_id, ZERO)
            goods = goods_map.get(goods_id)
            item_outs.append(
                SourceInboundItem(
                    goods_id=goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=(
                        unit_names.get(int(goods.unit_id))
                        if goods is not None and goods.unit_id
                        else None
                    ),
                    original_qty=qty,
                    returned_qty=returned,
                    returnable_qty=qty - returned,
                    price=last_price.get(goods_id, ZERO),
                )
            )

        payable = await self._repo.get_payable_for_update(source_no)
        payable_out = (
            SourcePayableOut(
                amount=Decimal(str(payable.amount)),
                paid_amount=Decimal(str(payable.paid_amount)),
                unpaid_amount=Decimal(str(payable.unpaid_amount)),
                status=int(payable.status),
            )
            if payable is not None
            else None
        )

        supplier_name, warehouse_name = await self._load_head_names(
            int(inbound.supplier_id), int(inbound.warehouse_id)
        )
        return SourceInboundOut(
            inbound_id=int(inbound.id),
            inbound_no=str(inbound.inbound_no),
            supplier_id=int(inbound.supplier_id),
            supplier_name=supplier_name,
            warehouse_id=int(inbound.warehouse_id),
            warehouse_name=warehouse_name,
            inbound_date=inbound.inbound_date,
            payable=payable_out,
            items=item_outs,
        )

    # ---------- 查询 ----------

    async def list_returns(self, query: PurReturnQuery, user: SysUser) -> Any:
        """分页查询退货单：先叠加数据范围，再按状态/供应商/仓库/原单号/日期筛选。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        # pur_return 无 dept_id 列，apply_scope 按创建人所属部门子树过滤
        stmt = await apply_scope(self._db, stmt, PurReturn, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_return(self, return_id: int, user: SysUser) -> PurReturnOut:
        """退货单详情（含明细与可退数量）：必须在当前用户数据范围内，否则 403；不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        ret = await self._get_visible(return_id, ctx, for_update=False)
        return (await self._to_out_list([ret], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_return(
        self, payload: PurReturnCreate, user: SysUser, *, commit: bool = True
    ) -> PurReturnOut:
        """新建采购退货（落库即草稿）：单号自动生成，金额由后端重算。"""
        try:
            source = await self._assert_source(payload.source_no)
            await self._assert_supplier(payload.supplier_id, source.supplier_id)
            await self._assert_warehouse(payload.warehouse_id)
            await self._assert_goods_returnable(
                payload.source_no, {int(item.goods_id) for item in payload.items}
            )
            await self._assert_lines(payload.source_no, payload.items)
            # 取号与单号查重共用事务锁，避免并发生成重复单号
            await self._repo.lock_for_write()
            return_no = await generate_bill_no(self._db, self.RETURN_BIZ_KEY)
            total_amount = self._calc_total(payload.items)
            ret = PurReturn(
                return_no=return_no,
                supplier_id=payload.supplier_id,
                warehouse_id=payload.warehouse_id,
                source_no=payload.source_no,
                return_date=payload.return_date,
                status=PurReturnStatus.DRAFT,
                total_amount=total_amount,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(ret)
            await self._replace_items(ret.id, payload.items, user.id)
            await self._write_log(user.id, "新增采购退货", ret.id, ret.return_no)
            created = await self._repo.get_with_lock(ret.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "采购退货单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_return(
        self,
        return_id: int,
        payload: PurReturnUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurReturnOut:
        """修改草稿：仅草稿可改（其余状态 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != PurReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购退货单可以修改")
            data = payload.model_dump(exclude_unset=True)
            source_no = str(data.get("source_no") or ret.source_no)
            supplier_id = int(data.get("supplier_id") or ret.supplier_id)
            warehouse_id = int(data.get("warehouse_id") or ret.warehouse_id)
            source = await self._assert_source(source_no)
            await self._assert_supplier(supplier_id, source.supplier_id)
            await self._assert_warehouse(warehouse_id)
            items = payload.items
            if items is not None:
                await self._assert_goods_returnable(
                    source_no, {int(item.goods_id) for item in items}
                )
                await self._assert_lines(source_no, items)
            # 先抢占版本号再动数据：并发修改时后到者在改明细之前就被拒绝
            await self._bump_version(ret, payload.version, user.id)
            if data.get("supplier_id") is not None:
                ret.supplier_id = supplier_id
            if data.get("warehouse_id") is not None:
                ret.warehouse_id = warehouse_id
            if data.get("source_no") is not None:
                ret.source_no = source_no
            if data.get("return_date") is not None:
                ret.return_date = data["return_date"]
            if "remark" in data:
                ret.remark = data["remark"]
            if items is not None:
                await self._replace_items(ret.id, items, user.id)
                # 明细变了必须重算合计，避免主表金额与明细不一致
                ret.total_amount = self._calc_total(items)
            ret.updated_by = user.id
            await self._write_log(user.id, "修改采购退货", ret.id, ret.return_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(ret.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购退货单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_return(self, return_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除草稿：仅草稿可删，软删主表并同步软删明细，历史流水保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != PurReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购退货单可以删除")
            await self._bump_version(ret, None, user.id)
            ret.is_deleted = True
            ret.updated_by = user.id
            await self._repo.soft_delete_items(ret.id, user.id)
            await self._write_log(user.id, "删除采购退货", ret.id, ret.return_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 确认事务 ----------

    async def confirm_return(
        self, return_id: int, user: SysUser, *, commit: bool = True
    ) -> PurReturnOut:
        """确认退货：库存减少 + 流水 + 冲减原应付，任一步失败整体回滚。

        事务步骤（全部在同一数据库事务内）：
          1. 锁定退货单行，校验状态为草稿（重复确认 409）并抢占版本号；
          2. 锁定原入库单行（同一 source_no 的并发确认在此串行化），校验原单已确认；
          3. 按商品聚合明细并校验：
             - 可退数量：qty ≤ 原入库累计 - 已确认已退（409）；
             - 当前可用量：available_qty ≥ qty（409，行锁 inv_stock 后判定）；
             - 退货金额：退货单合计 ≤ 原应付当前 amount（409，行锁 fin_payable 后判定）；
          4. 逐行调用 InventoryService.change_stock：减少 qty、重算 available_qty
             （avg_cost 不变）、写 inv_stock_flow（purchase_return, direction=-1）；
          5. 冲减原应付：amount -= 退货金额、unpaid_amount -= 退货金额，重算状态；
          6. 退货单置为已确认，写操作日志。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != PurReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "该采购退货单已确认，不能重复确认")
            await self._bump_version(ret, None, user.id)

            # 锁定原入库单：同一 source_no 的并发确认串行化，可退量/应付校验不被穿插
            source = await self._repo.get_source_inbound_for_update(ret.source_no)
            if source is None or source.is_deleted:
                raise AppException(ErrorCode.VALIDATION_ERROR, "原入库单不存在")
            if int(source.status) != PurReturnStatus.CONFIRMED:
                raise AppException(ErrorCode.CONFLICT, "原入库单尚未确认，不能退货")

            items_map = await self._repo.list_items([int(ret.id)])
            active_items = [
                item for item in items_map.get(int(ret.id), []) if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "退货单没有明细行，不能确认")

            # 同一商品可能被拆成多行提交，先按商品聚合再校验，避免绕过可退量限制
            merged: dict[int, Decimal] = {}
            for line in active_items:
                goods_id = int(line.goods_id)
                merged[goods_id] = merged.get(goods_id, ZERO) + Decimal(str(line.qty))

            # 可退数量校验（G10：原单号 + 商品口径，只统计已确认退货）
            original_qty = await self._sum_source_qty(ret.source_no, set(merged))
            returned_qty = await self._repo.sum_returned_qty(ret.source_no, set(merged))
            for goods_id, qty in merged.items():
                original = original_qty.get(goods_id, ZERO)
                returned = returned_qty.get(goods_id, ZERO)
                returnable = original - returned
                if qty > returnable:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"退货数量 {qty} 超过可退数量 {returnable}（商品 ID {goods_id}，"
                        f"原入库 {original}，已退 {returned}）",
                    )

            # 当前可用量校验：按「仓库 → 商品 → 库存行」顺序加行锁（与 change_stock 一致）
            await self._assert_warehouse_locked(int(ret.warehouse_id))
            stock_map: dict[int, Decimal] = {}
            for goods_id in merged:
                goods = await self._inv_repo.get_goods_for_update(goods_id)
                if goods is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {goods_id}")
                if goods.status != 1:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"商品「{goods.name}」已停用，不能退货出库"
                    )
                stock = await self._inv_repo.get_stock_for_update(
                    int(ret.warehouse_id), goods_id
                )
                available = Decimal(str(stock.available_qty)) if stock is not None else ZERO
                if stock is None or available < merged[goods_id]:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"可用库存不足：商品 ID {goods_id} 可用 {available}，"
                        f"退货需要 {merged[goods_id]}",
                    )
                stock_map[goods_id] = Decimal(str(stock.qty))

            # 退货金额校验：行锁应付后判定，退货单合计不得超过原应付当前 amount
            total_amount = Decimal(str(ret.total_amount))
            payable = await self._repo.get_payable_for_update(ret.source_no)
            if payable is None:
                raise AppException(ErrorCode.CONFLICT, "原入库单没有应付记录，无法冲减")
            if total_amount > Decimal(str(payable.amount)):
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"退货金额 {total_amount} 超过原应付当前余额 {Decimal(str(payable.amount))}",
                )

            # 库存扣减：direction=-1 时 avg_cost 不变，流水结转当前均价；任一行失败整体回滚
            biz_time = datetime.combine(ret.return_date, datetime.now().time())
            for line in active_items:
                await self._inv_service.change_stock(
                    ChangeStockIn(
                        warehouse_id=int(ret.warehouse_id),
                        goods_id=int(line.goods_id),
                        direction=-1,
                        qty=Decimal(str(line.qty)),
                        unit_cost=ZERO,
                        biz_type="purchase_return",
                        source_no=ret.return_no,
                        biz_time=biz_time,
                        remark=f"采购退货 {ret.return_no}，原入库单 {ret.source_no}",
                    ),
                    commit=False,
                )

            # 冲减原应付：amount 与 unpaid_amount 同步减少（G1 口径，不建红字）
            await self._reduce_payable(payable, total_amount)

            ret.status = PurReturnStatus.CONFIRMED
            ret.updated_by = user.id
            await self._write_log(
                user.id,
                "确认采购退货",
                ret.id,
                ret.return_no,
                extra={"sourceNo": ret.source_no, "amount": str(total_amount)},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(ret.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购退货单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _reduce_payable(self, payable: FinPayable, amount: Decimal) -> None:
        """冲减原应付：amount -= 退货金额、unpaid_amount -= 退货金额，并重算状态。

        应付行已在 confirm 事务内加锁，此处直接改写不会丢失更新；
        冲减后 amount 保持非负（模型 CHECK 兜底），unpaid 可为负（供应商应退/预付）。
        独立成方法便于测试模拟「冲减失败」验证回滚。
        """
        payable.amount = Decimal(str(payable.amount)) - amount
        payable.unpaid_amount = Decimal(str(payable.unpaid_amount)) - amount
        # 重算状态：0 未付 / 1 部分付 / 2 已付；未付为负表示供应商应退，视为已付清
        paid = Decimal(str(payable.paid_amount))
        unpaid = Decimal(str(payable.unpaid_amount))
        if unpaid <= 0:
            payable.status = 2
        elif paid > 0:
            payable.status = 1
        else:
            payable.status = 0
        await self._db.flush()

    async def _assert_source(self, source_no: str) -> PurInbound:
        """校验原入库单：存在、未删除且已确认（草稿无库存与应付，不可退）。"""
        source = await self._repo.get_source_inbound(source_no)
        if source is None or source.is_deleted:
            raise AppException(ErrorCode.VALIDATION_ERROR, f"原入库单不存在: {source_no}")
        if int(source.status) != PurReturnStatus.CONFIRMED:
            raise AppException(ErrorCode.CONFLICT, "原入库单尚未确认，不能退货")
        return source

    async def _assert_supplier(self, supplier_id: int, source_supplier_id: int) -> None:
        """校验退货供应商启用，且必须与原入库单供应商一致（应付按原单定位冲减）。"""
        if supplier_id != int(source_supplier_id):
            raise AppException(
                ErrorCode.VALIDATION_ERROR, "退货供应商必须与原入库单供应商一致"
            )
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
            raise AppException(ErrorCode.VALIDATION_ERROR, "供应商已停用，不能退货")

    async def _assert_warehouse(self, warehouse_id: int) -> None:
        """校验退货出库仓库存在且启用（草稿期快速失败，确认事务再加锁复核）。"""
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
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库已停用，不能退货")

    async def _assert_warehouse_locked(self, warehouse_id: int) -> None:
        """确认事务内的仓库复核：行锁读取并校验启用，保持与 change_stock 相同的锁顺序。"""
        warehouse = await self._inv_repo.get_warehouse_for_update(warehouse_id)
        if warehouse is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库不存在")
        if warehouse.status != 1:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库已停用，不能退货")

    async def _assert_goods_returnable(self, source_no: str, goods_ids: set[int]) -> None:
        """校验明细商品全部来自原入库单：不在原单内的商品没有可退数量。"""
        original_qty = await self._sum_source_qty(source_no, goods_ids)
        for goods_id in goods_ids:
            if goods_id not in original_qty:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品不在原入库单内，不能退货: {goods_id}"
                )

    async def _assert_lines(self, source_no: str, items: list[PurReturnItemIn]) -> None:
        """草稿期快速校验数量与金额：可退量与原应付余额（确认事务会再权威校验一次）。"""
        goods_ids = {int(item.goods_id) for item in items}
        original_qty = await self._sum_source_qty(source_no, goods_ids)
        returned_qty = await self._repo.sum_returned_qty(source_no, goods_ids)
        merged: dict[int, Decimal] = {}
        for item in items:
            goods_id = int(item.goods_id)
            merged[goods_id] = merged.get(goods_id, ZERO) + item.qty
        for goods_id, qty in merged.items():
            returnable = original_qty.get(goods_id, ZERO) - returned_qty.get(goods_id, ZERO)
            if qty > returnable:
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"退货数量 {qty} 超过可退数量 {returnable}（商品 ID {goods_id}）",
                )
        payable = await self._repo.get_payable_for_update(source_no)
        if payable is None:
            raise AppException(ErrorCode.CONFLICT, "原入库单没有应付记录，无法冲减")
        total = self._calc_total(items)
        if total > Decimal(str(payable.amount)):
            raise AppException(
                ErrorCode.CONFLICT,
                f"退货金额 {total} 超过原应付当前余额 {Decimal(str(payable.amount))}",
            )

    async def _sum_source_qty(self, source_no: str, goods_ids: set[int]) -> dict[int, Decimal]:
        """按商品汇总原入库单的实际入库数量（同一商品多行时合并）。"""
        stmt = (
            select(PurInboundItem.goods_id, func.sum(PurInboundItem.qty))
            .select_from(PurInboundItem)
            .join(PurInbound, PurInboundItem.inbound_id == PurInbound.id)
            .where(
                PurInbound.inbound_no == source_no,
                PurInbound.tenant_id == self._tenant_id,
                PurInbound.is_deleted.is_(False),
                PurInboundItem.tenant_id == self._tenant_id,
                PurInboundItem.is_deleted.is_(False),
            )
            .group_by(PurInboundItem.goods_id)
        )
        if goods_ids:
            stmt = stmt.where(PurInboundItem.goods_id.in_(goods_ids))
        rows = (await self._db.execute(stmt)).all()
        return {int(row[0]): Decimal(str(row[1])) for row in rows}

    @staticmethod
    def _calc_total(items: list[PurReturnItemIn]) -> Decimal:
        """按含税口径重算退货总金额：Σ round(qty*price, 2)。"""
        total = ZERO
        for item in items:
            total += (item.qty * item.price).quantize(CENT)
        return total

    async def _get_visible(
        self, return_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> PurReturn:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。"""
        ret = await self._repo.get_with_lock(return_id, for_update=for_update)
        if ret is None or ret.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购退货单不存在")
        if not await allows(self._db, ctx, created_by=ret.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购退货单")
        return ret

    async def _bump_version(
        self, ret: PurReturn, expected: int | None, user_id: int
    ) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409「数据已被他人修改」。

        SQLite 无 SELECT ... FOR UPDATE，只比较内存 version 拦不住并发；
        synchronize_session=False 避免 ORM 对象被同步成新版本后回写覆盖。
        """
        current = int(ret.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(PurReturn)
            .where(
                PurReturn.id == ret.id,
                PurReturn.tenant_id == self._tenant_id,
                PurReturn.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _replace_items(
        self, return_id: int, items: list[PurReturnItemIn], operator_id: int
    ) -> None:
        """明细整单替换：先软删旧行（保留流水），再批量插入新行并重算行金额。"""
        await self._repo.soft_delete_items(return_id, operator_id)
        for item in items:
            self._db.add(
                PurReturnItem(
                    return_id=return_id,
                    goods_id=item.goods_id,
                    qty=item.qty,
                    price=item.price,
                    amount=(item.qty * item.price).quantize(CENT),
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _load_goods(self, goods_ids: set[int]) -> dict[int, BdGoods]:
        """批量加载商品（一次 IN 查询），供原单查询与详情展示使用。"""
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

    async def _load_head_names(
        self, supplier_id: int, warehouse_id: int
    ) -> tuple[str | None, str | None]:
        """取原入库单的供应商名与仓库名（两条单行查询）。"""
        supplier_name = await self._db.scalar(
            select(BdSupplier.name).where(
                BdSupplier.id == supplier_id, BdSupplier.tenant_id == self._tenant_id
            )
        )
        warehouse_name = await self._db.scalar(
            select(BdWarehouse.name).where(
                BdWarehouse.id == warehouse_id, BdWarehouse.tenant_id == self._tenant_id
            )
        )
        return (
            str(supplier_name) if supplier_name else None,
            str(warehouse_name) if warehouse_name else None,
        )

    async def _to_out_list(self, rows: list[PurReturn], *, with_items: bool) -> list[PurReturnOut]:
        """批量转换出参：供应商、仓库名称与明细（含可退数量）一次查完，避免 N+1。"""
        if not rows:
            return []
        return_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(return_ids)

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

        goods_map: dict[int, BdGoods] = {}
        unit_names: dict[int, str] = {}
        source_qty_cache: dict[str, tuple[dict[int, Decimal], dict[int, Decimal]]] = {}
        if with_items:
            goods_ids = {
                int(item.goods_id)
                for items in items_map.values()
                for item in items
                if not item.is_deleted
            }
            goods_map = await self._load_goods(goods_ids)
            unit_names = await self._load_unit_names(
                {int(g.unit_id) for g in goods_map.values() if g.unit_id}
            )
            for row in rows:
                source_no = str(row.source_no)
                if source_no not in source_qty_cache:
                    source_qty_cache[source_no] = (
                        await self._sum_source_qty(source_no, goods_ids),
                        await self._repo.sum_returned_qty(source_no, goods_ids),
                    )

        result: list[PurReturnOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            item_outs: list[PurReturnItemOut] = []
            if with_items:
                original_qty, returned_qty = source_qty_cache[str(row.source_no)]
                item_outs = self._build_item_outs(
                    active_items, goods_map, unit_names, original_qty, returned_qty
                )
            result.append(
                PurReturnOut.model_validate(row).model_copy(
                    update={
                        "supplier_name": supplier_names.get(row.supplier_id),
                        "warehouse_name": warehouse_names.get(row.warehouse_id),
                        "items_count": len(active_items),
                        "items": item_outs,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[PurReturnItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
        original_qty: dict[int, Decimal],
        returned_qty: dict[int, Decimal],
    ) -> list[PurReturnItemOut]:
        """把明细行转换成出参；原入库/已退/可退数量按「原单号 + 商品」汇总补齐。"""
        result: list[PurReturnItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            goods_id = int(item.goods_id)
            original = original_qty.get(goods_id, ZERO)
            returned = returned_qty.get(goods_id, ZERO)
            result.append(
                PurReturnItemOut(
                    id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=(
                        unit_names.get(int(goods.unit_id))
                        if goods is not None and goods.unit_id
                        else None
                    ),
                    qty=item.qty,
                    price=item.price,
                    amount=item.amount,
                    original_qty=original,
                    returned_qty=returned,
                    returnable_qty=original - returned,
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        return_id: int,
        return_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。"""
        params: dict[str, Any] = {"returnId": return_id, "returnNo": return_no}
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
