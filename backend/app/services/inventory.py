"""库存写入统一服务：租户引用、状态、数量精度、库存与流水同事务。"""

from decimal import Decimal

from app.core.exceptions import AppException, ErrorCode
from app.repositories.inventory import InventoryRepository
from app.schemas.inventory import (
    ChangeStockIn,
    ChangeStockOut,
    LockStockIn,
    StockOut,
    UnlockStockIn,
)
from app.utils.numbers import validate_quantity


class InventoryService:
    """默认独立提交；单据/幂等包装器使用 commit=False 管理外层事务。"""

    def __init__(self, db, tenant_id: int) -> None:
        self._db = db
        self.tenant_id = tenant_id
        self._repo = InventoryRepository(db, tenant_id=tenant_id)

    async def _validate_references(
        self, warehouse_id: int, goods_id: int, qty: Decimal, *, allow_disabled: bool = False
    ) -> None:
        """先校验并锁住档案再触碰库存；停用仅允许释放历史锁定。"""
        warehouse = await self._repo.get_warehouse_for_update(warehouse_id)
        if warehouse is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "仓库不存在或不可访问")
        goods = await self._repo.get_goods_for_update(goods_id)
        if goods is None:
            raise AppException(ErrorCode.VALIDATION_ERROR, "商品不存在或不可访问")
        precision = 4
        if goods.unit_id is not None:
            unit = await self._repo.get_unit_for_update(goods.unit_id)
            if unit is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, "商品单位不存在或不可访问")
            precision = unit.precision
        if not allow_disabled and (warehouse.status != 1 or goods.status != 1):
            raise AppException(ErrorCode.VALIDATION_ERROR, "停用商品或仓库不允许新增库存写入")
        validate_quantity(qty, precision)
        if qty <= 0:
            raise AppException(ErrorCode.VALIDATION_ERROR, "库存数量必须大于 0")

    async def lock_stock(self, payload: LockStockIn, *, commit: bool = True) -> StockOut:
        """锁定库存；无记录或可用量不足时不创建空库存行。"""
        try:
            await self._validate_references(payload.warehouse_id, payload.goods_id, payload.qty)
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if stock is None or stock.available_qty < payload.qty:
                raise AppException(ErrorCode.VALIDATION_ERROR, "可用库存不足，无法锁定")
            before_available = stock.available_qty
            stock.locked_qty += payload.qty
            stock.available_qty = stock.qty - stock.locked_qty
            await self._repo.add_flow(
                warehouse_id=payload.warehouse_id,
                goods_id=payload.goods_id,
                flow_type="lock",
                direction=0,
                qty=payload.qty,
                before_qty=before_available,
                after_qty=stock.available_qty,
                unit_cost=stock.avg_cost,
                source_type=payload.source_type,
                source_no=payload.source_no,
                biz_time=payload.biz_time,
                remark=payload.remark or "库存锁定",
            )
            await self._db.flush()
            result = StockOut.model_validate(stock)
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def unlock_stock(self, payload: UnlockStockIn, *, commit: bool = True) -> StockOut:
        """释放历史锁定，允许已停用档案；已删除、跨租户档案仍拒绝。"""
        try:
            await self._validate_references(
                payload.warehouse_id, payload.goods_id, payload.qty, allow_disabled=True
            )
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if stock is None:
                raise AppException(ErrorCode.NOT_FOUND, "库存记录不存在")
            if stock.locked_qty < payload.qty:
                raise AppException(ErrorCode.VALIDATION_ERROR, "锁定数量不足，无法释放")
            before_available = stock.available_qty
            stock.locked_qty -= payload.qty
            stock.available_qty = stock.qty - stock.locked_qty
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
            await self._db.flush()
            result = StockOut.model_validate(stock)
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise

    async def change_stock(self, payload: ChangeStockIn, *, commit: bool = True) -> ChangeStockOut:
        """普通库存变更拒绝停用档案；数量校验通过后才允许创建库存。"""
        try:
            await self._validate_references(payload.warehouse_id, payload.goods_id, payload.qty)
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if payload.direction == -1 and (stock is None or stock.available_qty < payload.qty):
                raise AppException(ErrorCode.VALIDATION_ERROR, "可用库存不足，无法出库")
            if stock is None:
                stock = await self._repo.create_stock(payload.warehouse_id, payload.goods_id)
            before_qty = stock.qty
            before_available = stock.available_qty
            stock.qty += payload.qty * payload.direction
            stock.available_qty = stock.qty - stock.locked_qty
            if payload.direction == 1:
                total_cost = before_qty * stock.avg_cost + payload.qty * payload.unit_cost
                stock.avg_cost = total_cost / stock.qty if stock.qty > 0 else Decimal("0")
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
            await self._db.flush()
            result = ChangeStockOut(
                warehouse_id=stock.warehouse_id,
                goods_id=stock.goods_id,
                qty=stock.qty,
                locked_qty=stock.locked_qty,
                available_qty=stock.available_qty,
                avg_cost=stock.avg_cost,
            )
            if commit:
                await self._db.commit()
            return result
        except Exception:
            if commit:
                await self._db.rollback()
            raise
