"""采购订单业务逻辑（P-02）：草稿 CRUD、从已审申请单转单预填、操作日志。

数据闭环：
  pur_apply（已审）→ 转单预填接口 → 前端填单 → POST /purchase/orders 生成 pur_order 草稿
  → pur_order.source_apply_id / pur_order_item.apply_item_id 记录溯源。
  本模块**不写库存（inv_stock / inv_stock_flow）、不写财务（fin_payable / fin_receivable）**，
  入库与应付由后续 P-03/P-04 负责。

敏感点（事务 / 锁 / 状态机）：
  1. 修改、删除草稿先对主表加行锁（get_with_lock(for_update=True)），
     保证「读状态 → 校验 → 改状态」并发下不被穿插。
  2. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False。
  3. 明细整单替换采用「软删旧行 + 新增」，历史明细保留（is_deleted=True）。
  4. 转单预填为**只读**操作，不改变申请单状态、不落库。
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import allows, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdSupplier, BdUnit
from app.models.pur import PurApply, PurOrder, PurOrderItem
from app.models.sys import SysDept, SysUser
from app.repositories.purchase_apply import PurApplyRepository
from app.repositories.purchase_order import PurOrderRepository
from app.schemas.common import PageResult
from app.schemas.purchase_apply import PurApplyStatus
from app.schemas.purchase_order import (
    PurOrderCreate,
    PurOrderFromApplyItem,
    PurOrderFromApplyOut,
    PurOrderItemIn,
    PurOrderItemOut,
    PurOrderOut,
    PurOrderQuery,
    PurOrderStatus,
    PurOrderUpdate,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result


class PurchaseOrderService:
    """采购订单：查询 / 草稿新增修改删除 / 从已审申请单转单预填。"""

    # 单号规则键：sys_config.billno.pur_order = "PO,4"
    ORDER_BIZ_KEY = "pur_order"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "采购管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = PurOrderRepository(db, tenant_id=tenant_id)
        self._apply_repo = PurApplyRepository(db, tenant_id=tenant_id)

    # ---------- 转单预填（只读） ----------

    async def build_from_apply(self, apply_id: int, user: SysUser) -> PurOrderFromApplyOut:
        """按已审采购申请单组装订单预填数据，供前端填充新建订单表单。

        校验链：
          1. 申请单存在且未软删（否则 404）；
          2. 数据范围允许访问该申请单（否则 403）；
          3. 申请单状态必须为「已审」（否则 409）。
        只读：不修改申请单、不生成订单、不写操作日志（查询类操作不产生审计噪音）。
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
                    price=goods.cost_price if goods else Decimal("0"),
                    tax_rate=Decimal("0"),
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

    # ---------- 查询 ----------

    async def list_orders(
        self, query: PurOrderQuery, user: SysUser
    ) -> PageResult[PurOrderOut]:
        """分页查询订单：按状态/供应商/下单日期/来源申请单筛选，租户由仓储层保证。"""
        stmt = self._repo.build_query(query)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_order(self, order_id: int) -> PurOrderOut:
        """订单详情（含明细）；不存在或已软删抛 404。"""
        order = await self._repo.get_with_lock(order_id, for_update=False)
        if order is None or order.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")

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
        """修改草稿：仅草稿可改，其余状态一律 409；明细传了则整单替换（软删旧行保留流水）。"""
        try:
            order = await self._repo.get_with_lock(order_id, for_update=True)
            if order is None or order.is_deleted:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            if order.status != PurOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购订单可以修改")
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
            order.version += 1
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
            order = await self._repo.get_with_lock(order_id, for_update=True)
            if order is None or order.is_deleted:
                raise AppException(ErrorCode.NOT_FOUND, "采购订单不存在")
            if order.status != PurOrderStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的采购订单可以删除")
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


    # ---------- 内部辅助 ----------

    async def _load_goods(self, goods_ids: set[int]) -> dict[int, BdGoods]:
        """批量加载商品（一次 IN 查询），供转单预填使用。"""
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

        仅做「单据合法性」校验，不做数据范围校验——建单权限由 pur:order:add 控制；
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
        """按含税口径重算合计与税额：行金额 round(qty*price,2)，税额 Σ(amount-amount/(1+rate))。"""
        total = Decimal("0")
        tax = Decimal("0")
        for item in items:
            amount = (item.qty * item.price).quantize(Decimal("0.01"))
            total += amount
            if item.tax_rate > 0:
                tax += amount - (amount / (Decimal("1") + item.tax_rate)).quantize(
                    Decimal("0.01")
                )
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
                    amount=(item.qty * item.price).quantize(Decimal("0.01")),
                    tax_rate=item.tax_rate,
                    inbound_qty=Decimal("0"),
                    apply_item_id=item.apply_item_id,
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()


    async def _to_out_list(self, rows: list[PurOrder], *, with_items: bool) -> list[PurOrderOut]:
        """批量转换出参：供应商名、审核人、来源申请单号、明细与商品一次查完，避免 N+1。"""
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
                self._build_item_outs(active_items, goods_map, unit_names) if with_items else []
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
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[PurOrderItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
    ) -> list[PurOrderItemOut]:
        """把明细行转换成出参（只处理未软删行）。"""
        result: list[PurOrderItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
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
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。

        日志仅用于审计溯源，不参与任何业务校验。
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

