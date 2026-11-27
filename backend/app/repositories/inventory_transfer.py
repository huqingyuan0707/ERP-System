"""库存调拨数据访问层。"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base_data import BdGoods, BdWarehouse
from app.models.inventory import InvTransfer, InvTransferItem
from app.repositories.base import BaseRepository
from app.schemas.inventory_transfer import TransferQuery


class InventoryTransferRepository(BaseRepository[InvTransfer]):
    """库存调拨数据访问（带行锁查询与只读查询）。"""

    model = InvTransfer

    def __init__(self, db: AsyncSession, tenant_id: int) -> None:
        super().__init__(db, tenant_id=tenant_id)
        self._db = db

    async def get_with_lock(self, transfer_id: int, *, for_update: bool) -> InvTransfer | None:
        """按 ID 查询调拨单并可选行锁，带租户与软删过滤。"""
        stmt = select(InvTransfer).where(
            InvTransfer.id == transfer_id,
            InvTransfer.tenant_id == self.tenant_id,
            InvTransfer.is_deleted.is_(False),
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self._db.scalar(stmt)

    async def lock_for_write(self) -> None:
        """将调拨单写操作串行化，防止并发生成重复单号。

        对调拨主表加表级锁（SELECT ... FOR UPDATE 取第一行），确保
        取号与单号查重在同一事务内串行执行。
        """
        await self._db.execute(
            select(InvTransfer.id)
            .where(InvTransfer.tenant_id == self.tenant_id)
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_items(self, transfer_ids: list[int]) -> dict[int, list[InvTransferItem]]:
        """批量查询明细，按 transfer_id 建立索引。"""
        if not transfer_ids:
            return {}
        stmt = (
            select(InvTransferItem)
            .where(
                InvTransferItem.transfer_id.in_(transfer_ids),
                InvTransferItem.tenant_id == self.tenant_id,
                InvTransferItem.is_deleted.is_(False),
            )
            .order_by(InvTransferItem.id)
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        result: dict[int, list[InvTransferItem]] = {}
        for row in rows:
            result.setdefault(int(row.transfer_id), []).append(row)
        return result

    async def soft_delete_items(self, transfer_id: int, operator_id: int) -> None:
        """软删明细行。"""
        from sqlalchemy import update

        await self._db.execute(
            update(InvTransferItem)
            .where(
                InvTransferItem.transfer_id == transfer_id,
                InvTransferItem.tenant_id == self.tenant_id,
                InvTransferItem.is_deleted.is_(False),
            )
            .values(is_deleted=True, updated_by=operator_id)
            .execution_options(synchronize_session=False)
        )
        await self._db.flush()

    def build_query(self, query: TransferQuery) -> Select:
        """构建调拨单列表查询：一次 join 带出仓库名称，避免 N+1。"""
        stmt = (
            select(
                InvTransfer.id,
                InvTransfer.transfer_no,
                InvTransfer.from_warehouse_id,
                InvTransfer.to_warehouse_id,
                InvTransfer.transfer_date,
                InvTransfer.status,
                InvTransfer.remark,
                InvTransfer.created_by,
                InvTransfer.created_at,
                InvTransfer.version,
                BdWarehouse.name.label("from_warehouse_name"),
                BdWarehouse.name.label("to_warehouse_name"),
            )
            .join(
                BdWarehouse,
                (InvTransfer.from_warehouse_id == BdWarehouse.id)
                & (BdWarehouse.tenant_id == self.tenant_id)
                & (BdWarehouse.is_deleted.is_(False)),
                isouter=False,
            )
            .where(
                InvTransfer.tenant_id == self.tenant_id,
                InvTransfer.is_deleted.is_(False),
            )
        )

        if query.warehouse_id is not None:
            stmt = stmt.where(
                or_(
                    InvTransfer.from_warehouse_id == query.warehouse_id,
                    InvTransfer.to_warehouse_id == query.warehouse_id,
                )
            )
        if query.goods_id is not None:
            from app.models.inventory import InvTransferItem

            stmt = stmt.where(
                InvTransfer.id.in_(
                    select(InvTransferItem.transfer_id).where(
                        InvTransferItem.goods_id == query.goods_id,
                        InvTransferItem.tenant_id == self.tenant_id,
                        InvTransferItem.is_deleted.is_(False),
                    )
                )
            )
        if query.status is not None:
            stmt = stmt.where(InvTransfer.status == query.status)
        if query.start_date is not None:
            stmt = stmt.where(InvTransfer.transfer_date >= query.start_date)
        if query.end_date is not None:
            stmt = stmt.where(InvTransfer.transfer_date <= query.end_date)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(InvTransfer.transfer_no.ilike(like))

        return stmt.order_by(InvTransfer.created_at.desc())

    async def paginate(self, stmt: Select, page: int, page_size: int) -> tuple[list[Any], int]:
        """分页查询（自动叠加租户过滤）。"""
        return await self.paginate_rows(stmt, page, page_size)
