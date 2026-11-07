"""采购订单业务逻辑（P-02）：草稿 CRUD、状态机、入库进度回写、导出与操作日志。

数据闭环：
  pur_apply（已审）→ 转单预填接口 → 前端填单 → POST /purchase/orders 生成 pur_order 草稿
  → 提交（待审）→ 审核（已审 / 驳回回草稿）→ P-03 采购入库引用
  → 入库确认调用 apply_inbound 回写 pur_order_item.inbound_qty 并重算订单状态
  → 手工关闭后剩余未入库数量作废。
  本模块**不写库存（inv_stock / inv_stock_flow）、不写财务（fin_payable / fin_receivable）**，
  入库与应付由后续 P-03/P-04 负责。

敏感点（事务 / 锁 / 状态机）：
  1. 所有用户触发的写操作先按数据范围取单据，并对主表加行锁
     （get_with_lock(for_update=True)），保证「读状态 → 校验 → 改状态」并发下不被穿插。
  2. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定：SQLite 不支持
     FOR UPDATE，只比较内存版本号拦不住并发，必须靠原子更新抢占版本号（冲突 409）。
  3. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False；
     入库回写 apply_inbound 默认不提交，由 P-03 的入库确认事务统一提交。
  4. 明细整单替换采用「软删旧行 + 新增」，历史明细保留（is_deleted=True）。
  5. 转单预填为只读操作，不改变申请单状态、不落库。
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdSupplier, BdUnit
from app.models.pur import PurApply, PurOrder, PurOrderItem
from app.models.sys import SysDept, SysUser
from app.repositories.purchase_apply import PurApplyRepository
from app.repositories.purchase_order import PurOrderRepository
from app.schemas.common import PageResult
from app.schemas.purchase_apply import PurApplyStatus
from app.schemas.purchase_order import (
    AUDIT_RESULT_APPROVED,
    CLOSABLE_STATUSES,
    INBOUNDABLE_STATUSES,
    ORDER_STATUS_TEXT,
    PurOrderAudit,
    PurOrderClose,
    PurOrderCreate,
    PurOrderFromApplyItem,
    PurOrderFromApplyOut,
    PurOrderInboundApply,
    PurOrderItemIn,
    PurOrderItemOut,
    PurOrderOut,
    PurOrderQuery,
    PurOrderStatus,
    PurOrderUpdate,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.excel import build_workbook
from app.utils.pagination import paginate_result


# 金额与数量统一精度：ROUND_HALF_EVEN（银行家舍入），禁止改为 ROUND_HALF_UP
CENT = Decimal("0.01")
ZERO = Decimal("0")


class PurchaseOrderService:
    """采购订单：查询 / 草稿 CRUD / 提交审核关闭 / 入库回写 / 导出。"""

    # 单号规则键：sys_config.billno.pur_order = "PO,4"
    ORDER_BIZ_KEY = "pur_order"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "采购管理"
    # 导出列：与列表展示口径一致，取单据快照（含税金额、税额、入库进度）
    EXPORT_HEADERS: tuple[str, ...] = (
        "订单号",
        "供应商",
        "下单日期",
        "状态",
        "明细行数",
        "订购数量",
        "已入库数量",
        "入库进度",
        "含税金额",
        "税额",
        "审核人",
        "审核日期",
        "审核/驳回/关闭原因",
        "备注",
        "创建时间",
    )

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PurOrderRepository(db, tenant_id=tenant_id)
        self._apply_repo = PurApplyRepository(db, tenant_id=tenant_id)

    # ---------- 转单预填（只读） ----------

    async def build_from_apply(self, apply_id: int, user: SysUser) -> PurOrderFromApplyOut:
        """按已审采购申请单组装订单预填数据，供前端填充新建订单表单。

        校验链：申请单存在且未软删（404）→ 数据范围允许（403）→ 状态已审（409）。
        只读：不修改申请单、不生成订单、不写操作日志。
        """
        ctx = await resolve_scope(self._db, user)
        apply = await self._apply_repo.get_with_items(apply_id, for_update=False)
        if apply is None or apply.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购申请不存在")
        if not await allows(self._db, ctx, created_by=apply.created_by, dept_id=apply.dept_id):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购申请")
        if apply.status != PurApplyStatus.APPROVED:
            raise AppException(ErrorCode.CONFLICT, "只有已审的采购申请可以转为采购订单")

        # 批量取明细 + 商品 + 单位，避免逐行查询（N+1）
        items_map = await self._apply_repo.list_items([int(apply.id)])
        active_items = [item for item in items_map.get(int(apply.id), []) if not item.is_deleted]
        if not active_items:
            raise AppException(ErrorCode.VALIDATION_ERROR, "申请单没有可用明细，不能转单")

        goods_ids = {int(item.goods_id) for item in active_items}
        goods_map = await self._load_goods(goods_ids)
        unit_names = await self._load_unit_names(
            {int(g.unit_id) for g in goods_map.values() if g.unit_id}
        )
        dept_name, applicant_name = await self._load_apply_names(apply)

        from_rows: list[PurOrderFromApplyItem] = []
        for item in active_items:
            goods = goods_map.get(int(item.goods_id))
            from_rows.append(
                PurOrderFromApplyItem(
                    apply_item_id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_id=goods.unit_id if goods else None,
                    unit_name=(
                        unit_names.get(int(goods.unit_id)) if goods and goods.unit_id else None
                    ),
                    qty=item.qty,
                    # 单价默认取商品成本价，前端可修改后再提交
                    price=goods.cost_price if goods else ZERO,
                    tax_rate=ZERO,
                    need_date=item.need_date,
                )
            )

        return PurOrderFromApplyOut(
            source_apply_id=apply.id,
            apply_no=apply.apply_no,
            dept_id=apply.dept_id,
            dept_name=dept_name,
            applicant_id=apply.applicant_id,
            applicant_name=applicant_name,
            need_date=apply.need_date,
            order_date=date.today(),
            # 预填备注带出申请单号，便于下单时人工核对来源
            remark=f"由采购申请 {apply.apply_no} 转单",
            items=from_rows,
        )

    # ---------- 查询与导出 ----------

    async def list_orders(
        self, query: PurOrderQuery, user: SysUser
    ) -> PageResult[PurOrderOut]:
        """分页查询订单：先叠加数据范围，再按状态/供应商/日期/来源申请单筛选。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        # pur_order 无 dept_id 列，apply_scope 按创建人所属部门子树过滤
        stmt = await apply_scope(self._db, stmt, PurOrder, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_order(self, order_id: int, user: SysUser) -> PurOrderOut:
        """订单详情（含明细）：必须在当前用户数据范围内，否则 403；不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        order = await self._get_visible(order_id, ctx, for_update=False)
        return (await self._to_out_list([order], with_items=True))[0]

    async def export_orders(self, query: PurOrderQuery, user: SysUser) -> bytes:
        """按当前筛选条件导出订单 Excel；与列表共用数据范围，防止越权导出他人单据。

        返回 xlsx 字节流，由端点包装成下载响应；导出动作写操作日志（G-11）。
        """
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, PurOrder, ctx)
        rows = await self._repo.list_all(stmt)
        outs = await self._to_out_list(rows, with_items=False)

        body: list[list[object]] = []
        for out in outs:
            body.append(
                [
                    out.order_no,
                    out.supplier_name or "",
                    out.order_date.isoformat(),
                    ORDER_STATUS_TEXT.get(out.status, "未知"),
                    out.items_count,
                    str(out.total_qty),
                    str(out.inbound_qty),
                    f"{out.inbound_progress}%",
                    str(out.total_amount),
                    str(out.tax_amount),
                    out.audit_by_name or "",
                    out.audit_at.isoformat() if out.audit_at else "",
                    out.audit_remark or "",
                    out.remark or "",
                    out.created_at.strftime("%Y-%m-%d %H:%M:%S") if out.created_at else "",
                ]
            )
        content = build_workbook(list(self.EXPORT_HEADERS), body, "采购订单")
        # 导出属于审计动作：与文件生成成功后的日志同事务提交
        await write_oper_log(
            self._db,
            tenant_id=self._tenant_id,
            operator_id=user.id,
            action="导出采购订单",
            params={
                "count": len(outs),
                "filters": query.model_dump(mode="json", exclude_none=True),
            },
            module=self.OPER_MODULE,
        )
        await self._db.commit()
        return content

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_order(
        self, payload: PurOrderCreate, user: SysUser, *, commit: bool = True
    ) -> PurOrderOut:
        """新建采购订单（落库即草稿）：自动取号，金额由后端重算，记录来源申请单溯源。"""
        try:
            await self._assert_supplier(payload.supplier_id)
            items = await self._load_order_items(payload.items)
            await self._assert_source_apply(payload.source_apply_id)
            # 取号与单号查重共用事务锁，避免并发生成重复单号
            await self._repo.lock_for_write()
            order_no = await generate_bill_no(self._db, self.ORDER_BIZ_KEY)
            total_amount, tax_amount = self._calc_totals(items)
            order = PurOrder(
                order_no=order_no,
                supplier_id=payload.supplier_id,
                order_date=payload.order_date,
                status=PurOrderStatus.DRAFT,
                total_amount=total_amount,
                tax_amount=tax_amount,
                remark=payload.remark,
                source_apply_id=payload.source_apply_id,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(order)
            await self._replace_items(order.id, items, user.id)
            await self._write_log(user.id, "新增采购订单", order.id, order.order_no)
            created = await self._repo.get_with_lock(order.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "采购订单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_order(
        self,
        order_id: int,
        payload: PurOrderUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurOrderOut:
        """修改草稿：仅草稿可改（其余状态 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != PurOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购订单可以修改")
            # 先抢占版本号再动数据：并发修改时后到者在改明细之前就被拒绝
            await self._bump_version(order, payload.version, user.id)
            data = payload.model_dump(exclude_unset=True)
            if data.get("supplier_id") is not None:
                await self._assert_supplier(data["supplier_id"])
                order.supplier_id = data["supplier_id"]
            if data.get("order_date") is not None:
                order.order_date = data["order_date"]
            if "remark" in data:
                order.remark = data["remark"]
            if payload.items is not None:
                items = await self._load_order_items(payload.items)
                await self._replace_items(order.id, items, user.id)
                # 明细变了必须重算合计，避免主表金额与明细不一致
                order.total_amount, order.tax_amount = self._calc_totals(items)
            order.updated_by = user.id
            await self._write_log(user.id, "修改采购订单", order.id, order.order_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_order(self, order_id: int, user: SysUser, *, commit: bool = True) -> None:
        """删除草稿：仅草稿可删，软删主表并同步软删明细，历史流水保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != PurOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购订单可以删除")
            await self._bump_version(order, None, user.id)
            order.is_deleted = True
            order.updated_by = user.id
            await self._repo.soft_delete_items(order.id, user.id)
            await self._write_log(user.id, "删除采购订单", order.id, order.order_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 状态机：提交 / 审核 / 关闭 ----------

    async def submit_order(
        self,
        order_id: int,
        user: SysUser,
        *,
        version: int | None = None,
        commit: bool = True,
    ) -> PurOrderOut:
        """提交：草稿 → 待审；提交前复核供应商/商品启用与明细合法性（最后一道校验）。"""
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != PurOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购订单可以提交")
            active_items = await self._active_items(order.id)
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "订单没有明细行，不能提交")
            await self._assert_items_submittable(active_items)
            await self._assert_supplier(order.supplier_id)
            await self._bump_version(order, version, user.id)
            order.status = PurOrderStatus.PENDING
            # 重新提交时清空上一次驳回原因，避免历史意见误导审核人
            order.audit_remark = None
            order.updated_by = user.id
            await self._write_log(user.id, "提交采购订单", order.id, order.order_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def audit_order(
        self,
        order_id: int,
        payload: PurOrderAudit,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurOrderOut:
        """审核：待审 → 已审（通过）/ 草稿（驳回并写原因）；审核人/日期落库。

        审核只推进状态，不改写明细数量、不写库存、不写财务，
        保证审核后的订单内容与提交时一致，采购入库据此引用可追溯。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != PurOrderStatus.PENDING:
                raise AppException(ErrorCode.CONFLICT, "只有待审状态的采购订单可以审核")
            remark = (payload.remark or "").strip() or None
            if payload.result == AUDIT_RESULT_APPROVED:
                order.status = PurOrderStatus.APPROVED
                action = "审核通过采购订单"
            else:
                order.status = PurOrderStatus.DRAFT
                action = "驳回采购订单"
            await self._bump_version(order, payload.version, user.id)
            # 通过时保留审核意见，驳回时原因必填（Schema 已校验）
            order.audit_remark = remark
            order.audit_by = user.id
            # 模型 audit_at 为 DATE 字段（只到天），精确时间以 sys_oper_log.created_at 为准
            order.audit_at = date.today()
            order.updated_by = user.id
            await self._write_log(
                user.id,
                action,
                order.id,
                order.order_no,
                extra={"result": payload.result, "remark": remark},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def close_order(
        self,
        order_id: int,
        payload: PurOrderClose,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> PurOrderOut:
        """关闭：待审/已审/部分入库 → 关闭，剩余未入库数量作废，之后不可再入库。

        已全部入库（完成）的订单不允许关闭；关闭原因复用 audit_remark 存储，
        不新增列、不改迁移基线，前端按状态显示为「关闭原因」。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status == PurOrderStatus.COMPLETED:
                raise AppException(ErrorCode.CONFLICT, "已全部入库的订单不能关闭")
            if order.status not in CLOSABLE_STATUSES:
                raise AppException(
                    ErrorCode.CONFLICT, "只有待审、已审或部分入库的采购订单可以关闭"
                )
            reason = (payload.reason or "").strip() or None
            await self._bump_version(order, payload.version, user.id)
            order.status = PurOrderStatus.CLOSED
            order.audit_remark = reason
            order.updated_by = user.id
            await self._write_log(
                user.id,
                "关闭采购订单",
                order.id,
                order.order_no,
                extra={"reason": reason},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 入库回写（供 P-03 采购入库确认事务调用，不暴露 HTTP 接口） ----------

    async def apply_inbound(
        self,
        order_id: int,
        payload: PurOrderInboundApply,
        user: SysUser,
        *,
        commit: bool = False,
    ) -> PurOrderOut:
        """入库确认回写：累加 inbound_qty、重算订单状态，超剩余数量拒绝（409）。

        边界：
          - 只改 pur_order_item.inbound_qty 与 pur_order.status/version，
            **不写 inv_* 库存与 fin_* 应付**（P-03/P-04 职责）；
          - 由 P-03 在同一事务内调用，默认 commit=False；
          - 内部服务调用不做数据范围校验：仓库人员的数据范围可能不含采购订单创建人，
            但仍强制租户隔离（_repo.get_with_lock 自带 tenant_id 条件）。
        """
        try:
            order = await self._repo.get_with_lock(order_id, for_update=True)
            if order is None or order.is_deleted:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            if order.status not in INBOUNDABLE_STATUSES:
                raise AppException(
                    ErrorCode.CONFLICT, "采购订单当前状态不允许入库（需已审或部分入库）"
                )
            active_items = await self._active_items(order.id)
            item_map = {int(item.id): item for item in active_items}

            # 同一明细行可能被拆成多行提交，先聚合再校验，避免绕过剩余数量限制
            merged: dict[int, Decimal] = {}
            for line in payload.lines:
                merged[line.order_item_id] = merged.get(line.order_item_id, ZERO) + line.qty
            for item_id, qty in merged.items():
                item = item_map.get(item_id)
                if item is None:
                    raise AppException(
                        ErrorCode.VALIDATION_ERROR, f"订单明细不存在: {item_id}"
                    )
                remaining = Decimal(str(item.qty)) - Decimal(str(item.inbound_qty))
                if qty > remaining:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"入库数量 {qty} 超过剩余未入库数量 {remaining}（商品 ID {item.goods_id}）",
                    )
            # 全部校验通过后才抢占版本号并回写，保证失败时订单不被改动
            await self._bump_version(order, None, user.id)
            for item_id, qty in merged.items():
                item = item_map[item_id]
                item.inbound_qty = Decimal(str(item.inbound_qty)) + qty
                item.updated_by = user.id
            # 重算状态：所有明细行都入满 → 完成；否则部分入库
            all_done = all(
                Decimal(str(item.inbound_qty)) >= Decimal(str(item.qty))
                for item in active_items
            )
            order.status = (
                PurOrderStatus.COMPLETED if all_done else PurOrderStatus.PARTIAL_INBOUND
            )
            order.updated_by = user.id
            await self._write_log(
                user.id,
                "采购订单入库回写",
                order.id,
                order.order_no,
                extra={"lines": len(payload.lines), "qty": str(sum(merged.values(), ZERO))},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _get_visible(
        self, order_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> PurOrder:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。

        用户触发的列表详情之外的所有写操作都走这里，防止改 URL 越权操作他人订单。
        """
        order = await self._repo.get_with_lock(order_id, for_update=for_update)
        if order is None or order.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
        if not await allows(self._db, ctx, created_by=order.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该采购订单")
        return order

    async def _bump_version(
        self, order: PurOrder, expected: int | None, user_id: int
    ) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409「数据已被他人修改」。

        SQLite 无 SELECT ... FOR UPDATE，只比较内存 version 拦不住并发；
        synchronize_session=False 避免 ORM 对象被同步成新版本后回写覆盖，
        后续业务字段的 flush 只更新脏列，不会把 version 改回去。
        """
        current = int(order.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(PurOrder)
            .where(
                PurOrder.id == order.id,
                PurOrder.tenant_id == self._tenant_id,
                PurOrder.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _active_items(self, order_id: int) -> list[PurOrderItem]:
        """取订单当前有效明细行（排除编辑草稿时软删的历史行）。"""
        items_map = await self._repo.list_items([int(order_id)])
        return [item for item in items_map.get(int(order_id), []) if not item.is_deleted]

    async def _assert_items_submittable(self, items: list[PurOrderItem]) -> None:
        """提交前复核明细：商品仍启用、数量>0、单价≥0、税率 0～1。

        草稿保存期间商品可能被停用，提交时再查一次避免把停用商品带进审批。
        """
        goods_ids = {int(row.goods_id) for row in items}
        rows = (
            await self._db.execute(
                select(BdGoods.id, BdGoods.name, BdGoods.status).where(
                    BdGoods.id.in_(goods_ids),
                    BdGoods.tenant_id == self._tenant_id,
                    BdGoods.is_deleted.is_(False),
                )
            )
        ).all()
        status_map = {int(row[0]): (str(row[1]), int(row[2])) for row in rows}
        for row in items:
            goods_id = int(row.goods_id)
            if goods_id not in status_map:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {goods_id}")
            name, status = status_map[goods_id]
            if status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{name}」已停用，请删除该行后重新提交"
                )
            if Decimal(str(row.qty)) <= 0:
                raise AppException(ErrorCode.VALIDATION_ERROR, "订购数量必须大于 0")
            if Decimal(str(row.price)) < 0:
                raise AppException(ErrorCode.VALIDATION_ERROR, "含税单价不能小于 0")
            if not ZERO <= Decimal(str(row.tax_rate)) <= Decimal("1"):
                raise AppException(ErrorCode.VALIDATION_ERROR, "税率必须在 0 至 1 之间")

    async def _load_goods(self, goods_ids: set[int]) -> dict[int, BdGoods]:
        """批量加载商品（一次 IN 查询），供转单预填与详情展示使用。"""
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

    async def _load_apply_names(self, apply: PurApply) -> tuple[str | None, str | None]:
        """取申请单的部门名与申请人姓名（各一条查询，不循环）。"""
        dept_name = await self._db.scalar(
            select(SysDept.name).where(
                SysDept.id == apply.dept_id, SysDept.tenant_id == self._tenant_id
            )
        )
        applicant = (
            await self._db.execute(
                select(SysUser.real_name, SysUser.username).where(
                    SysUser.id == apply.applicant_id, SysUser.tenant_id == self._tenant_id
                )
            )
        ).first()
        applicant_name = None
        if applicant is not None:
            # 无真实姓名时回退登录账号
            applicant_name = str(applicant[0] or applicant[1])
        return (str(dept_name) if dept_name else None, applicant_name)

    async def _assert_supplier(self, supplier_id: int) -> None:
        """校验供应商存在、未删除且启用，避免把订单挂到已停用供应商上。"""
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
            raise AppException(ErrorCode.VALIDATION_ERROR, "供应商已停用，不能下单")

    async def _assert_source_apply(self, apply_id: int | None) -> None:
        """校验来源申请单（仅手工传入时）：存在、未删除、状态为已审。

        仅做单据合法性校验，不做数据范围校验——建单权限由 pur:order:add 控制；
        转单预填接口才做数据范围校验（见 build_from_apply）。
        """
        if apply_id is None:
            return
        apply = await self._apply_repo.get_with_items(apply_id, for_update=False)
        if apply is None or apply.is_deleted:
            raise AppException(ErrorCode.VALIDATION_ERROR, "来源采购申请不存在")
        if apply.status != PurApplyStatus.APPROVED:
            raise AppException(ErrorCode.VALIDATION_ERROR, "来源采购申请不是已审状态")

    async def _load_order_items(self, items: list[PurOrderItemIn]) -> list[PurOrderItemIn]:
        """批量校验订单明细：商品存在且启用、单位属于本租户，一次 IN 查询避免 N+1。"""
        if not items:
            raise AppException(ErrorCode.VALIDATION_ERROR, "订单必须至少有一行明细")
        goods_ids = {item.goods_id for item in items}
        goods_rows = (
            await self._db.execute(
                select(BdGoods.id, BdGoods.name, BdGoods.status).where(
                    BdGoods.id.in_(goods_ids),
                    BdGoods.tenant_id == self._tenant_id,
                    BdGoods.is_deleted.is_(False),
                )
            )
        ).all()
        goods_map = {int(row[0]): (str(row[1]), int(row[2])) for row in goods_rows}

        unit_ids = {item.unit_id for item in items}
        unit_rows = (
            await self._db.execute(
                select(BdUnit.id).where(
                    BdUnit.id.in_(unit_ids), BdUnit.tenant_id == self._tenant_id
                )
            )
        ).scalars()
        valid_units = {int(value) for value in unit_rows.all()}

        for item in items:
            info = goods_map.get(item.goods_id)
            if info is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {item.goods_id}")
            name, status = info
            if status != 1:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品「{name}」已停用，不能下单")
            if item.unit_id not in valid_units:
                raise AppException(ErrorCode.VALIDATION_ERROR, "计量单位不存在")
        return items

    @staticmethod
    def _calc_totals(items: list[PurOrderItemIn]) -> tuple[Decimal, Decimal]:
        """按含税口径重算合计与税额：行金额 round(qty*price,2)，税额 Σ(amount-amount/(1+rate))。

        单价与合计均为含税价，税额是「价税分离」倒推，不能再往合计上加税；
        舍入统一 quantize(0.01) 即 ROUND_HALF_EVEN（采购模块约定第 2 条）。
        """
        total = ZERO
        tax = ZERO
        for item in items:
            amount = (item.qty * item.price).quantize(CENT)
            total += amount
            if item.tax_rate > 0:
                tax += amount - (amount / (Decimal("1") + item.tax_rate)).quantize(CENT)
        return total, tax

    async def _replace_items(
        self, order_id: int, items: list[PurOrderItemIn], operator_id: int
    ) -> None:
        """明细整单替换：先软删旧行（保留流水），再批量插入新行并重算行金额。"""
        await self._repo.soft_delete_items(order_id, operator_id)
        for item in items:
            self._db.add(
                PurOrderItem(
                    order_id=order_id,
                    goods_id=item.goods_id,
                    unit_id=item.unit_id,
                    qty=item.qty,
                    price=item.price,
                    amount=(item.qty * item.price).quantize(CENT),
                    tax_rate=item.tax_rate,
                    inbound_qty=ZERO,
                    apply_item_id=item.apply_item_id,
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _to_out_list(self, rows: list[PurOrder], *, with_items: bool) -> list[PurOrderOut]:
        """批量转换出参：供应商名、审核人、来源申请单号、明细与进度一次查完，避免 N+1。"""
        if not rows:
            return []
        order_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(order_ids)

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

        audit_ids = {row.audit_by for row in rows if row.audit_by is not None}
        audit_names: dict[int, str] = {}
        if audit_ids:
            user_rows = (
                await self._db.execute(
                    select(SysUser.id, SysUser.real_name, SysUser.username).where(
                        SysUser.id.in_(audit_ids), SysUser.tenant_id == self._tenant_id
                    )
                )
            ).all()
            audit_names = {int(row[0]): str(row[1] or row[2]) for row in user_rows}

        apply_ids = {row.source_apply_id for row in rows if row.source_apply_id is not None}
        apply_nos: dict[int, str] = {}
        if apply_ids:
            apply_rows = (
                await self._db.execute(
                    select(PurApply.id, PurApply.apply_no).where(
                        PurApply.id.in_(apply_ids), PurApply.tenant_id == self._tenant_id
                    )
                )
            ).all()
            apply_nos = {int(row[0]): str(row[1]) for row in apply_rows}

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
                {int(item.unit_id) for items in items_map.values() for item in items}
            )

        result: list[PurOrderOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            item_outs = (
                self._build_item_outs(
                    active_items,
                    goods_map,
                    unit_names,
                    closed=row.status == PurOrderStatus.CLOSED,
                )
                if with_items
                else []
            )
            # 入库进度来自明细汇总：列表页直接展示，无需加载明细数组
            total_qty = sum((Decimal(str(i.qty)) for i in active_items), ZERO)
            inbound_qty = sum((Decimal(str(i.inbound_qty)) for i in active_items), ZERO)
            inbound_progress = (
                (inbound_qty / total_qty * Decimal("100")).quantize(CENT)
                if total_qty > 0
                else ZERO
            )
            result.append(
                PurOrderOut.model_validate(row).model_copy(
                    update={
                        "supplier_name": supplier_names.get(row.supplier_id),
                        "audit_by_name": audit_names.get(row.audit_by) if row.audit_by else None,
                        "source_apply_no": (
                            apply_nos.get(row.source_apply_id) if row.source_apply_id else None
                        ),
                        "items_count": len(active_items),
                        "items": item_outs,
                        "total_qty": total_qty,
                        "inbound_qty": inbound_qty,
                        "inbound_progress": inbound_progress,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[PurOrderItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
        *,
        closed: bool,
    ) -> list[PurOrderItemOut]:
        """把明细行转换成出参；关闭后剩余可入库数量作废（展示为 0）。"""
        result: list[PurOrderItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            qty = Decimal(str(item.qty))
            inbound_qty = Decimal(str(item.inbound_qty))
            remaining = ZERO if closed else max(qty - inbound_qty, ZERO)
            result.append(
                PurOrderItemOut(
                    id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_id=item.unit_id,
                    unit_name=unit_names.get(int(item.unit_id)),
                    qty=item.qty,
                    price=item.price,
                    amount=item.amount,
                    tax_rate=item.tax_rate,
                    inbound_qty=item.inbound_qty,
                    remaining_qty=remaining,
                    apply_item_id=item.apply_item_id,
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        order_id: int,
        order_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。"""
        params: dict[str, Any] = {"orderId": order_id, "orderNo": order_no}
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
