"""销售报价业务逻辑（SA-01）：草稿 CRUD、发送、作废、数据范围与操作日志。

数据闭环（需求条目拆分.md SA-01）：
  sal_quotation + sal_quotation_item → 打印 / 「复制为新订单」前端预填跳转销售订单
  （V1 不做后端转单关联）。本模块**不写库存（inv_stock / inv_stock_flow）、
  不写财务（fin_receivable）**，仅在 sal_quotation、sal_quotation_item 与
  sys_oper_log 三处落库。

敏感点（事务 / 锁 / 状态机）：
  1. 发送、作废、修改草稿都先对主表加行锁（get_with_items(for_update=True)），
     保证「读状态 → 校验 → 改状态」在并发请求下不被穿插，避免重复发送/重复作废。
  2. 事务边界在 Service：写方法带 commit=True 时自行提交，端点用幂等包装时传 commit=False。
  3. 修改草稿的明细采用「整行软删 + 新增」，历史明细保留（is_deleted=True），不物理删除。
  4. 过期报价不能发送：发送时校验 valid_until >= 今天（草稿保存时不校验“是否已过期”，
     因为草稿可以放置多日后再发送；过期拦截发生在发送动作上，验收口径一致）。
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer, BdGoods, BdUnit
from app.models.sal import SalQuotation, SalQuotationItem
from app.models.sys import SysUser
from app.repositories.sal_quotation import SalQuotationRepository
from app.schemas.common import PageResult
from app.schemas.sal_quotation import (
    STATUS_TEXTS,
    SalQuotationCreate,
    SalQuotationItemIn,
    SalQuotationItemOut,
    SalQuotationOut,
    SalQuotationQuery,
    SalQuotationStatus,
    SalQuotationUpdate,
)
from app.services.audit import write_oper_log
from app.utils.bill_no import generate_bill_no
from app.utils.optimistic_lock import bump_version
from app.utils.pagination import paginate_result


class SalQuotationService:
    """销售报价：查询 / 草稿新增修改删除 / 发送 / 作废。"""

    # 单号规则键：sys_config.billno.sal_quotation = "QT,4"
    QUOTATION_BIZ_KEY = "sal_quotation"
    # 操作日志模块名，前端日志页按此筛选
    OPER_MODULE = "销售管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = SalQuotationRepository(db, tenant_id=tenant_id)

    # ---------- 查询 ----------

    async def list_quotations(
        self, query: SalQuotationQuery, user: SysUser
    ) -> PageResult[SalQuotationOut]:
        """分页查询报价单：先按角色数据范围过滤，再叠加状态/日期/关键字条件。

        sal_quotation 无 dept_id 列（设计文档 7.1），数据范围按创建人所属部门子树过滤
        （与采购订单 pur_order 的处理方式一致）。
        """
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, SalQuotation, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_quotation(self, quotation_id: int, user: SysUser) -> SalQuotationOut:
        """详情（含明细）：单据必须落在当前用户可见数据范围内，否则 403。"""
        ctx = await resolve_scope(self._db, user)
        quotation = await self._get_visible(quotation_id, ctx, for_update=False)
        return (await self._to_out_list([quotation], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_quotation(
        self, payload: SalQuotationCreate, user: SysUser, *, commit: bool = True
    ) -> SalQuotationOut:
        """新建销售报价（落库即草稿）：自动取号 QT+yyyyMMdd+4 位，金额后端重算。"""
        try:
            await self._assert_customer_enabled(payload.customer_id)
            amounts = await self._load_items(payload.items)
            # 取号与单号查重共用事务锁，避免并发生成重复单号（PG advisory lock）
            await self._repo.lock_for_write()
            quote_no = await generate_bill_no(self._db, self.QUOTATION_BIZ_KEY)
            total = sum((amount for _, amount in amounts), Decimal("0"))
            quotation = SalQuotation(
                quote_no=quote_no,
                customer_id=payload.customer_id,
                quote_date=payload.quote_date,
                valid_until=payload.valid_until,
                status=SalQuotationStatus.DRAFT,
                total_amount=total,
                tax_amount=Decimal("0"),  # 明细无税率字段，V1 报价不拆税
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(quotation)
            await self._replace_items(quotation.id, payload.items, amounts, user.id)
            await self._write_log(user.id, "新增销售报价", quotation.id, quotation.quote_no)
            created = await self._repo.get_with_items(quotation.id)
            if created is None:  # 防御：插入后立刻回查失败说明事务异常
                raise AppException(ErrorCode.SERVER_ERROR, "销售报价创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_quotation(
        self,
        quotation_id: int,
        payload: SalQuotationUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> SalQuotationOut:
        """修改草稿：仅 status=0 可改，已发送/已失效一律 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            quotation = await self._get_visible(quotation_id, ctx, for_update=True)
            if quotation.status != SalQuotationStatus.DRAFT:
                status_text = STATUS_TEXTS.get(quotation.status, str(quotation.status))
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"报价单当前为「{status_text}」，只有草稿状态可以修改",
                )
            data = payload.model_dump(exclude_unset=True)
            if "customer_id" in data:
                customer_id = data["customer_id"]
                if customer_id is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "客户不能为 null")
                await self._assert_customer_enabled(customer_id)
                quotation.customer_id = customer_id
            if "quote_date" in data:
                if data["quote_date"] is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "报价日期不能为 null")
                quotation.quote_date = data["quote_date"]
            if "valid_until" in data:
                quotation.valid_until = data["valid_until"]
            # 有效期组合校验用更新后的最终值（Schema 只能拦截两个字段同时传入的场景）
            if (
                quotation.valid_until is not None
                and quotation.valid_until < quotation.quote_date
            ):
                raise AppException(ErrorCode.VALIDATION_ERROR, "报价有效期不能早于报价日期")
            if "remark" in data:
                # 备注允许显式传 null 表示清空
                quotation.remark = data["remark"]
            if payload.items is not None:
                # 明细整单替换：旧行软删保留流水，新行插入（ERP 要求留痕，不物理删历史）
                amounts = await self._load_items(payload.items)
                await self._replace_items(quotation.id, payload.items, amounts, user.id)
                quotation.total_amount = sum(
                    (amount for _, amount in amounts), Decimal("0")
                )
            quotation.updated_by = user.id
            # 乐观锁：条件 UPDATE 抢占版本，前端回传的 version 不一致直接 409
            await bump_version(
                self._db,
                SalQuotation,
                quotation,
                tenant_id=self._tenant_id,
                expected=payload.version,
                user_id=user.id,
            )
            await self._write_log(user.id, "修改销售报价", quotation.id, quotation.quote_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_items(quotation.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售报价不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_quotation(
        self, quotation_id: int, user: SysUser, *, commit: bool = True
    ) -> None:
        """删除草稿：仅 status=0 可删，软删主表并同步软删明细，历史保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            quotation = await self._get_visible(quotation_id, ctx, for_update=True)
            if quotation.status != SalQuotationStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的报价单可以删除")
            quotation.is_deleted = True
            quotation.updated_by = user.id
            await self._repo.soft_delete_items(quotation.id, user.id)
            await self._write_log(user.id, "删除销售报价", quotation.id, quotation.quote_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 发送 / 作废 ----------

    async def send_quotation(
        self, quotation_id: int, user: SysUser, *, commit: bool = True
    ) -> SalQuotationOut:
        """发送报价：草稿 0 → 已发送 1；已过有效期（valid_until < 今天）拒绝并发送提示。

        发送前复核明细行数与商品启用（草稿期间商品可能被停用）；
        报价发送只推进状态，不写库存、不写应收（数据闭环明确要求）。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            quotation = await self._get_visible(quotation_id, ctx, for_update=True)
            if quotation.status != SalQuotationStatus.DRAFT:
                status_text = STATUS_TEXTS.get(quotation.status, str(quotation.status))
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"报价单当前为「{status_text}」，只有草稿状态可以发送",
                )
            items_map = await self._repo.list_items([int(quotation.id)])
            active_items = [
                item
                for item in items_map.get(int(quotation.id), [])
                if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "报价单没有明细行，不能发送")
            await self._assert_items_sendable(active_items)
            if quotation.valid_until is not None and quotation.valid_until < date.today():
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"报价已过有效期（{quotation.valid_until}），不能发送",
                )
            quotation.status = SalQuotationStatus.SENT
            quotation.updated_by = user.id
            await bump_version(
                self._db,
                SalQuotation,
                quotation,
                tenant_id=self._tenant_id,
                expected=None,
                user_id=user.id,
            )
            await self._write_log(user.id, "发送销售报价", quotation.id, quotation.quote_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_items(quotation.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售报价不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def void_quotation(
        self, quotation_id: int, user: SysUser, *, commit: bool = True
    ) -> SalQuotationOut:
        """作废报价：草稿/已发送 → 已失效（终态）；已失效重复作废返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            quotation = await self._get_visible(quotation_id, ctx, for_update=True)
            if quotation.status == SalQuotationStatus.INVALID:
                raise AppException(ErrorCode.CONFLICT, "报价单已失效，不能重复作废")
            quotation.status = SalQuotationStatus.INVALID
            quotation.updated_by = user.id
            await bump_version(
                self._db,
                SalQuotation,
                quotation,
                tenant_id=self._tenant_id,
                expected=None,
                user_id=user.id,
            )
            await self._write_log(user.id, "作废销售报价", quotation.id, quotation.quote_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_items(quotation.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售报价不存在")
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
        self, quotation_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> SalQuotation:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。

        for_update=True 时对主表加行锁，发送/作废/修改/删除都走这里。
        sal_quotation 无 dept_id 列，越权判断只按创建人（数据范围 2 覆盖同部门创建人）。
        """
        quotation = await self._repo.get_with_items(quotation_id, for_update=for_update)
        if quotation is None or quotation.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售报价不存在")
        if not await allows(self._db, ctx, created_by=quotation.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售报价")
        return quotation

    async def _assert_customer_enabled(self, customer_id: int) -> None:
        """校验客户存在、未删除且启用；停用客户不允许出现在新报价里。"""
        customer = await self._db.scalar(
            select(BdCustomer).where(
                BdCustomer.id == customer_id,
                BdCustomer.tenant_id == self._tenant_id,
                BdCustomer.is_deleted.is_(False),
            )
        )
        if customer is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, f"客户不存在: {customer_id}")
        if customer.status != 1:
            raise AppException(
                ErrorCode.VALIDATION_ERROR, f"客户「{customer.name}」已停用，不能报价"
            )

    async def _load_items(
        self, items: list[SalQuotationItemIn]
    ) -> list[tuple[SalQuotationItemIn, Decimal]]:
        """批量校验商品并重算行金额，返回 (入参, 行金额) 列表。

        规则：商品必须存在、未删除、启用；金额 = qty * price 后按 0.01 精度
        银行家舍入（.trae 固定规则 §2，禁止 ROUND_HALF_UP）。
        """
        goods_ids = {item.goods_id for item in items}
        stmt = select(BdGoods).where(
            BdGoods.id.in_(goods_ids),
            BdGoods.tenant_id == self._tenant_id,
            BdGoods.is_deleted.is_(False),
        )
        goods_map = {int(row.id): row for row in (await self._db.execute(stmt)).scalars().all()}
        amounts: list[tuple[SalQuotationItemIn, Decimal]] = []
        for item in items:
            goods = goods_map.get(item.goods_id)
            if goods is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {item.goods_id}")
            if goods.status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{goods.name}」已停用，不能报价"
                )
            if item.qty <= 0:  # Schema 已拦截，双保险防止绕过入口写入非法数量
                raise AppException(ErrorCode.VALIDATION_ERROR, "报价数量必须大于 0")
            amounts.append(
                (item, (item.qty * item.price).quantize(Decimal("0.01")))
            )
        return amounts

    async def _assert_items_sendable(self, items: list[SalQuotationItem]) -> None:
        """发送前复核明细：数量 > 0、商品仍启用（草稿期间商品可能被停用）。"""
        goods_ids = {int(row.goods_id) for row in items}
        stmt = select(BdGoods.id, BdGoods.name, BdGoods.status).where(
            BdGoods.id.in_(goods_ids),
            BdGoods.tenant_id == self._tenant_id,
            BdGoods.is_deleted.is_(False),
        )
        rows = (await self._db.execute(stmt)).all()
        status_map = {int(row[0]): (str(row[1]), int(row[2])) for row in rows}
        for row in items:
            goods_id = int(row.goods_id)
            if goods_id not in status_map:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {goods_id}")
            name, status = status_map[goods_id]
            if status != 1:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品「{name}」已停用，请删除该行后重新发送"
                )
            if Decimal(row.qty) <= 0:
                raise AppException(ErrorCode.VALIDATION_ERROR, "报价数量必须大于 0")

    async def _replace_items(
        self,
        quotation_id: int,
        items: list[SalQuotationItemIn],
        amounts: list[tuple[SalQuotationItemIn, Decimal]],
        operator_id: int,
    ) -> None:
        """明细整单替换：先软删旧行（保留历史），再按重算金额批量插入新行。"""
        await self._repo.soft_delete_items(quotation_id, operator_id)
        amount_map = {item.goods_id: amount for item, amount in amounts}
        for item in items:
            self._db.add(
                SalQuotationItem(
                    quotation_id=quotation_id,
                    goods_id=item.goods_id,
                    qty=item.qty,
                    price=item.price,
                    amount=amount_map[item.goods_id],
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _to_out_list(
        self, rows: list[SalQuotation], *, with_items: bool
    ) -> list[SalQuotationOut]:
        """批量转换出参：客户名称、明细行数、商品信息一次查完，避免 N+1。

        with_items=True 时额外批量查明细与商品单位；False 时只做行数统计（列表页）。
        """
        if not rows:
            return []
        quotation_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(quotation_ids)
        customer_ids = {row.customer_id for row in rows}

        customer_names: dict[int, str] = {}
        if customer_ids:
            customer_rows = (
                await self._db.execute(
                    select(BdCustomer.id, BdCustomer.name).where(
                        BdCustomer.id.in_(customer_ids),
                        BdCustomer.tenant_id == self._tenant_id,
                    )
                )
            ).all()
            customer_names = {int(row[0]): str(row[1]) for row in customer_rows}

        # 行数直接由已批量查回的明细统计（排除软删行），无需再发 count 查询
        goods_map, unit_names = (
            await self._load_goods_info(items_map) if with_items else ({}, {})
        )

        result: list[SalQuotationOut] = []
        for row in rows:
            row_items = items_map.get(int(row.id), [])
            active_items = [item for item in row_items if not item.is_deleted]
            item_outs = (
                self._build_item_outs(active_items, goods_map, unit_names)
                if with_items
                else []
            )
            result.append(
                SalQuotationOut.model_validate(row).model_copy(
                    update={
                        "customer_name": customer_names.get(row.customer_id),
                        "items_count": len(active_items),
                        "items": item_outs,
                    }
                )
            )
        return result

    @staticmethod
    def _build_item_outs(
        items: list[SalQuotationItem],
        goods_map: dict[int, BdGoods],
        unit_names: dict[int, str],
    ) -> list[SalQuotationItemOut]:
        """把明细行转换成出参（只处理未软删行）；商品编码/名称/规格/单位来自批量查询结果。"""
        result: list[SalQuotationItemOut] = []
        for item in items:
            goods = goods_map.get(int(item.goods_id))
            unit_name = None
            if goods is not None and goods.unit_id is not None:
                unit_name = unit_names.get(int(goods.unit_id))
            result.append(
                SalQuotationItemOut(
                    id=item.id,
                    goods_id=item.goods_id,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    spec=goods.spec if goods else None,
                    unit_name=unit_name,
                    qty=item.qty,
                    price=item.price,
                    amount=item.amount,
                )
            )
        return result

    async def _load_goods_info(
        self, items_map: dict[int, list[SalQuotationItem]]
    ) -> tuple[dict[int, BdGoods], dict[int, str]]:
        """一次性加载明细涉及的商品与单位名称（详情/打印/复制预填用），避免逐行查商品。"""
        goods_ids = {
            int(item.goods_id)
            for items in items_map.values()
            for item in items
            if not item.is_deleted
        }
        if not goods_ids:
            return {}, {}
        goods_rows = (
            await self._db.execute(
                select(BdGoods).where(
                    BdGoods.id.in_(goods_ids), BdGoods.tenant_id == self._tenant_id
                )
            )
        ).scalars()
        goods_map = {int(goods.id): goods for goods in goods_rows.all()}
        unit_ids = {int(goods.unit_id) for goods in goods_map.values() if goods.unit_id}
        unit_names: dict[int, str] = {}
        if unit_ids:
            unit_rows = (
                await self._db.execute(
                    select(BdUnit.id, BdUnit.name).where(
                        BdUnit.id.in_(unit_ids), BdUnit.tenant_id == self._tenant_id
                    )
                )
            ).all()
            unit_names = {int(row[0]): str(row[1]) for row in unit_rows}
        return goods_map, unit_names

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        quotation_id: int,
        quote_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。

        发送/作废链路逐条记录 action，供验收「操作日志完整」与后续追溯对账使用。
        """
        params: dict[str, Any] = {"quotationId": quotation_id, "quoteNo": quote_no}
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
