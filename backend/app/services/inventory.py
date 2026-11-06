"""库存统一服务：写入（锁定/释放/变更）+ 只读查询（库存/流水/汇总）。

写路径的核心约束：
  - 所有库存增减必须经过本服务，同事务写 inv_stock 与 inv_stock_flow；
  - inv_stock 行使用 SELECT ... FOR UPDATE 行锁，负库存一律禁止；
  - 入库（direction=1）时按移动加权平均重算 avg_cost，出库直接结转出库前均价。

读路径仅做参数业务校验 + 组装，SQL 全部在 InventoryRepository 内完成，
一次 join 取回关联档案，避免 N+1。
"""

from decimal import Decimal

from sqlalchemy import select

from app.core.exceptions import AppException, ErrorCode
from app.models.base_data import BdGoods, BdWarehouse
from app.repositories.inventory import InventoryRepository
from app.schemas.common import PageResult
from app.schemas.inventory import (
    ChangeStockIn,
    ChangeStockOut,
    FlowListItem,
    FlowQuery,
    LockStockIn,
    StockListItem,
    StockOut,
    StockQuery,
    StockSummary,
    UnlockStockIn,
)
from app.utils.numbers import validate_quantity
from app.utils.pagination import paginate_result


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
        """锁定库存；无记录或可用量不足时不创建空库存行。

        保证 qty >= locked_qty 约束：先检查 available_qty >= payload.qty，
        再增加 locked_qty，最后重算 available_qty = qty - locked_qty。
        """
        try:
            await self._validate_references(payload.warehouse_id, payload.goods_id, payload.qty)
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if stock is None or stock.available_qty < payload.qty:
                raise AppException(ErrorCode.VALIDATION_ERROR, "可用库存不足，无法锁定")
            stock.locked_qty += payload.qty
            stock.available_qty = stock.qty - stock.locked_qty
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
        """释放历史锁定，允许已停用档案；已删除、跨租户档案仍拒绝。

        保证 qty >= locked_qty 约束：先检查 locked_qty >= payload.qty，
        再减少 locked_qty，最后重算 available_qty = qty - locked_qty。
        """
        try:
            await self._validate_references(
                payload.warehouse_id, payload.goods_id, payload.qty, allow_disabled=True
            )
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if stock is None:
                raise AppException(ErrorCode.NOT_FOUND, "库存记录不存在")
            if stock.locked_qty < payload.qty:
                raise AppException(ErrorCode.VALIDATION_ERROR, "锁定数量不足，无法释放")
            stock.locked_qty -= payload.qty
            stock.available_qty = stock.qty - stock.locked_qty
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
        """普通库存变更拒绝停用档案；数量校验通过后才允许创建库存。

        保证 qty >= locked_qty 约束：出库时先检查 available_qty >= payload.qty，
        再修改 qty，最后重算 available_qty = qty - locked_qty。
        """
        try:
            await self._validate_references(payload.warehouse_id, payload.goods_id, payload.qty)
            stock = await self._repo.get_stock_for_update(payload.warehouse_id, payload.goods_id)
            if payload.direction == -1 and (stock is None or stock.available_qty < payload.qty):
                raise AppException(ErrorCode.VALIDATION_ERROR, "可用库存不足，无法出库")
            if stock is None:
                stock = await self._repo.create_stock(payload.warehouse_id, payload.goods_id)
            before_qty = stock.qty
            outbound_cost = stock.avg_cost
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
                before_qty=before_qty,
                after_qty=stock.qty,
                unit_cost=payload.unit_cost if payload.direction == 1 else outbound_cost,
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

    # ---------- 只读查询 ----------

    async def list_stocks(self, query: StockQuery) -> PageResult[StockListItem]:
        """库存列表（分页）。

        口径说明：
          - 只返回 inv_stock 中真实存在的行；从未发生过库存变动的“仓库+商品”组合不会出现
            （库存行由首次入库时惰性创建，这是既有设计，不在此改造）；
          - 已软删的商品、仓库、库存行都不会出现在结果中。
        """
        await self._validate_stock_filter(query)
        rows, total = await self._repo.list_stocks(query)
        items = [StockListItem.model_validate(dict(row._mapping)) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def stock_summary(self, query: StockQuery) -> StockSummary:
        """库存汇总：与列表共用同一套过滤条件，保证底部合计与列表一致。"""
        await self._validate_stock_filter(query)
        return StockSummary.model_validate(await self._repo.stock_summary(query))

    async def list_flows(self, query: FlowQuery) -> PageResult[FlowListItem]:
        """库存流水列表（分页，只增不删的留痕数据）。"""
        rows, total = await self._repo.list_flows(query)
        items = [FlowListItem.model_validate(dict(row._mapping)) for row in rows]
        return paginate_result(items, total, query.page, query.page_size)

    async def _validate_stock_filter(self, query: StockQuery) -> None:
        """库存查询的档案校验：传了仓库/商品 ID 就必须在当前租户内存在且未删除。

        作用：把“查了一个不存在的仓库”从“静默返回空列表”变成明确的 422，
        避免前端把越权或错误 ID 误判为“该仓库没有库存”。

        风险点：这里**必须用不加锁的查询**。若复用 get_warehouse_for_update /
        get_goods_for_update（带 SELECT ... FOR UPDATE），一次普通列表查询就会持有
        档案行锁直到连接归还，可能阻塞入库/出库等写事务，属于严重的锁竞争问题。
        """
        if query.warehouse_id is not None:
            exists = await self._db.scalar(
                select(BdWarehouse.id).where(
                    BdWarehouse.id == query.warehouse_id,
                    BdWarehouse.tenant_id == self.tenant_id,
                    BdWarehouse.is_deleted.is_(False),
                )
            )
            if exists is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, "仓库不存在或不可访问")
        if query.goods_id is not None:
            exists = await self._db.scalar(
                select(BdGoods.id).where(
                    BdGoods.id == query.goods_id,
                    BdGoods.tenant_id == self.tenant_id,
                    BdGoods.is_deleted.is_(False),
                )
            )
            if exists is None:
                raise AppException(ErrorCode.VALIDATION_ERROR, "商品不存在或不可访问")


