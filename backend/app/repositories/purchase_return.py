"""采购退货数据访问（P-04）：取号锁、单号查重、累计已退汇总、原应付定位、列表筛选。

约定（与 PurOrderRepository 保持一致）：
  - 继承 BaseRepository，租户过滤统一走 _apply_tenant_filter，分页用 paginate；
  - 不抛业务异常、不自行提交事务（事务边界在 Service）；
  - PurReturn 未定义 relationship，明细用 list_items 批量 IN 查询，避免 N+1。
"""

from decimal import Decimal

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import FinPayable
from app.models.pur import PurInbound, PurReturn, PurReturnItem
from app.repositories.base import BaseRepository
from app.schemas.purchase_return import PurReturnQuery


def _pg_only(db: AsyncSession) -> bool:
    """当前连接是否为 PostgreSQL（advisory 锁仅 PG 支持，SQLite 测试库跳过）。"""
    return db.get_bind().dialect.name == "postgresql"


class PurReturnRepository(BaseRepository[PurReturn]):
    """封装 pur_return / pur_return_item 读写与冲减口径查询。"""

    model = PurReturn

    def __init__(self, db: AsyncSession, tenant_id: int | None = None) -> None:
        super().__init__(db, tenant_id=tenant_id)

    async def lock_for_write(self) -> None:
        """取号与单号查重共用一把事务级 advisory 锁，避免并发生成重复单号。

        PostgreSQL 用 pg_advisory_xact_lock 串行化；SQLite（测试库）无此函数直接跳过，
        由 uq_pur_return_no 唯一索引兜底。
        """
        if _pg_only(self.db):
            await self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext("pur_return"))))

    async def get_with_lock(self, return_id: int, *, for_update: bool = False) -> PurReturn | None:
        """按主键取退货单；for_update=True 时加行锁，用于草稿修改/删除/确认的状态判断。

        populate_existing=True 强制从库中取最新值，避免会话缓存导致读到旧状态。
        """
        stmt = (
            select(PurReturn)
            .where(PurReturn.id == return_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        stmt = self._apply_tenant_filter(stmt)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_items(self, return_ids: list[int]) -> dict[int, list[PurReturnItem]]:
        """按退货单 ID 批量查明细（含已软删行，供内部状态判断），一次 IN 查询避免 N+1。"""
        if not return_ids:
            return {}
        # 显式 select_from：TenantMixin 会反向引用 pur_return，不指定 FROM 会触发笛卡尔积告警
        stmt = (
            select(PurReturnItem)
            .select_from(PurReturnItem)
            .where(PurReturnItem.return_id.in_(return_ids))
            .order_by(PurReturnItem.id)
        )
        if self.tenant_id is not None:
            # 明细表自带 tenant_id，直接过滤；不用 _apply_tenant_filter 以免引入主表
            stmt = stmt.where(PurReturnItem.tenant_id == self.tenant_id)
        grouped: dict[int, list[PurReturnItem]] = {}
        for item in (await self.db.execute(stmt)).scalars().all():
            grouped.setdefault(int(item.return_id), []).append(item)
        return grouped

    async def get_source_inbound(self, source_no: str) -> PurInbound | None:
        """按原入库单号取单（含软删口径与唯一索引一致，供退货校验定位原单）。

        注意：这里查的是 pur_inbound，不能用 _apply_tenant_filter（它按本类
        model=pur_return 过滤租户，会造成笛卡尔积），必须显式加 PurInbound.tenant_id。
        """
        stmt = select(PurInbound).where(
            PurInbound.inbound_no == source_no,
            PurInbound.tenant_id == self.tenant_id,
        )
        stmt = stmt.execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_source_inbound_for_update(self, source_no: str) -> PurInbound | None:
        """按原入库单号取单并加行锁：确认事务用它串行化同一原单的并发退货确认。

        同一 source_no 的所有确认都要先拿这把锁，保证「累计可退量校验 + 应付冲减」
        在同一原单维度串行执行，不受锁获取顺序差异影响。
        """
        stmt = (
            select(PurInbound)
            .where(
                PurInbound.inbound_no == source_no,
                PurInbound.tenant_id == self.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_payable_for_update(self, source_no: str) -> FinPayable | None:
        """按原单号定位应付并加行锁（source_type=purchase_inbound），供冲减前校验与扣减。

        幂等唯一索引 uq_payable_source 保证同单只有一条未删应付；行锁防止两张退货单
        并发确认时读到同一 amount 各自冲减（丢失更新）。
        查询主表是 fin_payable，租户条件显式写在 WHERE（理由同上）。
        """
        stmt = (
            select(FinPayable)
            .where(
                FinPayable.source_type == "purchase_inbound",
                FinPayable.source_no == source_no,
                FinPayable.tenant_id == self.tenant_id,
                FinPayable.is_deleted.is_(False),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def sum_returned_qty(
        self, source_no: str, goods_ids: set[int] | None = None
    ) -> dict[int, Decimal]:
        """按商品汇总「原单号 + 商品」的已确认退货数量（G10 累计退货量口径）。

        只统计主表 status=1（已确认）且未软删的退货单的未删明细；
        草稿不占用可退量，确认时以本汇总为准做最终校验。
        """
        stmt = (
            select(PurReturnItem.goods_id, func.coalesce(func.sum(PurReturnItem.qty), 0))
            .select_from(PurReturnItem)
            .join(PurReturn, PurReturnItem.return_id == PurReturn.id)
            .where(
                PurReturn.source_no == source_no,
                PurReturn.status == 1,
                PurReturn.is_deleted.is_(False),
                PurReturnItem.is_deleted.is_(False),
            )
            .group_by(PurReturnItem.goods_id)
        )
        if self.tenant_id is not None:
            stmt = stmt.where(PurReturn.tenant_id == self.tenant_id)
        if goods_ids:
            stmt = stmt.where(PurReturnItem.goods_id.in_(goods_ids))
        rows = (await self.db.execute(stmt)).all()
        return {int(row[0]): Decimal(str(row[1])) for row in rows}

    def build_query(self, query: PurReturnQuery) -> Select:
        """组合状态/供应商/仓库/原单号/日期区间/关键字筛选；租户过滤由分页方法追加。"""
        stmt = select(PurReturn).where(PurReturn.is_deleted.is_(False))
        if query.status is not None:
            stmt = stmt.where(PurReturn.status == query.status)
        if query.supplier_id is not None:
            stmt = stmt.where(PurReturn.supplier_id == query.supplier_id)
        if query.warehouse_id is not None:
            stmt = stmt.where(PurReturn.warehouse_id == query.warehouse_id)
        if query.source_no:
            stmt = stmt.where(PurReturn.source_no == query.source_no)
        if query.return_date_start is not None:
            stmt = stmt.where(PurReturn.return_date >= query.return_date_start)
        if query.return_date_end is not None:
            stmt = stmt.where(PurReturn.return_date <= query.return_date_end)
        if query.keyword:
            like = f"%{query.keyword}%"
            stmt = stmt.where(
                or_(PurReturn.return_no.ilike(like), PurReturn.source_no.ilike(like),
                    PurReturn.remark.ilike(like))
            )
        # 单号倒序：最新退货单排在最前
        return stmt.order_by(PurReturn.return_no.desc(), PurReturn.id.desc())

    async def soft_delete_items(self, return_id: int, operator_id: int) -> None:
        """明细整单替换前的软删：UPDATE is_deleted，保留历史行便于追溯（不改历史数据）。"""
        await self.db.execute(
            update(PurReturnItem)
            .where(PurReturnItem.return_id == return_id, PurReturnItem.is_deleted.is_(False))
            .values(is_deleted=True, updated_by=operator_id)
        )
