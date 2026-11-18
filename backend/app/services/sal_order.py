"""销售订单业务逻辑（SAL-02/04~09/15）：草稿 CRUD、状态机、报价转单、信用校验与操作日志。

数据闭环：
  sal_quotation（已发送）→ 转单预填 / convert → sal_order 草稿 → 提交（待审）
  → 审核通过（已审，做 SAL-15 信用校验）/ 驳回（回草稿）
  → 后续 SAL-10/11 出库确认回写 outbound_qty 并驱动 部分出库/完成
  → 手工关闭后剩余未出库数量作废。
  本模块**不写库存（inv_stock / inv_stock_flow）、不写应收（fin_receivable）**。

与需求原文的偏差（以已落库的表结构为准）：
  - SAL-07 原文「审核锁库存」；模型 SalOrder 注释明确「订单无仓库字段（G3），
    审核只做客户信用校验，锁库存发生在出库草稿」。因此审核动作只做信用校验 + 状态推进，
    锁定/释放由后续出库模块（sal_outbound 草稿保存/删除）负责。

敏感点（事务 / 锁 / 状态机）：
  1. 所有用户触发的写操作先按数据范围取单据，并对主表加行锁
     （get_with_lock(for_update=True)），保证「读状态 → 校验 → 改状态」并发下不被穿插。
  2. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定（冲突 409）。
  3. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False；
     convert 同时改报价与订单两张表，同一事务提交保证「要么都成、要么都不成」。
  4. 明细整单替换采用「软删旧行 + 新增」，历史明细保留（is_deleted=True）。
  5. 信用校验（SAL-15）先对 bd_customer 行锁再汇总占用，串行化同一客户的并发下单/审核，
     防止并发突破额度；credit_limit=0 视为不启用信用控制。
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer, BdGoods, BdUnit
from app.models.finance import FinReceivable
from app.models.sal import SalOrder, SalOrderItem, SalQuotation, SalQuotationItem
from app.models.sys import SysUser
from app.repositories.sal_order import SalOrderRepository
from app.repositories.sal_quotation import SalQuotationRepository
from app.schemas.common import PageResult
from app.schemas.sal_order import (
    AUDIT_RESULT_APPROVED,
    CLOSABLE_STATUSES,
    CREDIT_OCCUPYING_STATUSES,
    ORDER_STATUS_TEXT,
    SalOrderAudit,
    SalOrderClose,
    SalOrderConvert,
    SalOrderCreate,
    SalOrderFromQuotationItem,
    SalOrderFromQuotationOut,
    SalOrderItemIn,
    SalOrderItemOut,
    SalOrderOut,
    SalOrderQuery,
    SalOrderStatus,
    SalOrderUpdate,
)
from app.schemas.sal_quotation import SalQuotationStatus
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.optimistic_lock import bump_version
from app.utils.pagination import paginate_result

# 金额与数量统一精度：ROUND_HALF_EVEN（银行家舍入），禁止改为 ROUND_HALF_UP
CENT = Decimal("0.01")
ZERO = Decimal("0")


class SalOrderService:
    """销售订单：查询 / 草稿 CRUD / 提交审核关闭 / 报价转单 / 信用概览。"""

    # 单号规则键：sys_config.billno.sal_order = "SO,4"
    ORDER_BIZ_KEY = "sal_order"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "销售管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = SalOrderRepository(db, tenant_id=tenant_id)
        self._quotation_repo = SalQuotationRepository(db, tenant_id=tenant_id)

    # ---------- 查询 ----------

    async def list_orders(self, query: SalOrderQuery, user: SysUser) -> PageResult[SalOrderOut]:
        """分页查询订单：先按角色数据范围过滤，再叠加状态/客户/日期/关键字条件。

        sal_order 无 dept_id 列（设计文档 7.3），数据范围按创建人所属部门子树过滤
        （与采购订单 pur_order、销售报价 sal_quotation 的处理方式一致）。
        """
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, SalOrder, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_order(self, order_id: int, user: SysUser) -> SalOrderOut:
        """详情（含明细与出库进度）：单据必须落在当前用户可见数据范围内，否则 403。"""
        ctx = await resolve_scope(self._db, user)
        order = await self._get_visible(order_id, ctx, for_update=False)
        return (await self._to_out_list([order], with_items=True))[0]

    async def credit_info(self, customer_id: int, user: SysUser) -> dict[str, Any]:
        """客户信用概览（SAL-15 前端展示）：额度、已占用、可用余额。

        credit_limit=0 表示不启用信用控制，可用余额返回 None（前端不显示限制）。
        """
        customer = await self._load_customer(customer_id, for_update=False)
        summary = await self._credit_summary(customer)
        limit = summary["credit_limit"]
        occupied = summary["occupied_amount"]
        summary["available_amount"] = max(limit - occupied, ZERO) if limit > ZERO else None
        return summary

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_order(
        self, payload: SalOrderCreate, user: SysUser, *, commit: bool = True
    ) -> SalOrderOut:
        """新建销售订单（落库即草稿）：自动取号，金额后端重算，SAL-15 信用校验（提前拦截）。

        草稿不占用信用额度（占用从「已审」开始），但创建时按
        「已占用 + 本单金额 ≤ 额度」预检，让超额客户在建单阶段就得到明确提示。
        """
        try:
            await self._assert_customer_enabled(payload.customer_id)
            items = await self._load_order_items(payload.items)
            subtotal, tax_amount = self._calc_totals(items, payload.discount)
            # 信用预检：对客户行锁后再汇总，防止并发建单误导出超额提示（SAL-15）
            await self._assert_credit(payload.customer_id, subtotal)
            # 取号与单号查重共用事务锁，避免并发生成重复单号
            await self._repo.lock_for_write()
            order_no = await generate_bill_no(self._db, self.ORDER_BIZ_KEY)
            order = SalOrder(
                order_no=order_no,
                customer_id=payload.customer_id,
                order_date=payload.order_date,
                status=SalOrderStatus.DRAFT,
                total_amount=subtotal,
                tax_amount=tax_amount,
                discount=payload.discount,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(order)
            await self._replace_items(order.id, items, user.id)
            await self._write_log(user.id, "新增销售订单", order.id, order.order_no)
            created = await self._repo.get_with_lock(order.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "销售订单创建失败")
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
        payload: SalOrderUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> SalOrderOut:
        """修改草稿：仅草稿可改（其余状态 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != SalOrderStatus.DRAFT:
                status_text = ORDER_STATUS_TEXT.get(order.status, str(order.status))
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"订单当前为「{status_text}」，只有草稿状态可以修改",
                )
            # 先抢占版本号再动数据：并发修改时后到者在改明细之前就被拒绝
            await self._bump_version(order, payload.version, user.id)
            data = payload.model_dump(exclude_unset=True)
            if data.get("customer_id") is not None:
                await self._assert_customer_enabled(data["customer_id"])
                order.customer_id = data["customer_id"]
            if data.get("order_date") is not None:
                order.order_date = data["order_date"]
            if "remark" in data:
                order.remark = data["remark"]
            if payload.items is not None:
                items = await self._load_order_items(payload.items)
                await self._replace_items(order.id, items, user.id)
            # 折扣与明细任意一个变化都按「当前有效明细」重算合计（明细变了必须重算金额）
            if payload.items is not None or data.get("discount") is not None:
                if data.get("discount") is not None:
                    order.discount = data["discount"]
                active_items = await self._active_items(order.id)
                subtotal, tax_amount = self._calc_totals(
                    self._stored_to_calc(active_items), order.discount
                )
                order.total_amount = subtotal
                order.tax_amount = tax_amount
            order.updated_by = user.id
            await self._write_log(user.id, "修改销售订单", order.id, order.order_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
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
            if order.status != SalOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售订单可以删除")
            await self._bump_version(order, None, user.id)
            order.is_deleted = True
            order.updated_by = user.id
            await self._repo.soft_delete_items(order.id, user.id)
            await self._write_log(user.id, "删除销售订单", order.id, order.order_no)
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
    ) -> SalOrderOut:
        """提交：草稿 → 待审；提交前复核客户/商品启用与明细合法性（最后一道校验）。"""
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != SalOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售订单可以提交")
            active_items = await self._active_items(order.id)
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "订单没有明细行，不能提交")
            await self._assert_items_submittable(active_items)
            await self._assert_customer_enabled(order.customer_id)
            await self._bump_version(order, version, user.id)
            order.status = SalOrderStatus.PENDING
            # 重新提交时清空上一次驳回原因，避免历史意见误导审核人
            order.audit_remark = None
            order.updated_by = user.id
            await self._write_log(user.id, "提交销售订单", order.id, order.order_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
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
        payload: SalOrderAudit,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> SalOrderOut:
        """审核：待审 → 已审（通过）/ 草稿（驳回并写原因）；审核人/日期落库。

        通过时做 SAL-15 信用校验：对客户行锁后汇总「未结清应收 + 已审未出库占用」，
        本单在待审态尚未计入占用，故直接 用占用 + 本单金额 与额度比较；
        超额拒绝并提示额度与占用明细，整单不落「已审」状态。

        审核只推进状态，不改写明细数量、不锁库存、不写应收（偏差说明见模块 docstring）。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status != SalOrderStatus.PENDING:
                status_text = ORDER_STATUS_TEXT.get(order.status, str(order.status))
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"订单当前为「{status_text}」，只有待审状态的销售订单可以审核",
                )
            remark = (payload.remark or "").strip() or None
            if payload.result == AUDIT_RESULT_APPROVED:
                # 信用硬闸门：通过前先校验，失败则整单保持待审（异常回滚，无状态变更）
                await self._assert_credit(order.customer_id, Decimal(order.total_amount))
                order.status = SalOrderStatus.APPROVED
                action = "审核通过销售订单"
            else:
                order.status = SalOrderStatus.DRAFT
                action = "驳回销售订单"
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
                raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
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
        payload: SalOrderClose,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> SalOrderOut:
        """关闭：已审/部分出库 → 关闭，剩余未出库数量作废，之后不可再出库（SAL-08）。

        关闭原因复用 audit_remark 存储，不新增列、不改迁移基线，前端按状态显示为「关闭原因」。
        已完成的订单不允许关闭；锁定库存的释放由出库模块在出库单删除/关闭时处理。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            order = await self._get_visible(order_id, ctx, for_update=True)
            if order.status == SalOrderStatus.COMPLETED:
                raise AppException(ErrorCode.CONFLICT, "已全部出库的订单不能关闭")
            if order.status not in CLOSABLE_STATUSES:
                raise AppException(ErrorCode.CONFLICT, "只有已审或部分出库的销售订单可以关闭")
            reason = (payload.reason or "").strip() or None
            await self._bump_version(order, payload.version, user.id)
            order.status = SalOrderStatus.CLOSED
            order.audit_remark = reason
            order.updated_by = user.id
            await self._write_log(
                user.id,
                "关闭销售订单",
                order.id,
                order.order_no,
                extra={"reason": reason},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(order.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 报价转单（SAL-02） ----------

    async def build_from_quotation(
        self, quotation_id: int, user: SysUser
    ) -> SalOrderFromQuotationOut:
        """按已发送报价单组装订单预填数据，供前端填充新建订单表单（只读，不落库）。

        校验链：报价单存在且未软删（404）→ 数据范围允许（403）→ 状态已发送（409）。
        同时带出客户信用额度概览（SAL-15），供下单页直接展示。
        """
        ctx = await resolve_scope(self._db, user)
        quotation = await self._get_quotation_visible(quotation_id, ctx, for_update=False)
        if quotation.status == SalQuotationStatus.CONVERTED:
            raise AppException(ErrorCode.CONFLICT, "报价单已转为销售订单，不能重复转单")
        if quotation.status != SalQuotationStatus.SENT:
            status_text = "已失效" if quotation.status == SalQuotationStatus.INVALID else "草稿"
            raise AppException(
                ErrorCode.CONFLICT, f"报价单当前为「{status_text}」，只有已发送的报价可以转单"
            )
        items_map = await self._quotation_repo.list_items([int(quotation.id)])
        active_items = [
            item for item in items_map.get(int(quotation.id), []) if not item.is_deleted
        ]
        if not active_items:
            raise AppException(ErrorCode.VALIDATION_ERROR, "报价单没有可用明细，不能转单")
        goods_map = await self._load_goods({int(item.goods_id) for item in active_items})
        unit_names = await self._load_unit_names(
            {int(goods.unit_id) for goods in goods_map.values() if goods.unit_id is not None}
        )
        customer = await self._load_customer(int(quotation.customer_id), for_update=False)
        summary = await self._credit_summary(customer)
        item_outs: list[SalOrderFromQuotationItem] = []
        for item in active_items:
            goods = goods_map.get(int(item.goods_id))
            unit_id = int(goods.unit_id) if goods is not None and goods.unit_id else None
            item_outs.append(
                SalOrderFromQuotationItem(
                    goods_id=int(item.goods_id),
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_id=unit_id,
                    unit_name=unit_names.get(unit_id) if unit_id else None,
                    qty=Decimal(item.qty),
                    price=Decimal(item.price),
                    tax_rate=ZERO,
                )
            )
        return SalOrderFromQuotationOut(
            source_quotation_id=int(quotation.id),
            quote_no=quotation.quote_no,
            customer_id=int(quotation.customer_id),
            customer_name=customer.name if customer else None,
            credit_limit=summary["credit_limit"],
            occupied_amount=summary["occupied_amount"],
            order_date=date.today(),
            remark=f"来源报价单：{quotation.quote_no}",
            items=item_outs,
        )

    async def convert_quotation(
        self,
        quotation_id: int,
        payload: SalOrderConvert,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> SalOrderOut:
        """报价转订单（SAL-02）：生成订单草稿并把报价置为「已转订单」。

        明细与价格直接从报价单复制（保证「转单后明细/价格一致」，前端不可改价提交）；
        同一报价不可重复转单（状态=已转订单后 409）。报价与订单在同一事务落库，
        任一失败整体回滚，避免出现「订单建了但报价没标转单」的中间态。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            quotation = await self._get_quotation_visible(quotation_id, ctx, for_update=True)
            if quotation.status == SalQuotationStatus.CONVERTED:
                raise AppException(ErrorCode.CONFLICT, "报价单已转为销售订单，不能重复转单")
            if quotation.status != SalQuotationStatus.SENT:
                status_text = "已失效" if quotation.status == SalQuotationStatus.INVALID else "草稿"
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"报价单当前为「{status_text}」，只有已发送的报价可以转单",
                )
            items_map = await self._quotation_repo.list_items([int(quotation.id)])
            active_items = [
                item for item in items_map.get(int(quotation.id), []) if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "报价单没有可用明细，不能转单")
            # 明细转订单行：单位取商品档案默认单位（报价明细无单位字段）
            items = await self._items_from_quotation(active_items)
            await self._assert_customer_enabled(int(quotation.customer_id))
            subtotal, tax_amount = self._calc_totals(items, payload.discount)
            # 信用预检（SAL-15）：与手工建单同一口径
            await self._assert_credit(int(quotation.customer_id), subtotal)
            await self._repo.lock_for_write()
            order_no = await generate_bill_no(self._db, self.ORDER_BIZ_KEY)
            remark = payload.remark if payload.remark is not None else quotation.remark
            if remark and not remark.startswith("来源报价单："):
                remark = f"{remark}（来源报价单：{quotation.quote_no}）"
            else:
                remark = remark or f"来源报价单：{quotation.quote_no}"
            order = SalOrder(
                order_no=order_no,
                customer_id=int(quotation.customer_id),
                order_date=payload.order_date,
                status=SalOrderStatus.DRAFT,
                total_amount=subtotal,
                tax_amount=tax_amount,
                discount=payload.discount,
                remark=remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(order)
            await self._replace_items(order.id, items, user.id)
            # 报价置「已转订单」：复用 bump_version 抢占报价乐观锁，防并发转单
            quotation.status = SalQuotationStatus.CONVERTED
            quotation.updated_by = user.id
            await bump_version(
                self._db,
                SalQuotation,
                quotation,
                tenant_id=self._tenant_id,
                expected=None,
                user_id=user.id,
            )
            await self._write_log(
                user.id,
                "报价转销售订单",
                order.id,
                order.order_no,
                extra={"quoteNo": quotation.quote_no},
            )
            await self._write_log(
                user.id,
                "销售报价转单",
                int(quotation.id),
                quotation.quote_no,
                extra={"orderNo": order_no},
            )
            await self._db.flush()
            created = await self._repo.get_with_lock(order.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "销售订单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _get_visible(self, order_id: int, ctx: ScopeContext, *, for_update: bool) -> SalOrder:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。

        用户触发的列表详情之外的所有写操作都走这里，防止改 URL 越权操作他人订单。
        """
        order = await self._repo.get_with_lock(order_id, for_update=for_update)
        if order is None or order.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售订单不存在")
        if not await allows(self._db, ctx, created_by=order.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售订单")
        return order

    async def _get_quotation_visible(
        self, quotation_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> SalQuotation:
        """按 ID 取报价单并做数据范围校验（转单入口共用）。"""
        quotation = await self._quotation_repo.get_with_items(quotation_id, for_update=for_update)
        if quotation is None or quotation.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售报价不存在")
        if not await allows(self._db, ctx, created_by=quotation.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售报价")
        return quotation

    async def _bump_version(self, order: SalOrder, expected: int | None, user_id: int) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409「数据已被他人修改」。

        SQLite 无 SELECT ... FOR UPDATE，只比较内存 version 拦不住并发；
        synchronize_session=False 避免 ORM 对象被同步成新版本后回写覆盖，
        后续业务字段的 flush 只更新脏列，不会把 version 改回去。
        """
        current = int(order.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(SalOrder)
            .where(
                SalOrder.id == order.id,
                SalOrder.tenant_id == self._tenant_id,
                SalOrder.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _active_items(self, order_id: int) -> list[SalOrderItem]:
        """取订单当前有效明细行（排除编辑草稿时软删的历史行）。"""
        items_map = await self._repo.list_items([int(order_id)])
        return [item for item in items_map.get(int(order_id), []) if not item.is_deleted]

    @staticmethod
    def _stored_to_calc(items: list[SalOrderItem]) -> list[SalOrderItemIn]:
        """把库中明细行转成计算入参形态（重算合计用），字段语义一一对应。"""
        return [
            SalOrderItemIn(
                goods_id=int(item.goods_id),
                unit_id=int(item.unit_id),
                qty=Decimal(item.qty),
                price=Decimal(item.price),
                tax_rate=Decimal(item.tax_rate),
            )
            for item in items
        ]

    async def _assert_items_submittable(self, items: list[SalOrderItem]) -> None:
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
        """批量加载商品（一次 IN 查询），供转单预填与明细转换使用。"""
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

    async def _assert_customer_enabled(self, customer_id: int) -> None:
        """校验客户存在、未删除且启用，避免把订单挂到已停用客户上。"""
        customer = await self._load_customer(customer_id, for_update=False)
        if customer.status != 1:
            raise AppException(
                ErrorCode.VALIDATION_ERROR, f"客户「{customer.name}」已停用，不能下单"
            )

    async def _load_customer(self, customer_id: int, *, for_update: bool) -> BdCustomer:
        """按 ID 取客户；不存在/已软删返回校验错误。

        for_update=True 时对客户行加锁（SAL-15 信用校验的串行化入口）：
        同一客户的并发下单/审核在「读占用 → 比较」临界区上排队，防止并发突破额度。
        """
        stmt = select(BdCustomer).where(
            BdCustomer.id == customer_id,
            BdCustomer.tenant_id == self._tenant_id,
            BdCustomer.is_deleted.is_(False),
        )
        if for_update:
            stmt = stmt.with_for_update()
        customer = (await self._db.execute(stmt)).scalar_one_or_none()
        if customer is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, f"客户不存在: {customer_id}")
        return customer

    async def _credit_summary(self, customer: BdCustomer) -> dict[str, Any]:
        """汇总客户信用占用（SAL-15）：

        已占用 = 未结清应收净额（fin_receivable.unreceived_amount 合计，不含已结清）
               + Σ 已审/部分出库订单的未出库金额（total_amount - 已确认出库金额）。
        出库确认后金额切换为应收占用，订单侧扣减已出库部分，避免同一笔重复计入。
        """
        customer_id = int(customer.id)
        receivable_stmt = (
            select(func.coalesce(func.sum(FinReceivable.unreceived_amount), ZERO))
            .select_from(FinReceivable)
            .where(
                FinReceivable.customer_id == customer_id,
                FinReceivable.tenant_id == self._tenant_id,
                FinReceivable.is_deleted.is_(False),
            )
        )
        receivable_total = Decimal((await self._db.execute(receivable_stmt)).scalar() or ZERO)
        order_stmt = (
            select(SalOrder.id, SalOrder.total_amount)
            .select_from(SalOrder)
            .where(
                SalOrder.customer_id == customer_id,
                SalOrder.tenant_id == self._tenant_id,
                SalOrder.is_deleted.is_(False),
                SalOrder.status.in_(CREDIT_OCCUPYING_STATUSES),
            )
        )
        order_rows = (await self._db.execute(order_stmt)).all()
        occupied = receivable_total
        for row in order_rows:
            shipped = await self._repo.shipped_amount(int(row[0]))
            remaining = Decimal(row[1]) - shipped
            if remaining > ZERO:
                occupied += remaining
        return {
            "customer_id": customer_id,
            "customer_name": customer.name,
            "credit_limit": Decimal(customer.credit_limit),
            "credit_days": int(customer.credit_days),
            "occupied_amount": occupied,
        }

    async def _assert_credit(self, customer_id: int, order_amount: Decimal) -> None:
        """信用校验硬闸门（SAL-15）：占用 + 本单金额 > 额度 时拒绝（409）。

        credit_limit=0 视为不启用信用控制。行锁在 _load_customer(for_update=True) 上，
        保证并发下单/审核时「读占用 → 比较」对同一客户串行执行。
        """
        customer = await self._load_customer(customer_id, for_update=True)
        summary = await self._credit_summary(customer)
        limit = summary["credit_limit"]
        if limit <= ZERO:
            return
        occupied = summary["occupied_amount"]
        if occupied + order_amount > limit:
            raise AppException(
                ErrorCode.CONFLICT,
                f"客户「{customer.name}」信用额度不足：额度 {limit}，已占用 {occupied}，"
                f"本单 {order_amount}，超出额度 {(occupied + order_amount) - limit}",
            )

    async def _items_from_quotation(
        self, quotation_items: list[SalQuotationItem]
    ) -> list[SalOrderItemIn]:
        """报价明细 → 订单明细入参：数量/单价原样复制，单位取商品档案，税率默认 0。"""
        goods_map = await self._load_goods({int(item.goods_id) for item in quotation_items})
        items: list[SalOrderItemIn] = []
        for item in quotation_items:
            goods = goods_map.get(int(item.goods_id))
            if goods is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {item.goods_id}")
            if goods.status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{goods.name}」已停用，不能转单"
                )
            if goods.unit_id is None:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    f"商品「{goods.name}」未设置计量单位，请先在商品档案补齐后转单",
                )
            items.append(
                SalOrderItemIn(
                    goods_id=int(item.goods_id),
                    unit_id=int(goods.unit_id),
                    qty=Decimal(item.qty),
                    price=Decimal(item.price),
                    tax_rate=ZERO,
                )
            )
        return items

    async def _load_order_items(self, items: list[SalOrderItemIn]) -> list[SalOrderItemIn]:
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
    def _calc_totals(items: list[SalOrderItemIn], discount: Decimal) -> tuple[Decimal, Decimal]:
        """按含税口径重算合计与税额：行金额 round(qty*price,2)，税额 Σ(amount-amount/(1+rate))。

        discount 为整单折扣金额：total = Σ行金额 - discount（折扣超过行金额合计直接 422）；
        税额按「折扣后金额 / 折扣前金额」比例同口径缩放（模型注释 G8），合计仍 quantize(0.01)。
        舍入统一 quantize(0.01) 即 ROUND_HALF_EVEN（销售模块与采购模块约定一致）。
        """
        subtotal = ZERO
        tax = ZERO
        for item in items:
            amount = (item.qty * item.price).quantize(CENT)
            subtotal += amount
            if item.tax_rate > 0:
                tax += amount - (amount / (Decimal("1") + item.tax_rate)).quantize(CENT)
        if discount > subtotal:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"整单折扣 {discount} 不能超过行金额合计 {subtotal}",
            )
        total = subtotal - discount
        if subtotal > ZERO and discount > ZERO:
            scale = total / subtotal
            tax = (tax * scale).quantize(CENT)
        return total, tax

    async def _replace_items(
        self, order_id: int, items: list[SalOrderItemIn], operator_id: int
    ) -> None:
        """明细整单替换：先软删旧行（保留流水），再批量插入新行并重算行金额。"""
        await self._repo.soft_delete_items(order_id, operator_id)
        for item in items:
            self._db.add(
                SalOrderItem(
                    order_id=order_id,
                    goods_id=item.goods_id,
                    unit_id=item.unit_id,
                    qty=item.qty,
                    price=item.price,
                    amount=(item.qty * item.price).quantize(CENT),
                    tax_rate=item.tax_rate,
                    outbound_qty=ZERO,
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _to_out_list(self, rows: list[SalOrder], *, with_items: bool) -> list[SalOrderOut]:
        """批量转换出参：客户名、审核人、明细与出库进度一次查完，避免 N+1。"""
        if not rows:
            return []
        order_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(order_ids)

        customer_ids = {row.customer_id for row in rows}
        customer_names: dict[int, str] = {}
        if customer_ids:
            customer_rows = (
                await self._db.execute(
                    select(BdCustomer.id, BdCustomer.name).where(
                        BdCustomer.id.in_(customer_ids), BdCustomer.tenant_id == self._tenant_id
                    )
                )
            ).all()
            customer_names = {int(row[0]): str(row[1]) for row in customer_rows}

        audit_ids = {row.audit_by for row in rows if row.audit_by is not None}
        audit_names: dict[int, str] = {}
        if audit_ids:
            audit_rows = (
                await self._db.execute(
                    select(SysUser.id, SysUser.real_name, SysUser.username).where(
                        SysUser.id.in_(audit_ids), SysUser.tenant_id == self._tenant_id
                    )
                )
            ).all()
            for row in audit_rows:
                # 无真实姓名时回退登录账号
                audit_names[int(row[0])] = str(row[1] or row[2])

        # 出库进度：按订单聚合已确认出库数量（SAL-09）
        outbound_totals = await self._repo.outbound_totals(order_ids)

        # 行数直接由已批量查回的明细统计（排除软删行），无需再发 count 查询
        goods_map, unit_names = await self._load_detail_info(items_map) if with_items else ({}, {})

        result: list[SalOrderOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            total_qty = sum((Decimal(item.qty) for item in active_items), ZERO)
            outbound_qty = outbound_totals.get(int(row.id), ZERO)
            if total_qty > ZERO:
                progress = ((outbound_qty / total_qty) * Decimal("100")).quantize(CENT)
            else:
                progress = ZERO
            item_outs = (
                self._build_item_outs(active_items, goods_map, unit_names) if with_items else []
            )
            result.append(
                SalOrderOut.model_validate(row).model_copy(
                    update={
                        "customer_name": customer_names.get(row.customer_id),
                        "audit_by_name": (
                            audit_names.get(row.audit_by) if row.audit_by is not None else None
                        ),
                        "items_count": len(active_items),
                        "total_qty": total_qty,
                        "outbound_qty": outbound_qty,
                        "outbound_progress": progress,
                        "items": item_outs,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[SalOrderItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
    ) -> list[SalOrderItemOut]:
        """把明细行转换成出参（只处理未软删行）；商品编码/名称/规格/单位来自批量查询结果。"""
        result: list[SalOrderItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            unit_name = unit_names.get(int(item.unit_id))
            qty = Decimal(item.qty)
            outbound_qty = Decimal(item.outbound_qty)
            result.append(
                SalOrderItemOut(
                    id=int(item.id),
                    goods_id=int(item.goods_id),
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_id=int(item.unit_id),
                    unit_name=unit_name,
                    qty=qty,
                    price=Decimal(item.price),
                    amount=Decimal(item.amount),
                    tax_rate=Decimal(item.tax_rate),
                    outbound_qty=outbound_qty,
                    remaining_qty=qty - outbound_qty,
                )
            )
        return result

    async def _load_detail_info(
        self, items_map: dict[int, list[SalOrderItem]]
    ) -> tuple[dict[int, BdGoods], dict[int, str]]:
        """一次性加载明细涉及的商品与单位名称（详情/打印用），避免逐行查商品。"""
        goods_ids = {
            int(item.goods_id)
            for items in items_map.values()
            for item in items
            if not item.is_deleted
        }
        unit_ids = {
            int(item.unit_id)
            for items in items_map.values()
            for item in items
            if not item.is_deleted
        }
        if not goods_ids and not unit_ids:
            return {}, {}
        goods_map: dict[int, BdGoods] = {}
        if goods_ids:
            goods_rows = (
                await self._db.execute(
                    select(BdGoods).where(
                        BdGoods.id.in_(goods_ids), BdGoods.tenant_id == self._tenant_id
                    )
                )
            ).scalars()
            goods_map = {int(goods.id): goods for goods in goods_rows.all()}
        unit_names = await self._load_unit_names(unit_ids)
        return goods_map, unit_names

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        order_id: int,
        order_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。

        提交/审核/关闭/转单链路逐条记录 action，供验收「操作日志完整」与追溯对账使用。
        """
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
