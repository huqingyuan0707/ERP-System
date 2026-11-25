"""销售退货业务逻辑（SA-04）：按原出库单退货、回补库存、冲减应收。

数据闭环：
  原出库单（已确认）→ 按原单号取可退明细（可退 = 原出库 - 已确认退货）
  → 新建退货草稿 → 确认（同一事务）：
    锁原出库单串行化 → 校验累计可退量 + 应收余额 → change_stock(sale_return, +1) 回补库存
    → 冲减原应收 amount / unreceived_amount（不建红字）→ 提交。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdCustomer, BdGoods, BdUnit, BdWarehouse
from app.models.finance import FinReceivable
from app.models.inventory import InvStockFlow
from app.models.sal import SalOutbound, SalOutboundItem, SalReturn, SalReturnItem
from app.models.sys import SysUser
from app.repositories.sal_return import SalReturnRepository
from app.schemas.common import PageResult
from app.schemas.inventory import ChangeStockIn
from app.schemas.sal_return import (
    SalReturnCreate,
    SalReturnOut,
    SalReturnQuery,
    SalReturnSourceItem,
    SalReturnSourceOut,
    SalReturnStatus,
    SalReturnUpdate,
)
from app.schemas.sal_outbound import SalOutboundStatus
from app.services.audit import write_oper_log
from app.services.inventory import InventoryService
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result

CENT = Decimal("0.01")
ZERO = Decimal("0")


class SalReturnService:
    """销售退货：查询 / 原单可退明细 / 草稿 CRUD / 确认事务。"""

    RETURN_BIZ_KEY = "sal_return"
    OPER_MODULE = "销售管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = SalReturnRepository(db, tenant_id=tenant_id)
        self._inv_service = InventoryService(db, tenant_id)

    # ---------- 原出库单可退明细（只读） ----------

    async def source_outbound(self, source_no: str, user: SysUser) -> SalReturnSourceOut:
        """按原出库单号给出可退明细；不存在的单号 404，未确认不可退。"""
        outbound = await self._get_source(source_no, for_update=False)
        if int(outbound.status) != SalOutboundStatus.CONFIRMED:
            raise AppException(ErrorCode.CONFLICT, "原出库单尚未确认，不能退货")

        items_map = await self._outbound_items([int(outbound.id)])
        rows = [item for item in items_map.get(int(outbound.id), []) if not item.is_deleted]
        returned = await self._returned_qty(source_no)
        goods_ids = {int(item.goods_id) for item in rows}
        goods_map = await self._load_goods(goods_ids)
        unit_ids = {int(item.goods_id) for item in rows}
        unit_names = await self._load_goods_unit_names(unit_ids, goods_map)

        source_items: list[SalReturnSourceItem] = []
        for item in rows:
            gid = int(item.goods_id)
            out_qty = Decimal(str(item.qty))
            done = returned.get(gid, ZERO)
            remaining = out_qty - done
            if remaining <= 0:
                continue
            goods = goods_map.get(gid)
            source_items.append(
                SalReturnSourceItem(
                    goods_id=gid,
                    goods_code=goods.code if goods else None,
                    goods_name=goods.name if goods else None,
                    unit_name=unit_names.get(gid),
                    outbound_qty=out_qty,
                    returned_qty=done,
                    returnable_qty=remaining,
                    price=item.price,
                )
            )
        customer_name = await self._db.scalar(
            select(BdCustomer.name).where(
                BdCustomer.id == outbound.customer_id,
                BdCustomer.tenant_id == self._tenant_id,
            )
        )
        warehouse_name = await self._db.scalar(
            select(BdWarehouse.name).where(
                BdWarehouse.id == outbound.warehouse_id,
                BdWarehouse.tenant_id == self._tenant_id,
            )
        )
        return SalReturnSourceOut(
            outbound_id=int(outbound.id),
            outbound_no=str(outbound.outbound_no),
            customer_id=int(outbound.customer_id),
            customer_name=customer_name,
            warehouse_id=int(outbound.warehouse_id),
            warehouse_name=warehouse_name,
            outbound_date=outbound.outbound_date,
            total_amount=outbound.total_amount,
            items=source_items,
        )

    # ---------- 查询 ----------

    async def list_returns(self, query: SalReturnQuery, user: SysUser) -> PageResult[SalReturnOut]:
        ctx = await resolve_scope(self._db, user)
        stmt = await apply_scope(self._db, self._repo.build_query(query), SalReturn, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        return paginate_result(
            await self._to_out_list(rows, with_items=False), total, query.page, query.page_size
        )

    async def get_return(self, return_id: int, user: SysUser) -> SalReturnOut:
        ctx = await resolve_scope(self._db, user)
        ret = await self._get_visible(return_id, ctx, for_update=False)
        return (await self._to_out_list([ret], with_items=True))[0]

    # ---------- 草稿 CRUD ----------

    async def create_return(
        self, payload: SalReturnCreate, user: SysUser, *, commit: bool = True
    ) -> SalReturnOut:
        try:
            source = await self._get_source(payload.source_no, for_update=False)
            if int(source.status) != SalOutboundStatus.CONFIRMED:
                raise AppException(ErrorCode.CONFLICT, "原出库单尚未确认，不能退货")
            await self._assert_warehouse(payload.warehouse_id)
            lines = await self._resolve_lines(source, payload.items)
            await self._repo.lock_for_write()
            return_no = await generate_bill_no(self._db, self.RETURN_BIZ_KEY)
            total = sum((amount for _, _, amount in lines), ZERO).quantize(CENT)
            ret = SalReturn(
                return_no=return_no,
                customer_id=int(source.customer_id),
                warehouse_id=payload.warehouse_id,
                source_no=payload.source_no,
                return_date=payload.return_date,
                status=SalReturnStatus.DRAFT,
                total_amount=total,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(ret)
            await self._replace_items(ret.id, lines, user.id)
            await self._write_log(user.id, "新增销售退货", ret.id, ret.return_no)
            created = await self._repo.get_with_lock(ret.id)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "销售退货单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_return(
        self, return_id: int, payload: SalReturnUpdate, user: SysUser, *, commit: bool = True
    ) -> SalReturnOut:
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != SalReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售退货单可以修改")
            data = payload.model_dump(exclude_unset=True)
            lines = None
            if data.get("warehouse_id") is not None:
                await self._assert_warehouse(data["warehouse_id"])
            if payload.items is not None:
                source = await self._get_source(ret.source_no, for_update=False)
                lines = await self._resolve_lines(source, payload.items)
            await self._bump_version(ret, payload.version, user.id)
            if data.get("warehouse_id") is not None:
                ret.warehouse_id = data["warehouse_id"]
            if data.get("return_date") is not None:
                ret.return_date = data["return_date"]
            if "remark" in data:
                ret.remark = data["remark"]
            if lines is not None:
                await self._replace_items(ret.id, lines, user.id)
                ret.total_amount = sum((amount for _, _, amount in lines), ZERO).quantize(CENT)
            ret.updated_by = user.id
            await self._write_log(user.id, "修改销售退货", ret.id, ret.return_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(ret.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售退货单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_return(self, return_id: int, user: SysUser, *, commit: bool = True) -> None:
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != SalReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的销售退货单可以删除")
            await self._bump_version(ret, None, user.id)
            ret.is_deleted = True
            ret.updated_by = user.id
            await self._repo.soft_delete_items(ret.id, user.id)
            await self._write_log(user.id, "删除销售退货", ret.id, ret.return_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 确认事务 ----------

    async def confirm_return(
        self, return_id: int, user: SysUser, *, commit: bool = True
    ) -> SalReturnOut:
        """确认退货：回补库存 + 冲减原应收，同一事务；累计可退量校验串行化。"""
        try:
            ctx = await resolve_scope(self._db, user)
            ret = await self._get_visible(return_id, ctx, for_update=True)
            if ret.status != SalReturnStatus.DRAFT:
                raise AppException(ErrorCode.CONFLICT, "该销售退货单已确认，不能重复确认")
            await self._bump_version(ret, None, user.id)

            # 锁原出库单：同一 source_no 的并发确认串行化
            source = await self._get_source(ret.source_no, for_update=True)
            if int(source.status) != SalOutboundStatus.CONFIRMED:
                raise AppException(ErrorCode.CONFLICT, "原出库单尚未确认，不能退货")
            if int(source.customer_id) != int(ret.customer_id):
                raise AppException(ErrorCode.VALIDATION_ERROR, "退货客户必须与原出库单一致")

            items_map = await self._repo.list_items([int(ret.id)])
            lines = [item for item in items_map.get(int(ret.id), []) if not item.is_deleted]
            if not lines:
                raise AppException(ErrorCode.VALIDATION_ERROR, "退货单没有明细行，不能确认")

            # 累计可退量按「原单号 + 商品」校验（含本次）
            out_items_map = await self._outbound_items([int(source.id)])
            out_items = [
                item for item in out_items_map.get(int(source.id), []) if not item.is_deleted
            ]
            out_by_goods = {int(item.goods_id): item for item in out_items}
            returned = await self._returned_qty(ret.source_no)
            for line in lines:
                gid = int(line.goods_id)
                src = out_by_goods.get(gid)
                if src is None:
                    raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不属于原出库单: {gid}")
                out_qty = Decimal(str(src.qty))
                done = returned.get(gid, ZERO)
                if Decimal(str(line.qty)) > out_qty - done:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"退货数量 {line.qty} 超过可退数量 {out_qty - done}（商品 ID {gid}）",
                    )

            # 锁原应收并校验余额（amount 非负由 CHECK 兜底）
            receivable = await self._get_receivable_for_update(ret.source_no)
            if receivable is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, "原出库单尚未生成应收，不能退货")
            total_amount = Decimal(str(ret.total_amount))
            if total_amount > Decimal(str(receivable.amount)):
                raise AppException(ErrorCode.CONFLICT, "退货金额超过原应收金额")

            # 回补库存（sale_return，direction=+1）
            biz_time = datetime.combine(ret.return_date, datetime.now().time())
            for line in lines:
                await self._inv_service.change_stock(
                    ChangeStockIn(
                        warehouse_id=int(ret.warehouse_id),
                        goods_id=int(line.goods_id),
                        direction=1,
                        qty=Decimal(str(line.qty)),
                        unit_cost=Decimal(str(line.price)),
                        biz_type="sale_return",
                        source_no=ret.return_no,
                        biz_time=biz_time,
                        remark=f"销售退货回补 {ret.return_no}",
                    ),
                    commit=False,
                )

            # 冲减原应收（不建红字）
            await self._reduce_receivable(receivable, total_amount)
            ret.status = SalReturnStatus.CONFIRMED
            ret.updated_by = user.id
            await self._write_log(
                user.id,
                "确认销售退货",
                ret.id,
                ret.return_no,
                extra={"sourceNo": ret.source_no, "amount": str(total_amount)},
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(ret.id)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "销售退货单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 内部辅助 ----------

    async def _get_source(self, source_no: str, *, for_update: bool) -> SalOutbound:
        stmt = select(SalOutbound).where(
            SalOutbound.outbound_no == source_no,
            SalOutbound.tenant_id == self._tenant_id,
            SalOutbound.is_deleted.is_(False),
        )
        if for_update:
            stmt = stmt.with_for_update()
        outbound = (
            await self._db.execute(stmt.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if outbound is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, f"原出库单不存在: {source_no}")
        return outbound

    async def _get_visible(
        self, return_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> SalReturn:
        ret = await self._repo.get_with_lock(return_id, for_update=for_update)
        if ret is None or ret.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "销售退货单不存在")
        if not await allows(self._db, ctx, created_by=ret.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该销售退货单")
        return ret

    async def _bump_version(self, ret: SalReturn, expected: int | None, user_id: int) -> None:
        current = int(ret.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(SalReturn)
            .where(
                SalReturn.id == ret.id,
                SalReturn.tenant_id == self._tenant_id,
                SalReturn.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _assert_warehouse(self, warehouse_id: int) -> None:
        wid = await self._db.scalar(
            select(BdWarehouse.id).where(
                BdWarehouse.id == warehouse_id,
                BdWarehouse.tenant_id == self._tenant_id,
                BdWarehouse.is_deleted.is_(False),
                BdWarehouse.status == 1,
            )
        )
        if wid is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "退货仓库不存在或已停用")

    async def _outbound_items(self, outbound_ids: list[int]) -> dict[int, list[SalOutboundItem]]:
        if not outbound_ids:
            return {}
        stmt = (
            select(SalOutboundItem)
            .select_from(SalOutboundItem)
            .where(SalOutboundItem.outbound_id.in_(outbound_ids))
        )
        if self._tenant_id is not None:
            stmt = stmt.where(SalOutboundItem.tenant_id == self._tenant_id)
        result: dict[int, list[SalOutboundItem]] = {}
        for row in (await self._db.execute(stmt)).scalars().all():
            result.setdefault(int(row.outbound_id), []).append(row)
        return result

    async def _returned_qty(self, source_no: str) -> dict[int, Decimal]:
        """按「原单号 + 商品」汇总已确认退货数量。"""
        rows = (
            await self._db.execute(
                select(SalReturnItem.goods_id, func.sum(SalReturnItem.qty))
                .select_from(SalReturnItem)
                .join(SalReturn, SalReturn.id == SalReturnItem.return_id)
                .where(
                    SalReturn.source_no == source_no,
                    SalReturn.status == SalReturnStatus.CONFIRMED,
                    SalReturn.is_deleted.is_(False),
                    SalReturnItem.is_deleted.is_(False),
                )
                .group_by(SalReturnItem.goods_id)
            )
        ).all()
        return {int(row[0]): Decimal(str(row[1])) for row in rows}

    async def _get_receivable_for_update(self, source_no: str) -> FinReceivable | None:
        stmt = (
            select(FinReceivable)
            .where(
                FinReceivable.source_no == source_no,
                FinReceivable.source_type == "sales_outbound",
                FinReceivable.tenant_id == self._tenant_id,
                FinReceivable.is_deleted.is_(False),
            )
            .with_for_update()
        )
        return (
            await self._db.execute(stmt.execution_options(populate_existing=True))
        ).scalar_one_or_none()

    async def _reduce_receivable(self, receivable: FinReceivable, amount: Decimal) -> None:
        """冲减应收：amount 与 unreceived 同步减少；已收不变，重算状态。"""
        receivable.amount = Decimal(str(receivable.amount)) - amount
        receivable.unreceived_amount = Decimal(str(receivable.unreceived_amount)) - amount
        paid = Decimal(str(receivable.received_amount))
        unpaid = Decimal(str(receivable.unreceived_amount))
        if unpaid <= 0:
            receivable.status = 2
        elif paid > 0:
            receivable.status = 1
        else:
            receivable.status = 0
        await self._db.flush()

    async def _resolve_lines(
        self, source: SalOutbound, payload_items: list[Any]
    ) -> list[tuple[Any, SalOutboundItem, Decimal]]:
        """按原出库明细解析单价，并校验可退数量。返回 (入参行, 原出库明细, 行金额)。"""
        items_map = await self._outbound_items([int(source.id)])
        rows = [item for item in items_map.get(int(source.id), []) if not item.is_deleted]
        by_goods = {int(item.goods_id): item for item in rows}
        returned = await self._returned_qty(str(source.outbound_no))
        resolved: list[tuple[Any, SalOutboundItem, Decimal]] = []
        for row in payload_items:
            src = by_goods.get(int(row.goods_id))
            if src is None:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR, f"商品不属于原出库单: {row.goods_id}"
                )
            out_qty = Decimal(str(src.qty))
            done = returned.get(int(row.goods_id), ZERO)
            if row.qty > out_qty - done:
                raise AppException(
                    ErrorCode.CONFLICT,
                    f"退货数量 {row.qty} 超过可退数量 {out_qty - done}（商品 ID {row.goods_id}）",
                )
            amount = (Decimal(str(row.qty)) * Decimal(str(src.price))).quantize(CENT)
            resolved.append((row, src, amount))
        return resolved

    async def _replace_items(
        self, return_id: int, lines: list[tuple[Any, SalOutboundItem, Decimal]], user_id: int
    ) -> None:
        await self._repo.soft_delete_items(return_id, user_id)
        for row, src, amount in lines:
            self._db.add(
                SalReturnItem(
                    return_id=return_id,
                    goods_id=int(row.goods_id),
                    qty=Decimal(str(row.qty)),
                    price=Decimal(str(src.price)),
                    amount=amount,
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
        await self._db.flush()

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

    async def _load_goods_unit_names(
        self, goods_ids: set[int], goods_map: dict[int, BdGoods]
    ) -> dict[int, str]:
        unit_ids = {
            int(goods_map[gid].unit_id)
            for gid in goods_ids
            if gid in goods_map and goods_map[gid].unit_id
        }
        if not unit_ids:
            return {}
        rows = (
            await self._db.execute(
                select(BdUnit.id, BdUnit.name).where(
                    BdUnit.id.in_(unit_ids), BdUnit.tenant_id == self._tenant_id
                )
            )
        ).all()
        unit_names = {int(r[0]): str(r[1]) for r in rows}
        return {
            gid: unit_names.get(int(goods_map[gid].unit_id))
            for gid in goods_ids
            if gid in goods_map and goods_map[gid].unit_id is not None
        }

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        return_id: int,
        return_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
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

    async def _to_out_list(self, rows: list[SalReturn], *, with_items: bool) -> list[SalReturnOut]:
        if not rows:
            return []
        ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(ids) if with_items else {}
        customer_ids = {int(row.customer_id) for row in rows}
        warehouse_ids = {int(row.warehouse_id) for row in rows}
        goods_ids: set[int] = set()
        for items in items_map.values():
            for item in items:
                if not item.is_deleted:
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

        result_out: list[SalReturnOut] = []
        for row in rows:
            from app.schemas.sal_return import SalReturnItemOut

            item_outs: list[SalReturnItemOut] = []
            if with_items:
                for item in items_map.get(int(row.id), []):
                    if item.is_deleted:
                        continue
                    goods = goods_map.get(int(item.goods_id))
                    item_outs.append(
                        SalReturnItemOut(
                            id=item.id,
                            goods_id=item.goods_id,
                            goods_code=goods.code if goods else None,
                            goods_name=goods.name if goods else None,
                            qty=item.qty,
                            price=item.price,
                            amount=item.amount,
                        )
                    )
            result_out.append(
                SalReturnOut(
                    id=row.id,
                    return_no=row.return_no,
                    customer_id=row.customer_id,
                    customer_name=names.get("customer", {}).get(int(row.customer_id)),
                    warehouse_id=row.warehouse_id,
                    warehouse_name=names.get("warehouse", {}).get(int(row.warehouse_id)),
                    source_no=row.source_no,
                    return_date=row.return_date,
                    status=row.status,
                    total_amount=row.total_amount,
                    remark=row.remark,
                    version=row.version,
                    items=item_outs,
                )
            )
        return result_out
