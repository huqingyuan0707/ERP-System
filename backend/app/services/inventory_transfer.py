"""库存调拨业务逻辑（I-03）：草稿 CRUD、确认事务与操作日志。

数据闭环：
  调出仓、调入仓、日期、明细 → POST /inventory/transfers 生成草稿
  → 确认（同一事务）：
    校验调出/调入仓不相同、调出仓 available_qty >= 数量
    → 按 id 顺序锁两仓库存行（防死锁）
    → 调出仓 qty -= qty、available 重算
    → 调入仓 qty += qty、按调出仓 avg_cost 重算 avg_cost/available
    → 写两条流水（transfer_out dir=-1、transfer_in dir=1）
    → 状态已确认
  任一步失败整体回滚；重复确认 409。

敏感点（事务 / 锁 / 状态机）：
  1. 确认前按数据范围取单据并对主表加行锁；重复确认返回 409。
  2. 乐观锁用「WHERE version = 当前值」的条件 UPDATE + rowcount 判定（与 P-02 一致）。
  3. 库存写入统一走 InventoryService.change_stock（行锁 + 流水同事务）；
     调出/调入仓按仓库 id 顺序加锁，避免两个调拨单互相等待导致死锁。
  4. 调入仓 avg_cost 按调出仓当前 avg_cost 结转，调入仓重新加权：
     new_avg = (old_qty * old_avg + in_qty * in_cost) / (old_qty + in_qty)。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.data_scope import ScopeContext, allows, apply_scope, resolve_scope
from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdWarehouse
from app.models.inventory import InvStock, InvStockFlow, InvTransfer, InvTransferItem
from app.models.sys import SysUser
from app.repositories.inventory_transfer import InventoryTransferRepository
from app.schemas.common import PageResult
from app.schemas.inventory import ChangeStockIn
from app.schemas.inventory_transfer import (
    TransferConfirm,
    TransferCreate,
    TransferOut,
    TransferQuery,
    TransferUpdate,
)
from app.services.audit import write_oper_log
from app.services.inventory import InventoryService
from app.utils.bill_no import generate_bill_no
from app.utils.pagination import paginate_result

CENT = Decimal("0.01")
ZERO = Decimal("0")


class InventoryTransferService:
    """库存调拨：查询 / 草稿 CRUD / 确认事务。"""

    # 单号规则键：sys_config.billno.inv_transfer = "TF,4"
    TRANSFER_BIZ_KEY = "inv_transfer"
    OPER_MODULE = "库存管理"

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._repo = InventoryTransferRepository(db, tenant_id=tenant_id)
        self._inv_service = InventoryService(db, tenant_id)

    # ---------- 查询 ----------

    async def list_transfers(self, query: TransferQuery, user: SysUser) -> PageResult[TransferOut]:
        """分页查询调拨单：先叠加数据范围，再按状态/仓库/商品/日期筛选。"""
        ctx = await resolve_scope(self._db, user)
        stmt = self._repo.build_query(query)
        stmt = await apply_scope(self._db, stmt, InvTransfer, ctx)
        rows, total = await self._repo.paginate(stmt, query.page, query.page_size)
        items = await self._to_out_list(rows, with_items=False)
        return paginate_result(items, total, query.page, query.page_size)

    async def get_transfer(self, transfer_id: int, user: SysUser) -> TransferOut:
        """调拨单详情（含明细）：必须在当前用户数据范围内，否则 403；不存在 404。"""
        ctx = await resolve_scope(self._db, user)
        transfer = await self._get_visible(transfer_id, ctx, for_update=False)
        return (await self._to_out_list([transfer], with_items=True))[0]

    # ---------- 草稿新增 / 修改 / 删除 ----------

    async def create_transfer(
        self, payload: TransferCreate, user: SysUser, *, commit: bool = True
    ) -> TransferOut:
        """新建调拨单（落库即草稿）：调出/调入仓不能相同，单号自动生成。"""
        try:
            if int(payload.from_warehouse_id) == int(payload.to_warehouse_id):
                raise AppException(ErrorCode.VALIDATION_ERROR, "调出仓与调入仓不能相同")
            await self._assert_warehouse(payload.from_warehouse_id)
            await self._assert_warehouse(payload.to_warehouse_id)
            await self._assert_goods(payload.items)
            await self._repo.lock_for_write()
            transfer_no = await generate_bill_no(self._db, self.TRANSFER_BIZ_KEY)
            total_qty = sum((Decimal(str(item.qty)) for item in payload.items), ZERO)
            transfer = InvTransfer(
                transfer_no=transfer_no,
                from_warehouse_id=int(payload.from_warehouse_id),
                to_warehouse_id=int(payload.to_warehouse_id),
                transfer_date=payload.transfer_date,
                status=0,  # 草稿
                total_qty=total_qty,
                remark=payload.remark,
                created_by=user.id,
                updated_by=user.id,
            )
            await self._repo.add(transfer)
            await self._replace_items(transfer.id, payload.items, user.id)
            await self._write_log(user.id, "新增库存调拨", transfer.id, transfer.transfer_no)
            created = await self._repo.get_with_lock(transfer.id, for_update=False)
            if created is None:
                raise AppException(ErrorCode.SERVER_ERROR, "库存调拨单创建失败")
            result = (await self._to_out_list([created], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def update_transfer(
        self,
        transfer_id: int,
        payload: TransferUpdate,
        user: SysUser,
        *,
        commit: bool = True,
    ) -> TransferOut:
        """修改草稿：仅草稿可改（其余状态 409）；版本号不一致返回 409。"""
        try:
            ctx = await resolve_scope(self._db, user)
            transfer = await self._get_visible(transfer_id, ctx, for_update=True)
            if transfer.status != 0:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的调拨单可以修改")
            data = payload.model_dump(exclude_unset=True)
            if data.get("from_warehouse_id") is not None or data.get("to_warehouse_id") is not None:
                new_from = int(data.get("from_warehouse_id") or transfer.from_warehouse_id)
                new_to = int(data.get("to_warehouse_id") or transfer.to_warehouse_id)
                if new_from == new_to:
                    raise AppException(ErrorCode.VALIDATION_ERROR, "调出仓与调入仓不能相同")
                await self._assert_warehouse(new_from)
                await self._assert_warehouse(new_to)
            if payload.items is not None:
                await self._assert_goods(payload.items)
            # 先抢占版本号再动数据
            await self._bump_version(transfer, payload.version, user.id)
            if data.get("from_warehouse_id") is not None:
                transfer.from_warehouse_id = int(data["from_warehouse_id"])
            if data.get("to_warehouse_id") is not None:
                transfer.to_warehouse_id = int(data["to_warehouse_id"])
            if data.get("transfer_date") is not None:
                transfer.transfer_date = data["transfer_date"]
            if payload.items is not None:
                await self._replace_items(transfer.id, payload.items, user.id)
                transfer.total_qty = sum((Decimal(str(item.qty)) for item in payload.items), ZERO)
            if "remark" in data:
                transfer.remark = data["remark"]
            transfer.updated_by = user.id
            await self._write_log(user.id, "修改库存调拨", transfer.id, transfer.transfer_no)
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(transfer.id, for_update=False)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "库存调拨单不存在")
            result = (await self._to_out_list([refreshed], with_items=True))[0]
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def delete_transfer(
        self, transfer_id: int, user: SysUser, *, commit: bool = True
    ) -> None:
        """删除草稿：仅草稿可删，软删主表并同步软删明细，历史流水保留。"""
        try:
            ctx = await resolve_scope(self._db, user)
            transfer = await self._get_visible(transfer_id, ctx, for_update=True)
            if transfer.status != 0:
                raise AppException(ErrorCode.CONFLICT, "只有草稿状态的调拨单可以删除")
            await self._bump_version(transfer, None, user.id)
            transfer.is_deleted = True
            transfer.updated_by = user.id
            await self._repo.soft_delete_items(transfer_id, user.id)
            await self._write_log(user.id, "删除库存调拨", transfer.id, transfer.transfer_no)
            if commit:
                await self._db.commit()
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    # ---------- 确认事务 ----------

    async def confirm_transfer(
        self, transfer_id: int, user: SysUser, *, commit: bool = True
    ) -> TransferOut:
        """确认调拨：两仓库存变动 + 2 条流水，同一事务。

        事务步骤：
          1. 锁定调拨单行，校验状态为草稿（重复确认 409）并抢占版本号；
          2. 校验调出/调入仓不相同、调出仓可用量 >= 调拨数量；
          3. 按仓库 id 顺序锁两仓库存行（防死锁）；
          4. 调出仓 qty -= qty、available 重算（方向 -1）；
          5. 调入仓 qty += qty、按调出仓 avg_cost 重算 avg_cost/available（方向 +1）；
          6. 写两条流水（transfer_out、transfer_in）；
          7. 调拨单置为已确认，写操作日志。
        """
        try:
            ctx = await resolve_scope(self._db, user)
            transfer = await self._get_visible(transfer_id, ctx, for_update=True)
            if transfer.status != 0:
                raise AppException(ErrorCode.CONFLICT, "该库存调拨单已确认，不能重复确认")
            await self._bump_version(transfer, None, user.id)

            if int(transfer.from_warehouse_id) == int(transfer.to_warehouse_id):
                raise AppException(ErrorCode.VALIDATION_ERROR, "调出仓与调入仓不能相同")

            items_map = await self._repo.list_items([int(transfer.id)])
            active_items = [
                item for item in items_map.get(int(transfer.id), []) if not item.is_deleted
            ]
            if not active_items:
                raise AppException(ErrorCode.VALIDATION_ERROR, "调拨单没有明细行，不能确认")

            # 按商品聚合调拨数量（同一商品可能拆多行）
            merged_qty: dict[int, Decimal] = {}
            for line in active_items:
                gid = int(line.goods_id)
                merged_qty[gid] = merged_qty.get(gid, ZERO) + Decimal(str(line.qty))

            # 获取调出仓与调入仓商品档案（加行锁，保持与 change_stock 一致的锁顺序）
            from_wh = int(transfer.from_warehouse_id)
            to_wh = int(transfer.to_warehouse_id)
            # 按仓库 id 顺序加锁，避免死锁
            lock_order = sorted([from_wh, to_wh])
            stock_rows: dict[tuple[int, int], InvStock] = {}
            for wh_id in lock_order:
                for goods_id in merged_qty:
                    stock = await self._db.scalar(
                        select(InvStock)
                        .where(
                            InvStock.warehouse_id == wh_id,
                            InvStock.goods_id == goods_id,
                            InvStock.tenant_id == self._tenant_id,
                            InvStock.is_deleted.is_(False),
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if stock is None:
                        # 库存行不存在，视为 0
                        stock = InvStock(
                            tenant_id=self._tenant_id,
                            warehouse_id=wh_id,
                            goods_id=goods_id,
                            qty=ZERO,
                            locked_qty=ZERO,
                            available_qty=ZERO,
                            avg_cost=ZERO,
                        )
                        self._db.add(stock)
                        await self._db.flush()
                    stock_rows[(wh_id, goods_id)] = stock

            # 校验调出仓可用量（锁定库存不可调）
            for goods_id, qty in merged_qty.items():
                from_stock = stock_rows[(from_wh, goods_id)]
                available = Decimal(str(from_stock.available_qty))
                if available < qty:
                    raise AppException(
                        ErrorCode.CONFLICT,
                        f"调出仓可用库存不足：商品 ID {goods_id} 可用 {available}，"
                        f"调拨需要 {qty}",
                    )

            # 获取调出仓商品当前 avg_cost（用于调入仓重新加权）
            from_cost_map: dict[int, Decimal] = {}
            for goods_id in merged_qty:
                from_cost_map[goods_id] = Decimal(str(stock_rows[(from_wh, goods_id)].avg_cost))

            biz_time = datetime.combine(transfer.transfer_date, datetime.now().time())

            # 调出仓扣减（transfer_out，direction=-1）
            for line in active_items:
                goods_id = int(line.goods_id)
                qty = Decimal(str(line.qty))
                from_stock = stock_rows[(from_wh, goods_id)]
                before_qty = Decimal(str(from_stock.qty))
                from_stock.qty = Decimal(str(from_stock.qty)) - qty
                from_stock.available_qty = Decimal(str(from_stock.qty)) - Decimal(
                    str(from_stock.locked_qty)
                )
                self._db.add(
                    InvStockFlow(
                        tenant_id=self._tenant_id,
                        warehouse_id=from_wh,
                        goods_id=goods_id,
                        flow_type="transfer_out",
                        direction=-1,
                        qty=qty,
                        before_qty=before_qty,
                        after_qty=Decimal(str(from_stock.qty)),
                        unit_cost=Decimal(str(stock_rows[(from_wh, goods_id)].avg_cost)),
                        source_type="inv_transfer",
                        source_no=transfer.transfer_no,
                        biz_time=biz_time,
                        remark=f"库存调拨出库 {transfer.transfer_no}",
                    )
                )

            # 调入仓增加（transfer_in，direction=1，按调出仓 avg_cost 结转）
            for line in active_items:
                goods_id = int(line.goods_id)
                qty = Decimal(str(line.qty))
                to_stock = stock_rows[(to_wh, goods_id)]
                before_qty = Decimal(str(to_stock.qty))
                in_cost = Decimal(str(from_cost_map[goods_id]))
                to_stock.qty = Decimal(str(to_stock.qty)) + qty
                # 按调出仓 avg_cost 重新加权调入仓
                total_cost = before_qty * Decimal(str(to_stock.avg_cost)) + qty * in_cost
                to_stock.avg_cost = (
                    total_cost / Decimal(str(to_stock.qty))
                    if Decimal(str(to_stock.qty)) > 0
                    else ZERO
                )
                to_stock.available_qty = Decimal(str(to_stock.qty)) - Decimal(
                    str(to_stock.locked_qty)
                )
                self._db.add(
                    InvStockFlow(
                        tenant_id=self._tenant_id,
                        warehouse_id=to_wh,
                        goods_id=goods_id,
                        flow_type="transfer_in",
                        direction=1,
                        qty=qty,
                        before_qty=before_qty,
                        after_qty=Decimal(str(to_stock.qty)),
                        unit_cost=in_cost,
                        source_type="inv_transfer",
                        source_no=transfer.transfer_no,
                        biz_time=biz_time,
                        remark=f"库存调拨入库 {transfer.transfer_no}",
                    )
                )

            await self._db.flush()

            transfer.status = 1  # 已确认
            transfer.updated_by = user.id
            await self._write_log(
                user.id,
                "确认库存调拨",
                transfer.id,
                transfer.transfer_no,
                extra={
                    "fromWarehouseId": from_wh,
                    "toWarehouseId": to_wh,
                    "lines": len(active_items),
                    "qty": str(sum(merged_qty.values(), ZERO)),
                },
            )
            await self._db.flush()
            refreshed = await self._repo.get_with_lock(transfer.id, for_update=False)
            if refreshed is None:
                raise AppException(ErrorCode.NOT_FOUND, "库存调拨单不存在")
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
        self, transfer_id: int, ctx: ScopeContext, *, for_update: bool
    ) -> InvTransfer:
        """按 ID 取单据并做数据范围校验；不存在 404，越权 403。"""
        transfer = await self._repo.get_with_lock(transfer_id, for_update=for_update)
        if transfer is None or transfer.is_deleted:
            raise AppException(ErrorCode.NOT_FOUND, "库存调拨单不存在")
        if not await allows(self._db, ctx, created_by=transfer.created_by):
            raise AppException(ErrorCode.FORBIDDEN, "无权访问该库存调拨单")
        return transfer

    async def _bump_version(
        self, transfer: InvTransfer, expected: int | None, user_id: int
    ) -> None:
        """乐观锁：以条件 UPDATE 原子抢占版本号，未命中即 409。"""
        current = int(transfer.version)
        if expected is not None and expected != current:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")
        result = await self._db.execute(
            update(InvTransfer)
            .where(
                InvTransfer.id == transfer.id,
                InvTransfer.tenant_id == self._tenant_id,
                InvTransfer.version == current,
            )
            .values(version=current + 1, updated_by=user_id)
            .execution_options(synchronize_session=False)
        )
        if int(result.rowcount or 0) != 1:
            raise AppException(ErrorCode.CONFLICT, "数据已被他人修改，请刷新后重试")

    async def _assert_warehouse(self, warehouse_id: int) -> None:
        """校验仓库存在且启用。"""
        warehouse = await self._db.scalar(
            select(BdWarehouse).where(
                BdWarehouse.id == warehouse_id,
                BdWarehouse.tenant_id == self._tenant_id,
                BdWarehouse.is_deleted.is_(False),
                BdWarehouse.status == 1,
            )
        )
        if warehouse is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库不存在或已停用")

    async def _assert_goods(self, items) -> None:
        """校验明细商品存在且启用。"""
        goods_ids = {int(item.goods_id) for item in items}
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
        for item in items:
            goods_id = int(item.goods_id)
            if goods_id not in status_map:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品不存在: {goods_id}")
            name, status = status_map[goods_id]
            if status != 1:
                raise AppException(ErrorCode.VALIDATION_ERROR, f"商品「{name}」已停用，不能调拨")

    async def _replace_items(self, transfer_id: int, items, operator_id: int) -> None:
        """明细整单替换：先软删旧行，再批量插入新行。"""
        await self._repo.soft_delete_items(transfer_id, operator_id)
        for item in items:
            self._db.add(
                InvTransferItem(
                    transfer_id=transfer_id,
                    goods_id=int(item.goods_id),
                    qty=Decimal(str(item.qty)),
                    cost=ZERO,  # 确认时由 Service 按调出仓 avg_cost 重算
                    tenant_id=self._tenant_id,
                    created_by=operator_id,
                    updated_by=operator_id,
                )
            )
        await self._db.flush()

    async def _to_out_list(self, rows: list[InvTransfer], *, with_items: bool) -> list[TransferOut]:
        """批量转换出参：仓库名称与明细一次查完，避免 N+1。"""
        if not rows:
            return []
        transfer_ids = [int(row.id) for row in rows]
        items_map = await self._repo.list_items(transfer_ids) if with_items else {}
        warehouse_ids = {int(row.from_warehouse_id) for row in rows} | {
            int(row.to_warehouse_id) for row in rows
        }
        warehouse_names: dict[int, str] = {}
        if warehouse_ids:
            rows_wh = (
                await self._db.execute(
                    select(BdWarehouse.id, BdWarehouse.name).where(
                        BdWarehouse.id.in_(warehouse_ids),
                        BdWarehouse.tenant_id == self._tenant_id,
                    )
                )
            ).all()
            warehouse_names = {int(r[0]): str(r[1]) for r in rows_wh}

        goods_map: dict[int, Any] = {}
        if with_items:
            goods_ids = {
                int(item.goods_id)
                for items in items_map.values()
                for item in items
                if not item.is_deleted
            }
            if goods_ids:
                rows_goods = (
                    await self._db.execute(
                        select(BdGoods.id, BdGoods.code, BdGoods.name, BdGoods.spec).where(
                            BdGoods.id.in_(goods_ids),
                            BdGoods.tenant_id == self._tenant_id,
                        )
                    )
                ).all()
                goods_map = {int(r[0]): r for r in rows_goods}

        result: list[TransferOut] = []
        for row in rows:
            item_outs: list = []
            if with_items:
                goods_id_set = {
                    int(item.goods_id)
                    for item in items_map.get(int(row.id), [])
                    if not item.is_deleted
                }
                for item in items_map.get(int(row.id), []):
                    if item.is_deleted:
                        continue
                    goods = goods_map.get(int(item.goods_id))
                    item_outs.append(
                        {
                            "id": int(item.id),
                            "transferId": int(row.id),
                            "goodsId": int(item.goods_id),
                            "goodsCode": goods.code if goods else None,
                            "goodsName": goods.name if goods else None,
                            "goodsSpec": goods.spec if goods else None,
                            "qty": item.qty,
                            "cost": item.cost,
                        }
                    )
            result.append(
                TransferOut(
                    id=int(row.id),
                    transferNo=row.transfer_no,
                    fromWarehouseId=int(row.from_warehouse_id),
                    fromWarehouseName=warehouse_names.get(int(row.from_warehouse_id)),
                    toWarehouseId=int(row.to_warehouse_id),
                    toWarehouseName=warehouse_names.get(int(row.to_warehouse_id)),
                    transferDate=row.transfer_date,
                    status=int(row.status),
                    totalQty=Decimal(str(row.total_qty)),
                    remark=row.remark,
                    createdBy=int(row.created_by) if row.created_by else None,
                    createdAt=row.created_at,
                    version=int(row.version),
                    items=item_outs,
                )
            )
        return result

    async def _write_log(
        self,
        operator_id: int,
        action: str,
        transfer_id: int,
        transfer_no: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """写操作日志：与业务同一事务提交，保证「有单必有日志」。"""
        params: dict[str, Any] = {
            "transferId": transfer_id,
            "transferNo": transfer_no,
        }
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
