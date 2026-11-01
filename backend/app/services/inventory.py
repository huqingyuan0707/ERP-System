"""库存服务：INV-05 锁定/释放、INV-06 库存变更统一服务。

本服务是 ERP 库存模块的事务核心，所有入库/出库/退货/调拨/盘点
都必须通过 change_stock 在单个事务内完成，保证库存、流水、
单据状态四处数据一致。
"""

from decimal import Decimal

from app.core.exceptions import AppException, ErrorCode
from app.models.inventory import InvStock
from app.repositories.inventory import InventoryRepository
from app.schemas.inventory import (
    ChangeStockIn,
    ChangeStockOut,
    LockStockIn,
    StockOut,
    UnlockStockIn,
)


class InventoryService:
    """库存业务逻辑。"""

    def __init__(self, db, tenant_id: int) -> None:
        self._db = db
        self.tenant_id = tenant_id
        self._repo = InventoryRepository(db, tenant_id=tenant_id)

    # ------------------------------------------------------------------
    # INV-05：库存锁定 / 释放（供订单审核/关闭调用）
    # ------------------------------------------------------------------

    async def lock_stock(self, payload: LockStockIn) -> StockOut:
        """锁定库存：available_qty -= qty, locked_qty += qty。
        
        可用量不足时拒绝，避免超卖。
        """
        stock = await self._repo.get_stock_for_update(
            payload.warehouse_id, payload.goods_id
        )
        if stock is None:
            stock = await self._repo.create_stock(
                payload.warehouse_id, payload.goods_id
            )

        if stock.available_qty < payload.qty:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"可用库存不足：商品 {payload.goods_id} 可用 {stock.available_qty}，请求 {payload.qty}",
            )

        before_available = stock.available_qty
        stock.locked_qty += payload.qty
        stock.available_qty = stock.qty - stock.locked_qty
        await self._db.flush()

        await self._repo.add_flow(
            warehouse_id=payload.warehouse_id,
            goods_id=payload.goods_id,
            flow_type="lock",
            direction=0,  # 锁定不改变 qty，只改变 locked
            qty=payload.qty,
            before_qty=before_available,
            after_qty=stock.available_qty,
            unit_cost=stock.avg_cost,
            source_type=payload.source_type,
            source_no=payload.source_no,
            biz_time=payload.biz_time,
            remark=payload.remark or "库存锁定",
        )

        await self._db.commit()
        return StockOut(
            id=stock.id,
            warehouse_id=stock.warehouse_id,
            goods_id=stock.goods_id,
            qty=stock.qty,
            locked_qty=stock.locked_qty,
            available_qty=stock.available_qty,
            avg_cost=stock.avg_cost,
        )

    async def unlock_stock(self, payload: UnlockStockIn) -> StockOut:
        """释放库存：locked_qty -= qty, available_qty += qty。"""
        stock = await self._repo.get_stock_for_update(
            payload.warehouse_id, payload.goods_id
        )
        if stock is None:
            raise AppException(ErrorCode.NOT_FOUND, "库存记录不存在")

        if stock.locked_qty < payload.qty:
            raise AppException(
                ErrorCode.VALIDATION_ERROR,
                f"锁定数量不足：已锁定 {stock.locked_qty}，请求释放 {payload.qty}",
            )

        before_available = stock.available_qty
        stock.locked_qty -= payload.qty
        stock.available_qty = stock.qty - stock.locked_qty
        await self._db.flush()

        await self._repo.add_flow(
            warehouse_id=payload.warehouse_id,
            goods_id=payload.goods_id,
            flow_type="unlock",
            direction=0,
            qty=payload.qty,
            before_qty=before_available,
            after_qty=stock.available_qty,
            unit_cost=stock.avg_cost,
            source_type=payload.source_type,
            source_no=payload.source_no,
            biz_time=payload.biz_time,
            remark=payload.remark or "库存释放",
        )

        await self._db.commit()
        return StockOut(
            id=stock.id,
            warehouse_id=stock.warehouse_id,
            goods_id=stock.goods_id,
            qty=stock.qty,
            locked_qty=stock.locked_qty,
            available_qty=stock.available_qty,
            avg_cost=stock.avg_cost,
        )

    # ------------------------------------------------------------------
    # INV-06：库存变更统一服务（所有入库/出库/退货/调拨/盘点调用）
    # ------------------------------------------------------------------

    async def change_stock(self, payload: ChangeStockIn) -> ChangeStockOut:
        """库存变更统一服务（单事务）。
        
        流程：
        1. 行锁查询/创建库存行
        2. 校验：出库时 available >= qty，不允许负库存
        3. 更新 qty / locked_qty / available_qty / avg_cost
        4. 写入 inv_stock_flow 流水（before/after 连续）
        5. 提交事务
        
        任一步骤失败则整个事务回滚。
        """
        stock = await self._repo.get_stock_for_update(
            payload.warehouse_id, payload.goods_id
        )
        if stock is None:
            stock = await self._repo.create_stock(
                payload.warehouse_id, payload.goods_id
            )

        # ---------- 校验 ----------
        if payload.direction == -1:  # 出库
            if stock.available_qty < payload.qty:
                raise AppException(
                    ErrorCode.VALIDATION_ERROR,
                    f"可用库存不足：商品 {payload.goods_id} 可用 {stock.available_qty}，请求出库 {payload.qty}",
                )

        # ---------- 更新库存 ----------
        before_qty = stock.qty
        before_available = stock.available_qty

        if payload.direction == 1:  # 入库
            stock.qty += payload.qty
        else:  # 出库
            stock.qty -= payload.qty

        stock.available_qty = stock.qty - stock.locked_qty

        # 移动加权平均成本（简化版：入库按新成本混合，出库不改 avg_cost）
        if payload.direction == 1 and payload.unit_cost > 0:
            total_cost = stock.qty * stock.avg_cost + payload.qty * payload.unit_cost
            stock.avg_cost = total_cost / stock.qty if stock.qty > 0 else Decimal("0")

        await self._db.flush()

        # ---------- 写流水 ----------
        await self._repo.add_flow(
            warehouse_id=payload.warehouse_id,
            goods_id=payload.goods_id,
            flow_type=payload.biz_type,
            direction=payload.direction,
            qty=payload.qty,
            before_qty=before_available,
            after_qty=stock.available_qty,
            unit_cost=payload.unit_cost,
            source_type=payload.biz_type,
            source_no=payload.source_no,
            biz_time=payload.biz_time,
            remark=payload.remark,
        )

        await self._db.commit()
        return ChangeStockOut(
            warehouse_id=stock.warehouse_id,
            goods_id=stock.goods_id,
            qty=stock.qty,
            locked_qty=stock.locked_qty,
            available_qty=stock.available_qty,
            avg_cost=stock.avg_cost,
        )
